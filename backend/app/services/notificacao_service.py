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
        "ajuda": "Container do FindFace fora do ar, unhealthy, morto por falta "
                 "de memória ou reiniciando em laço.",
        "icone": "🔴",
    },
    {
        "chave": "host_sem_contato",
        "rotulo": "Servidor sem contato",
        "ajuda": "A máquina não respondeu ao coletor. Costuma ser rede ou VM "
                 "desligada — não o FindFace.",
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
]

POR_TIPO = {t["chave"]: t for t in TIPOS}
TIPOS_VALIDOS = frozenset(POR_TIPO)


def _quando(dt: datetime) -> str:
    return dt.astimezone().strftime("%d/%m %H:%M")


def _duracao(segundos: float | None) -> str:
    if not segundos or segundos < 0:
        return ""
    s = int(segundos)
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}min"
    h = m // 60
    if h < 24:
        return f"{h}h{m % 60:02d}"
    return f"{h // 24}d{h % 24}h"


def montar_mensagem(evento: dict) -> str:
    """
    Mensagem curta, legível no aviso do celular sem abrir o app.

    Quatro linhas no máximo: o que, onde, por que (quando se sabe) e desde
    quando. Mensagem longa no Telegram vira bloco cinza cortado — quem
    está de plantão precisa decidir "levanto ou não" pela prévia.
    """
    tipo = evento.get("tipo", "servico_parado")
    host = evento.get("host") or "servidor"
    servico = evento.get("servico") or ""
    alvo = servico or "máquina inteira"
    icone = (POR_TIPO.get(tipo) or {}).get("icone", "🟡")

    if tipo == "retorno":
        linhas = [f"{icone} NORMALIZADO · {host}", f"{alvo} voltou"]
        d = _duracao(evento.get("duracao_s"))
        if d:
            linhas.append(f"Ficou fora {d}")
        return "\n".join(linhas)

    if tipo == "host_sem_contato":
        linhas = [f"{icone} SEM CONTATO · {host}", "A máquina não respondeu ao coletor"]
        causa = (evento.get("causa_provavel") or "").strip()
        if causa:
            linhas.append(f"Provável: {causa.split('.')[0][:140]}")
        inicio = evento.get("inicio")
        if isinstance(inicio, datetime):
            linhas.append(f"Desde {_quando(inicio)}")
        return "\n".join(linhas)

    if tipo == "metrica":
        linhas = [f"{icone} LIMITE · {host}", evento.get("texto") or "recurso acima do limite"]
        acao = (evento.get("acao") or "").strip()
        if acao:
            linhas.append(acao.split(".")[0][:140])
        return "\n".join(linhas)

    # servico_parado
    grave = evento.get("nivel") == "critico"
    rotulo = "PARADO" if grave else "ATENÇÃO"
    linhas = [f"{icone} {rotulo} · {host}", evento.get("texto") or f"{alvo} com problema"]

    causa = (evento.get("causa_provavel") or "").strip()
    if causa:
        # Uma frase só: a causa provável completa é longa e está na tela.
        linhas.append(f"Provável: {causa.split('.')[0][:140]}")

    inicio = evento.get("inicio")
    if isinstance(inicio, datetime):
        linhas.append(f"Desde {_quando(inicio)}")
    return "\n".join(linhas)


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

                texto = montar_mensagem(evento)
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

        for destino in alvos:
            texto = (
                "✅ FaceOps conectado\n"
                f"Destino: {destino.nome}\n"
                f"Teste enviado por {usuario}"
            )
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
