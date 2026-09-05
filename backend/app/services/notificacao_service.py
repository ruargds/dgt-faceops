"""
Quem recebe o quê — e a mensagem que chega.

Desenho em duas peças, como no Zabbix (media type + action), no Grafana
(contact point + notification policy) e no Alertmanager (receiver + route):

* **destino** — para onde: um grupo do Telegram, ou uma pessoa;
* **regra** — o que mandar para lá: quais tipos de evento, de quais
  servidores/serviços, a partir de qual gravidade, com quanto de espera.

Sem essa separação, cada destino novo exigiria duplicar todas as regras.

Regra de ouro do serviço: **aviso que repete vira aviso que se ignora.**
Daí a deduplicação por evento, o teto por ciclo, a espera antes de avisar
(o `for:` do Prometheus) e a janela de repetição desligada por padrão.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.core.vault import decrypt_secret
from app.models.notificacao import (
    NotificacaoConta, NotificacaoDestino, NotificacaoEnvio, NotificacaoRegra,
)
from app.services import telegram_service

log = logging.getLogger("faceops.notificacao")

# Teto por ciclo. Se dez serviços caem juntos, a pessoa precisa saber que
# dez caíram — não receber dez mensagens no meio da madrugada.
TETO_POR_CICLO = 8

NIVEIS = {"atencao": 1, "critico": 2}

# ── Catálogo de tipos de evento ────────────────────────────────────────
# Hardcoded como o catálogo de permissões e o de configuração: tipo que só
# existe no banco vira caixa de seleção que ninguém sabe para que serve.
TIPOS: list[dict] = [
    {
        "chave": "servico_parado",
        "rotulo": "Serviço parado",
        "ajuda": "Container do Face Detect fora do ar, unhealthy, morto por falta "
                 "de memória ou reiniciando em laço.",
        "icone": "🔴",
    },
    {
        "chave": "host_sem_contato",
        "rotulo": "Servidor sem comunicação",
        "ajuda": "A máquina não respondeu ao coletor. Costuma ser rede ou VM "
                 "desligada — não o Face Detect.",
        "icone": "⛔",
    },
    {
        "chave": "retorno",
        "rotulo": "Voltou ao normal",
        "ajuda": "O que estava fora voltou. Saber disso evita alguém sair de "
                 "casa por um problema que já passou.",
        "icone": "🟢",
    },
    {
        "chave": "metrica",
        "rotulo": "Limite de recurso",
        "ajuda": "Disco, memória, swap, carga, memória de vídeo ou temperatura "
                 "da GPU acima do limiar configurado.",
        "icone": "🟡",
    },
    {
        "chave": "crescimento",
        "rotulo": "Consumo subindo sem parar",
        "ajuda": "Memória ou disco em subida contínua, com a projeção de "
                 "quando encosta no limite e quem está ocupando. Chega ANTES "
                 "do limiar — é o aviso que ainda dá tempo de usar.",
        "icone": "📈",
    },
]

POR_TIPO = {t["chave"]: t for t in TIPOS}
TIPOS_VALIDOS = frozenset(POR_TIPO)


def _quando(dt: datetime) -> str:
    """
    Com segundos, como no template do Zabbix: dois avisos no mesmo minuto
    são indistinguíveis sem eles, e a ordem importa para reconstruir o que
    aconteceu. Com a data, porque incidente atravessa o dia.
    """
    return dt.astimezone().strftime("%d/%m %H:%M:%S")


def _duracao(segundos: float | None) -> str:
    """
    "4d 18h 50m 42s", no mesmo formato do Zabbix.

    Da maior unidade não-zero até os segundos, sem omitir o meio: "12m 0s"
    diz que a medição é exata, enquanto "12m" deixa a dúvida de estar
    arredondado — e em janela de indisponibilidade essa dúvida importa.
    """
    if not segundos or segundos < 0:
        return ""
    s = int(segundos)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    partes = []
    if d:
        partes.append(f"{d}d")
    if d or h:
        partes.append(f"{h}h")
    if d or h or m:
        partes.append(f"{m}m")
    partes.append(f"{s}s")
    return " ".join(partes)


# ── A mensagem ─────────────────────────────────────────────────────────
# O desenho é o do template de Telegram do Zabbix que a equipe já lê todo
# dia — e é deliberado copiá-lo: quem está de plantão não devia ter de
# aprender dois formatos para ler o mesmo grupo. Dele vêm três decisões
# que parecem enfeite e não são:
#
# * `ícone - Rótulo: valor` — o ícone dá a varredura visual, o rótulo dá
#   o significado. Só ícone obriga a decorar a legenda; só texto obriga a
#   ler tudo.
# * **linha em branco entre campos** — no cliente de Telegram as linhas
#   ficam coladas, e sem o respiro a mensagem vira um parágrafo cinza.
# * **ícone dobrado no cabeçalho de resolvido** (`✅✅`) — é o que deixa a
#   boa notícia reconhecível na rolagem, sem ler.
#
# A PRIMEIRA linha é a assinatura, e não é enfeite nenhum: no mesmo grupo
# caem avisos do Zabbix e do FaceOps, e o caminho de resolução é outro em
# cada caso. O nome do cliente vem de `projeto.cliente` (Configurações →
# Identidade do projeto) — o mesmo campo que já nomeia o painel, para não
# haver dois lugares dizendo quem é o cliente.
MARCA = "FaceOps 🤖"

# Separador entre ícone e rótulo, como no template do Zabbix.
SEP_CAMPO = " - "

CAMPO_PROBLEMA = "⚠️"
CAMPO_SIGNIFICA = "💬"
CAMPO_PROVAVEL = "🔎"
CAMPO_FAZER = "🛠"
CAMPO_INICIO = "⏳"
CAMPO_GRAVIDADE = "⚡"
CAMPO_RESOLVIDO = "✅"
CAMPO_DURACAO = "⏱"
CAMPO_HORARIO = "🕐"
CAMPO_DESTINO = "📨"
CAMPO_AUTOR = "👤"

GRAVIDADE = {"critico": "Crítico", "atencao": "Atenção"}

# Papel do servidor em palavras, os mesmos rótulos da tela de Servidores.
PAPEL = {
    "appserver": "Aplicação",
    "dbserver": "Banco de dados",
    "extraction": "Extração / GPU",
    "ftpserver": "FTP / arquivos",
}

# Teto de segurança. O Telegram corta em 4096 caracteres, e mensagem
# cortada esconde justamente o fim — onde estão horário e gravidade.
LIMITE_CARACTERES = 3500


def _cabecalho(cliente: str) -> str:
    cliente = (cliente or "").strip()
    return f"{MARCA} · {cliente}" if cliente else MARCA


def _faixa(icone: str, host: str, papel: str = "") -> str:
    """
    Linha do servidor, com o ícone dos dois lados — como no Zabbix. O
    papel entra entre parênteses quando conhecido: "vm-dbserver" já diz
    para quem convive com os nomes, e não diz nada para quem não convive.
    """
    rotulo = PAPEL.get((papel or "").strip().lower(), "")
    alvo = f"{host} ({rotulo})" if rotulo else host
    return f"{icone} - {alvo} - {icone}"


def _campo(icone: str, rotulo: str, valor: str) -> str:
    return f"{icone}{SEP_CAMPO}{rotulo}: {valor}"


def _frase(texto: str, limite: int = 240) -> str:
    """
    Uma frase, sem cortar palavra no meio.

    A causa provável e a ação completas são longas e estão na tela; aqui
    entra o suficiente para decidir se levanta da cama.
    """
    texto = " ".join((texto or "").split())
    if not texto:
        return ""
    if len(texto) <= limite:
        return texto
    corte = texto[:limite]
    espaco = corte.rfind(" ")
    return (corte[:espaco] if espaco > 40 else corte).rstrip(" ,;.") + "…"


def montar_mensagem(evento: dict, cliente: str = "") -> str:
    """
    A mensagem que chega no Telegram.

    Campos rotulados em vez de texto corrido: quem recebe precisa achar
    "o que fazer" sem ler o resto. Cada campo é opcional — evento sem
    causa provável não ganha uma linha "Provável: —", que só ocuparia
    espaço para não dizer nada.

    Texto puro, sem Markdown: nome de container tem `_`, `-` e `.`, que
    quebram o parser do Telegram e fariam a mensagem falhar justamente
    durante um incidente. Sem endereço interno, também: IP de servidor
    não vai para um grupo de mensagens.
    """
    tipo = evento.get("tipo", "servico_parado")
    host = evento.get("host") or "servidor"
    papel = evento.get("papel") or ""
    servico = evento.get("servico") or ""
    alvo = servico or "a máquina inteira"
    icone = (POR_TIPO.get(tipo) or {}).get("icone", "🟡")

    campos: list[str] = []

    if tipo == "retorno":
        # Ícone dobrado: é assim que a boa notícia se distingue na rolagem.
        campos.append(_faixa(f"{CAMPO_RESOLVIDO}{CAMPO_RESOLVIDO}", host, papel))
        campos.append(_campo(CAMPO_RESOLVIDO, "Resolvido",
                             f"{alvo} voltou a funcionar"))
        fora = _duracao(evento.get("duracao_s"))
        if fora:
            campos.append(_campo(CAMPO_DURACAO, "Duração", fora))

        # A pergunta que o retorno sempre deixava no ar: "e o que foi?".
        # A apuração roda no fechamento, então a resposta cabe nesta
        # mesma mensagem — inclusive quando a resposta é "não achei".
        apuracao = evento.get("apuracao") or {}
        veredito = (apuracao.get("veredito") or "").strip()
        if veredito:
            campos.append(_campo(CAMPO_PROVAVEL, "Causa", _frase(veredito)))
            primeiro = (apuracao.get("achados") or [{}])[0].get("texto", "")
            if primeiro:
                campos.append(_campo(CAMPO_SIGNIFICA, "Evidência", _frase(primeiro)))

        campos.append(_campo(CAMPO_HORARIO, "Horário",
                             _quando(datetime.now(timezone.utc))))
        return _juntar(cliente, campos)

    campos.append(_faixa(icone, host, papel))

    if tipo == "host_sem_contato":
        problema = "o servidor não respondeu ao monitoramento"
    elif tipo == "metrica":
        problema = evento.get("texto") or "recurso acima do limite"
    else:
        problema = evento.get("texto") or f"{alvo} com problema"
    campos.append(_campo(CAMPO_PROBLEMA, "Problema", _frase(problema)))

    # O que isso quer dizer na prática. É a linha que faltava: quem recebe
    # o aviso não sabe o que é `findface-video-worker`, e sem ela a
    # mensagem informa sem explicar.
    significa = _frase(evento.get("significa") or "")
    if significa:
        campos.append(_campo(CAMPO_SIGNIFICA, "Significa", significa))

    provavel = _frase(evento.get("causa_provavel") or "")
    if provavel:
        campos.append(_campo(CAMPO_PROVAVEL, "Provável", provavel))

    fazer = _frase(evento.get("acao") or "")
    if fazer:
        campos.append(_campo(CAMPO_FAZER, "Fazer", fazer))

    inicio = evento.get("inicio")
    if isinstance(inicio, datetime):
        desde = _duracao(evento.get("duracao_s"))
        quanto = f" (há {desde})" if desde else ""
        campos.append(_campo(CAMPO_INICIO, "Iniciado em",
                             f"{_quando(inicio)}{quanto}"))

    gravidade = GRAVIDADE.get(evento.get("nivel") or "")
    if gravidade:
        campos.append(_campo(CAMPO_GRAVIDADE, "Gravidade", gravidade))

    return _juntar(cliente, campos)


def _juntar(cliente: str, campos: list[str]) -> str:
    """
    Assinatura no topo e linha em branco entre os campos.

    O respiro é o que separa uma mensagem legível de um parágrafo cinza no
    celular — o template do Zabbix faz igual, e é por isso que aqueles
    avisos se leem de relance.
    """
    return "\n\n".join([_cabecalho(cliente), *campos])[:LIMITE_CARACTERES]


class NotificacaoService:
    def __init__(self, config=None) -> None:
        self.config = config

    def _cfg(self, chave: str, padrao):
        if self.config is None:
            return padrao
        try:
            return self.config.get(chave)
        except (KeyError, ValueError, TypeError):
            return padrao

    # ── Conta (o bot) ──────────────────────────────────────────────────

    @staticmethod
    async def conta(db) -> NotificacaoConta | None:
        r = await db.execute(select(NotificacaoConta).limit(1))
        return r.scalars().first()

    # ── Destinos ───────────────────────────────────────────────────────

    @staticmethod
    async def destinos(db, so_ativos: bool = False) -> list[NotificacaoDestino]:
        consulta = select(NotificacaoDestino).order_by(NotificacaoDestino.nome)
        if so_ativos:
            consulta = consulta.where(NotificacaoDestino.ativo.is_(True))
        r = await db.execute(consulta)
        return list(r.scalars().all())

    # ── Decisão ────────────────────────────────────────────────────────

    @staticmethod
    def _escopo_casa(regra: NotificacaoRegra, evento: dict) -> bool:
        """A regra cobre este host/serviço?"""
        if regra.host_id is not None and regra.host_id != evento.get("host_id"):
            return False
        if regra.servico and regra.servico != (evento.get("servico") or ""):
            return False
        return True

    @staticmethod
    def na_janela(regra: NotificacaoRegra, agora: datetime | None = None) -> bool:
        """
        A regra vale NESTE momento? É o "time period" do Zabbix.

        Três casos, e o padrão é o mais permissivo para que toda regra já
        criada continue valendo 24/7:

        * `dias_semana` vazio = todos os dias;
        * início igual ao fim = o dia inteiro;
        * fim ANTES do início = janela que cruza a meia-noite
          (22:00–06:00), o turno da madrugada.

        A hora é a LOCAL do painel — é a que a pessoa usou para
        configurar, e converter para UTC na tela só criaria uma
        oportunidade a mais de errar por um fuso.
        """
        agora = agora or datetime.now()
        if agora.tzinfo is not None:
            agora = agora.astimezone().replace(tzinfo=None)

        dias = list(regra.dias_semana or [])
        if dias:
            # Python: segunda=0. Zabbix e a tela: segunda=1.
            if (agora.weekday() + 1) not in [int(d) for d in dias]:
                return False

        inicio = int(regra.hora_inicio_min or 0)
        fim = int(regra.hora_fim_min or 0)
        if inicio == fim:
            return True

        # O fim é INCLUSIVO: "até 23:59" cobre até 23:59:59, que é o que a
        # pessoa quis dizer ao escrever o último horário do dia. Com fim
        # exclusivo, `00:00–23:59` deixaria o último minuto de fora — um
        # minuto cego, e ninguém descobre um minuto cego por inspeção: só
        # pelo alerta que não chegou.
        #
        # Duas regras vizinhas (08:00–18:00 e 18:00–22:00) passam a se
        # tocar às 18:00. É de propósito: regra somam, então sobreposição
        # não causa dano, enquanto buraco causa.
        minuto = agora.hour * 60 + agora.minute
        if inicio < fim:
            return inicio <= minuto <= fim
        # Cruza a meia-noite.
        return minuto >= inicio or minuto <= fim

    @classmethod
    def rotear(
        cls,
        regras: list[NotificacaoRegra],
        destinos: list[NotificacaoDestino],
        evento: dict,
    ) -> list[NotificacaoDestino]:
        """
        Para quais destinos este evento deve ir.

        Diferente da primeira versão, que devolvia só sim/não: com destino
        separado da regra, a resposta é **a lista de quem recebe**. Lista
        vazia = ninguém, que é o padrão quando nenhuma regra cobre — o
        silêncio por omissão continua valendo.

        Uma regra mais específica não anula as outras: se alguém quer que o
        plantão receba tudo e que o dono de um serviço receba só o dele, as
        duas regras valem ao mesmo tempo, cada uma para o seu destino.
        """
        tipo = evento.get("tipo")
        nivel = NIVEIS.get(evento.get("nivel"), 1)
        idade = float(evento.get("duracao_s") or 0)

        por_id = {d.id: d for d in destinos if d.ativo}
        escolhidos: dict[int, NotificacaoDestino] = {}

        for regra in regras:
            if not regra.ativo:
                continue
            if tipo not in (regra.tipos or []):
                continue
            if not cls._escopo_casa(regra, evento):
                continue
            # Fora do dia/horário da regra ela simplesmente não vale —
            # nem para a abertura, nem para o retorno. Silêncio aqui é a
            # intenção de quem configurou: "não me acorde fora do turno".
            if not cls.na_janela(regra):
                continue
            # Gravidade não se aplica ao retorno: quem pediu para saber que
            # voltou quer saber, independente de quão grave foi a queda.
            if tipo != "retorno" and nivel < NIVEIS.get(regra.nivel_minimo, 2):
                continue
            # Espera antes de avisar (o `for:` do Prometheus). Retorno não
            # espera — a boa notícia não tem por que atrasar.
            if tipo != "retorno" and regra.atraso_s and idade < regra.atraso_s:
                continue

            if regra.destino_id is None:
                escolhidos.update(por_id)
            elif regra.destino_id in por_id:
                escolhidos[regra.destino_id] = por_id[regra.destino_id]

        return list(escolhidos.values())

    # ── Envio ──────────────────────────────────────────────────────────

    async def despachar(self, db, eventos: list[dict]) -> int:
        """
        Roteia, evita repetido e manda. Devolve quantas mensagens saíram.

        Nunca levanta: falha de notificação não pode derrubar o ciclo do
        monitor, que é quem grava a amostra e o incidente.
        """
        if not eventos:
            return 0
        try:
            conta = await self.conta(db)
            if conta is None or not conta.ativo or not conta.bot_token_enc:
                return 0

            destinos = await self.destinos(db, so_ativos=True)
            if not destinos:
                return 0

            r = await db.execute(select(NotificacaoRegra))
            regras = list(r.scalars().all())
            if not regras:
                return 0

            token = decrypt_secret(conta.bot_token_enc)
        except Exception:
            log.exception("notificação: configuração ilegível")
            return 0

        repetir_s = int(self._cfg("notificacao.repetir_apos_h", 0)) * 3600
        # Reaproveita a identidade do projeto: quem já nomeou o cliente em
        # Configurações não precisa nomear de novo aqui.
        cliente = str(self._cfg("projeto.cliente", "") or "")
        enviados = 0

        for evento in eventos:
            if enviados >= TETO_POR_CICLO:
                log.warning("teto de %d notificações no ciclo atingido", TETO_POR_CICLO)
                break

            for destino in self.rotear(regras, destinos, evento):
                if enviados >= TETO_POR_CICLO:
                    break

                # A chave carrega o destino: a mesma queda avisada para dois
                # grupos são dois envios, e cada um tem de ser rastreado.
                chave = f"{evento.get('chave') or ''}|d{destino.id}"
                if await self._ja_enviado(db, chave, repetir_s):
                    continue

                # Retorno órfão não sai. A abertura pode ter sido barrada
                # pela gravidade mínima ou pela espera da regra — e aí o
                # "resolvido" chegava sozinho, contando o fim de uma
                # história cujo começo ninguém recebeu. Zabbix e
                # Alertmanager amarram os dois pelo mesmo motivo.
                if evento.get("tipo") == "retorno" and not await self._abertura_enviada(
                    db, evento, destino.id
                ):
                    continue

                texto = montar_mensagem(evento, cliente)
                registro = NotificacaoEnvio(
                    chave=chave, texto=texto[:1000], destino=destino.nome[:120],
                )
                try:
                    await telegram_service.enviar(token, destino.chat_id, texto)
                    registro.status = "enviado"
                    enviados += 1
                except Exception as exc:
                    registro.status = "falha"
                    registro.erro = str(exc)[:300]
                    log.warning(
                        "falha ao notificar %s (%s): %s",
                        destino.nome, chave, registro.erro,
                    )
                db.add(registro)

        return enviados

    @staticmethod
    async def _abertura_enviada(db, evento: dict, destino_id: int) -> bool:
        """
        A abertura correspondente a este retorno chegou NESTE destino?

        Duas formas, porque as duas famílias de evento numeram diferente:

        * **incidente** — a chave do fim espelha a do início, então a
          conferência é por igualdade (`chave_abertura`);
        * **crescimento** — cada nível é uma chave própria (a vigilância
          pode ter subido de atenção para crítico no meio), então a
          conferência é por PREFIXO, limitada ao episódio: só vale o envio
          feito depois que a vigilância abriu.

        Evento sem nenhuma das duas pistas passa — é o comportamento
        antigo, e vale para qualquer origem que ainda não amarre o par.
        """
        exata = evento.get("chave_abertura")
        prefixo = evento.get("chave_abertura_prefixo")
        if not exata and not prefixo:
            return True

        consulta = select(NotificacaoEnvio.id).where(
            NotificacaoEnvio.status == "enviado"
        )
        if exata:
            consulta = consulta.where(
                NotificacaoEnvio.chave == f"{exata}|d{destino_id}"
            )
        else:
            consulta = consulta.where(
                NotificacaoEnvio.chave.like(f"{prefixo}%|d{destino_id}")
            )
            desde = evento.get("abertura_desde")
            if desde:
                try:
                    corte = datetime.fromisoformat(str(desde))
                    if corte.tzinfo is None:
                        corte = corte.replace(tzinfo=timezone.utc)
                    consulta = consulta.where(NotificacaoEnvio.ts >= corte)
                except ValueError:
                    pass

        r = await db.execute(consulta.limit(1))
        return r.scalar() is not None

    @staticmethod
    async def _ja_enviado(db, chave: str, repetir_s: int = 0) -> bool:
        """
        Já mandamos este evento para este destino?

        Com `repetir_s` > 0, um envio antigo deixa de contar — é o
        `repeat_interval` do Alertmanager, para problema que dura dias
        voltar a lembrar de si. Desligado por padrão: repetir sem pedido é
        o caminho mais curto para o aviso ser ignorado.
        """
        consulta = select(NotificacaoEnvio.id).where(
            NotificacaoEnvio.chave == chave,
            NotificacaoEnvio.status == "enviado",
        )
        if repetir_s > 0:
            corte = datetime.now(timezone.utc) - timedelta(seconds=repetir_s)
            consulta = consulta.where(NotificacaoEnvio.ts >= corte)
        r = await db.execute(consulta.limit(1))
        return r.scalar() is not None

    async def enviar_teste(self, db, usuario: str, destino_id: int | None = None) -> dict:
        """
        Botão 'enviar teste'. Prova o caminho inteiro: token, destino e
        permissão do bot naquele chat.
        """
        conta = await self.conta(db)
        if conta is None or not conta.bot_token_enc:
            raise ValueError("configure o bot antes de testar")

        todos = await self.destinos(db)
        alvos = [d for d in todos if destino_id is None or d.id == destino_id]
        if not alvos:
            raise ValueError("cadastre um destino antes de testar")

        token = decrypt_secret(conta.bot_token_enc)
        agora = datetime.now(timezone.utc).timestamp()
        resultados = []

        cliente = str(self._cfg("projeto.cliente", "") or "")
        tipo_destino = {"grupo": "grupo", "individual": "conversa direta"}

        for destino in alvos:
            # Mesmo cabeçalho do aviso de verdade: o teste tem de provar
            # também que a mensagem chega no formato certo, e não só que o
            # token funciona.
            texto = _juntar(cliente, [
                _faixa(f"{CAMPO_RESOLVIDO}{CAMPO_RESOLVIDO}", "Teste de envio"),
                _campo(CAMPO_RESOLVIDO, "Comunicação",
                       "o FaceOps consegue enviar para este destino"),
                _campo(CAMPO_DESTINO, "Destino",
                       f"{destino.nome} "
                       f"({tipo_destino.get(destino.tipo, destino.tipo)})"),
                _campo(CAMPO_AUTOR, "Enviado por", usuario),
                _campo(CAMPO_HORARIO, "Horário",
                       _quando(datetime.now(timezone.utc))),
            ])
            registro = NotificacaoEnvio(
                chave=f"teste:{destino.id}:{agora}", texto=texto,
                destino=destino.nome[:120],
            )
            try:
                await telegram_service.enviar(token, destino.chat_id, texto)
                registro.status = "enviado"
                resultados.append({"destino": destino.nome, "ok": True, "erro": ""})
            except Exception as exc:
                registro.status = "falha"
                registro.erro = str(exc)[:300]
                resultados.append({"destino": destino.nome, "ok": False, "erro": str(exc)[:200]})
            db.add(registro)

        return {"ok": all(r["ok"] for r in resultados), "resultados": resultados}

    # ── Consulta e faxina ──────────────────────────────────────────────

    @staticmethod
    async def ultimos(db, limite: int = 30) -> list[dict]:
        r = await db.execute(
            select(NotificacaoEnvio).order_by(NotificacaoEnvio.ts.desc()).limit(limite)
        )
        return [
            {
                "id": e.id, "ts": e.ts.isoformat(), "texto": e.texto,
                "destino": e.destino, "status": e.status, "erro": e.erro,
            }
            for e in r.scalars().all()
        ]

    @staticmethod
    async def limpar(db, dias: int) -> int:
        """
        Log de envio é operacional, não histórico. Passado o prazo, sai —
        inclusive as falhas: o que importa delas é o agora.
        """
        if dias <= 0:
            return 0
        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        r = await db.execute(delete(NotificacaoEnvio).where(NotificacaoEnvio.ts < corte))
        return r.rowcount or 0
