"""
Apuração de incidente — o que causou, apurado **quando ele fecha**.

O painel já dizia "provável" na abertura, a partir do que o Docker
contava. O que faltava era a pergunta seguinte: *e o que foi, afinal?* —
respondida no momento em que a máquina volta a atender, que é justamente
o único momento em que se pode perguntar a ela.

A pergunta mais valiosa é a mais barata de todas. Quando um servidor fica
sem comunicação, há duas explicações muito diferentes:

* **a máquina reiniciou** — e aí o problema é dela (Azure, kernel, OOM);
* **a máquina nunca reiniciou** — ficou ligada durante toda a janela, e
  então o problema foi de rede, de rota ou do caminho até ela.

Distinguir as duas custa ler o *uptime*. Um número. E é a diferença entre
abrir chamado no provedor de rede e abrir no provedor de VM.

Regras de custo, porque isto roda em produção:

* **uma única chamada SSH** por incidente apurado, na conexão que o ciclo
  do monitor já tem aberta (pool com TTL) — não há novo handshake;
* **só no fechamento**, e só para incidente que fechou nesta passada;
* **teto por passada** (`MAX_POR_CICLO`), para dez serviços voltando
  juntos não virarem dez comandos;
* **saída limitada** por `-n` no journalctl e por corte de caracteres
  antes de gravar;
* o resultado mora na **própria linha do incidente** — logo, é apagado
  pela retenção de incidentes (padrão 30 dias) sem faxina nova.

E a regra da casa acima de tudo: **não afirmar o que não se apurou.**
Quando nada é encontrado, o veredito é "não encontrei evidência", e não
uma hipótese vestida de fato.
"""
import logging
import re
import shlex
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.incidente import Incidente

log = logging.getLogger("faceops.apuracao")

SEP = "###FACEOPS:"

# Teto por passada do monitor. Dez serviços voltando ao mesmo tempo é um
# cenário real (o stack subindo); dez comandos SSH seguidos, não.
MAX_POR_CICLO = 2

# Teto do comando inteiro. Passado disso, o que interessa é o ciclo do
# monitor seguir, não a apuração terminar.
TIMEOUT_S = 30

# Corte do que é gravado. A apuração serve para apontar a causa, não para
# arquivar o log — o log inteiro continua a um clique, na tela de Logs.
#
# Dois níveis, porque as duas necessidades são reais e opostas:
#
# * **resumido** (padrão) responde "o que foi" em quatro linhas, e é o que
#   cabe no aviso do Telegram e na leitura de plantão;
# * **completo** guarda o material de uma investigação — serviços do
#   systemd que falharam, dmesg, o estado da rede no momento, mais linhas
#   de journal e de log do container.
#
# O nível completo NÃO é o padrão de propósito. Ele lê mais coisa do
# servidor, grava mais no banco e vale a pena exatamente quando alguém
# está investigando um caso — não em toda queda de todo dia, para sempre.
NIVEIS = {
    "resumido": {
        "linhas_por_fonte": 12,
        "linhas_journal": 25,
        "chars_achado": 400,
        "chars_total": 4000,
        "avancado": False,
    },
    "completo": {
        "linhas_por_fonte": 40,
        "linhas_journal": 80,
        "chars_achado": 700,
        "chars_total": 20000,
        "avancado": True,
    },
}
NIVEL_PADRAO = "resumido"

MAX_LINHAS_POR_FONTE = NIVEIS[NIVEL_PADRAO]["linhas_por_fonte"]
MAX_CHARS_ACHADO = NIVEIS[NIVEL_PADRAO]["chars_achado"]
MAX_CHARS_TOTAL = NIVEIS[NIVEL_PADRAO]["chars_total"]


def limites(nivel: str) -> dict:
    """Os tetos do nível pedido; cai no padrão para valor desconhecido."""
    return NIVEIS.get((nivel or "").strip().lower(), NIVEIS[NIVEL_PADRAO])


# Folga na janela consultada. O fim do incidente é o instante em que o
# painel viu a máquina responder; a causa aconteceu antes disso, e o
# registro do boot costuma vir alguns segundos depois.
FOLGA_ANTES_S = 120
FOLGA_DEPOIS_S = 120

# Ruído conhecido do journal que não explica nada e só ocuparia a tela.
IGNORAR = re.compile(
    r"(Failed to (?:connect to|open) (?:bus|system bus)"
    r"|Startup finished"
    r"|systemd-journald.*Data hash table"
    r"|apt-daily|motd-news|snapd.*refresh)",
    re.I,
)


def _epoch(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _secoes(saida: str) -> dict[str, str]:
    """Divide a saída pelas marcas — mesmo formato do internos_service."""
    secoes: dict[str, str] = {}
    atual = None
    buf: list[str] = []
    for linha in (saida or "").splitlines():
        if linha.startswith(SEP):
            if atual is not None:
                secoes[atual] = "\n".join(buf).strip()
            atual = linha[len(SEP):].strip()
            buf = []
        elif atual is not None:
            buf.append(linha)
    if atual is not None:
        secoes[atual] = "\n".join(buf).strip()
    return secoes


def _limpar(linhas: str, lim: dict | None = None) -> list[str]:
    """Linhas úteis, sem ruído conhecido e sem repetição."""
    lim = lim or NIVEIS[NIVEL_PADRAO]
    vistas: list[str] = []
    for linha in (linhas or "").splitlines():
        linha = linha.strip()
        if not linha or IGNORAR.search(linha):
            continue
        if linha in vistas:
            continue
        vistas.append(linha[:lim["chars_achado"]])
        if len(vistas) >= lim["linhas_por_fonte"]:
            break
    return vistas


def _comando(
    inicio: datetime, fim: datetime, container: str = "", nivel: str = NIVEL_PADRAO
) -> str:
    """
    O comando único da apuração.

    `date +%s` e `/proc/uptime` juntos dão o instante do boot **sem passar
    por fuso**: `uptime -s` imprime hora local, e comparar isso com a
    janela em UTC produziria conclusões erradas duas vezes por ano.
    """
    lim = limites(nivel)
    n = lim["linhas_journal"]
    ini = _epoch(inicio) - FOLGA_ANTES_S
    fim_e = _epoch(fim) + FOLGA_DEPOIS_S

    partes = [
        f'echo "{SEP}tempo"',
        "date +%s; cut -d. -f1 /proc/uptime 2>/dev/null",
        f'echo "{SEP}reboots"',
        "(last -x -n 6 reboot shutdown 2>/dev/null || true) | head -12",
        f'echo "{SEP}kernel"',
        f"(journalctl -k --since=@{ini} --until=@{fim_e} -p warning "
        f"--no-pager -n {n} 2>/dev/null || true) | tail -{n}",
        f'echo "{SEP}sistema"',
        f"(journalctl --since=@{ini} --until=@{fim_e} -p err "
        f"--no-pager -n {n} 2>/dev/null || true) | tail -{n}",
    ]

    if lim["avancado"]:
        # Fontes que só valem quando alguém está investigando de fato.
        # Todas de leitura, todas com teto, e nenhuma delas escreve nada
        # no servidor.
        partes += [
            f'echo "{SEP}unidades"',
            # Serviço do systemd em falha explica queda que o journal por
            # prioridade não mostra (unidade que nem chegou a subir).
            "(systemctl --failed --no-legend --no-pager 2>/dev/null || true) | head -15",
            f'echo "{SEP}dmesg"',
            # Hardware e OOM aparecem aqui antes de aparecer no journal.
            "(dmesg -T --level=err,warn 2>/dev/null || dmesg 2>/dev/null || true) "
            "| tail -25",
            f'echo "{SEP}rede"',
            # Estado das interfaces: link que caiu e voltou é a explicação
            # mais comum de 'a máquina não reiniciou e mesmo assim sumiu'.
            "(ip -br link 2>/dev/null || true) | head -10",
            f'echo "{SEP}pressao"',
            # Memória e disco no momento da volta — contexto do que
            # antecedeu, quando a causa foi esgotamento.
            "(free -m 2>/dev/null | head -3 || true); "
            "(df -h --output=pcent,target 2>/dev/null | sort -rn | head -4 || true)",
        ]
    if container:
        alvo = shlex.quote(container)
        partes += [
            f'echo "{SEP}container"',
            f"(docker inspect {alvo} --format "
            "'{{.State.ExitCode}}|{{.State.OOMKilled}}|{{.State.FinishedAt}}"
            "|{{.RestartCount}}' 2>/dev/null || true)",
            f'echo "{SEP}log"',
            f"(docker logs --tail 25 {alvo} 2>&1 || true) | tail -25",
        ]
    return "; ".join(partes)


class ApuracaoService:
    """Apura a causa de incidente que acabou de fechar."""

    def __init__(self, stack, config=None) -> None:
        self.stack = stack
        self.config = config

    def _cfg(self, chave: str, padrao):
        if self.config is None:
            return padrao
        try:
            return self.config.get(chave)
        except (KeyError, ValueError, TypeError):
            return padrao

    def habilitada(self) -> bool:
        return bool(self._cfg("apuracao.ativa", True))

    # ── Interpretação ──────────────────────────────────────────────────

    @staticmethod
    def _boot_epoch(secao: str) -> int | None:
        """
        Instante do boot, de `date +%s` e `/proc/uptime`.

        Devolve None quando a leitura não veio — e None significa
        "não sei", nunca "não reiniciou".
        """
        numeros = [n for n in (secao or "").split() if n.lstrip("-").isdigit()]
        if len(numeros) < 2:
            return None
        agora, uptime = int(numeros[0]), int(numeros[1])
        if agora <= 0 or uptime < 0:
            return None
        return agora - uptime

    @classmethod
    def interpretar(
        cls,
        secoes: dict[str, str],
        incidente_tipo: str,
        inicio: datetime,
        fim: datetime,
        nivel: str = NIVEL_PADRAO,
    ) -> dict:
        """
        Do texto bruto para um veredito.

        Separado da coleta para poder ser testado sem SSH nenhum — e é
        aqui que mora toda a decisão, então é aqui que os testes precisam
        alcançar.
        """
        lim = limites(nivel)
        achados: list[dict] = []
        boot = cls._boot_epoch(secoes.get("tempo", ""))
        ini_e = _epoch(inicio)
        fim_e = _epoch(fim)

        veredito = ""
        confianca = "nenhuma"
        reiniciou: bool | None = None

        if boot is not None:
            reiniciou = boot >= ini_e - FOLGA_ANTES_S
            quando = datetime.fromtimestamp(boot, timezone.utc)
            if reiniciou:
                veredito = "A máquina reiniciou durante a janela"
                confianca = "alta"
                achados.append({
                    "fonte": "uptime",
                    "texto": f"o sistema subiu em {quando:%d/%m %H:%M:%S} (UTC), "
                             "dentro da janela do incidente",
                })
            else:
                # A conclusão mais útil da apuração, e a mais barata.
                veredito = (
                    "A máquina NÃO reiniciou — ficou ligada durante toda a janela"
                )
                confianca = "alta"
                fora = timedelta(seconds=max(0, ini_e - boot))
                achados.append({
                    "fonte": "uptime",
                    "texto": f"o sistema já estava de pé desde "
                             f"{quando:%d/%m %H:%M:%S} (UTC), {fora.days}d antes "
                             "do incidente começar — então o servidor não caiu: "
                             "o que falhou foi o caminho até ele (rede, rota ou "
                             "o firewall).",
                })

        for chave, rotulo in (("reboots", "last"), ("kernel", "kernel"),
                              ("sistema", "journal"),
                              ("unidades", "systemd em falha"),
                              ("dmesg", "dmesg"), ("rede", "interfaces"),
                              ("pressao", "memória e disco")):
            for linha in _limpar(secoes.get(chave, ""), lim):
                achados.append({"fonte": rotulo, "texto": linha})

        # Container: o Docker é mais específico que o journal quando o
        # incidente é de serviço.
        bruto = (secoes.get("container") or "").strip()
        if bruto and "|" in bruto:
            partes = bruto.split("|")
            exit_code = partes[0].strip()
            oom = partes[1].strip().lower() == "true"
            if oom:
                veredito = "O container foi encerrado por falta de memória (OOM)"
                confianca = "alta"
                achados.insert(0, {
                    "fonte": "docker",
                    "texto": "State.OOMKilled=true — o kernel matou o processo "
                             "por falta de memória. Confira Recursos antes de "
                             "reiniciar, ou ele cai de novo.",
                })
            elif exit_code not in ("", "0"):
                if confianca != "alta":
                    veredito = f"O container saiu com código de erro {exit_code}"
                    confianca = "media"
                achados.append({
                    "fonte": "docker",
                    "texto": f"State.ExitCode={exit_code} — saída anormal; o log "
                             "abaixo é do fim da execução.",
                })

        for linha in _limpar(secoes.get("log", ""), lim):
            achados.append({"fonte": "log do container", "texto": linha})

        if not veredito:
            # A resposta honesta. Antes de existir esta função, a ausência
            # de evidência virava silêncio — e silêncio se lê como "não
            # houve nada", que é outra coisa.
            veredito = "Não encontrei evidência da causa"
            confianca = "nenhuma"
            if incidente_tipo == "host":
                achados.append({
                    "fonte": "apuração",
                    "texto": "não consegui ler o uptime nem o journal deste "
                             "servidor. Sem isso não é possível dizer se ele "
                             "reiniciou — e não vou afirmar por dedução.",
                })
            else:
                achados.append({
                    "fonte": "apuração",
                    "texto": "o container não deixou código de erro, sinal de "
                             "OOM nem linha de log no período. Costuma ser "
                             "parada manual, ou reinício do stack.",
                })

        # Corte final: o que interessa é apontar a causa, não arquivar log.
        total = 0
        cortados = []
        for a in achados:
            total += len(a["texto"])
            if total > lim["chars_total"]:
                break
            cortados.append(a)

        return {
            "veredito": veredito,
            "confianca": confianca,
            "reiniciou": reiniciou,
            "achados": cortados,
            # Quantos achados ficaram de fora do teto. Dizer isso é o que
            # separa "foi só isso" de "cortei aqui" — sem o número, o fim
            # da lista parece o fim da evidência.
            "truncado": max(0, len(achados) - len(cortados)),
            "nivel": nivel,
            "em": datetime.now(timezone.utc).isoformat(),
        }

    # ── Correlação com o que o próprio painel mediu ────────────────────

    # Janela olhada antes da queda. Meia hora cobre a subida de memória
    # que antecede um OOM sem trazer ruído de horas antes.
    JANELA_ANTES_MIN = 30

    # Salto que vale mencionar. Abaixo disso é variação normal de carga —
    # apontar 3 pontos percentuais como "causa" seria ruído com cara de
    # conclusão.
    SALTO_PCT = 10.0

    @staticmethod
    async def pressao_antes(db, host_id: int, inicio: datetime, janela_min: int = 30) -> list[dict]:
        """
        O que as amostras do painel mostram nos minutos ANTES da queda.

        Custo zero de servidor: são as amostras que o ciclo do monitor já
        gravou. É a leitura que responde "foi falta de memória?" sem
        precisar de nada do FindFace — e foi um gráfico de memória subindo
        de 78% para 94% que motivou esta função.

        Devolve achados, nunca uma afirmação de causa: memória alta antes
        da queda é correlação, e correlação apontada como causa é
        exatamente o tipo de conclusão que o painel não deve dar.
        """
        from sqlalchemy import select as _select

        from app.models.amostra import Amostra

        if inicio.tzinfo is None:
            inicio = inicio.replace(tzinfo=timezone.utc)
        desde = inicio - timedelta(minutes=max(5, janela_min))

        r = await db.execute(
            _select(Amostra)
            .where(
                Amostra.host_id == host_id,
                Amostra.ts >= desde,
                Amostra.ts <= inicio,
                Amostra.erro == "",
            )
            .order_by(Amostra.ts)
        )
        amostras = list(r.scalars().all())
        if len(amostras) < 3:
            # Duas amostras não desenham tendência. Silêncio é melhor que
            # uma "tendência" tirada de dois pontos.
            return []

        achados: list[dict] = []
        primeira, ultima = amostras[0], amostras[-1]

        for campo, rotulo, unidade in (
            ("mem_pct", "memória", "%"),
            ("swap_pct", "swap", "%"),
            ("disco_pct", "disco", "%"),
            ("gpu_mem_pct", "memória de vídeo", "%"),
        ):
            de = float(getattr(primeira, campo, 0) or 0)
            ate = float(getattr(ultima, campo, 0) or 0)
            pico = max(float(getattr(a, campo, 0) or 0) for a in amostras)
            if ate <= 0:
                continue
            subiu = ate - de
            if subiu >= ApuracaoService.SALTO_PCT or pico >= 90:
                achados.append({
                    "fonte": "amostras do painel",
                    "texto": (
                        f"{rotulo} foi de {de:.0f}{unidade} a {ate:.0f}{unidade} "
                        f"(pico {pico:.0f}{unidade}) nos {janela_min} min antes da "
                        "queda — correlação, não causa comprovada."
                    ),
                })

        carga = max(float(getattr(a, "carga_por_nucleo", 0) or 0) for a in amostras)
        if carga >= 2.0:
            achados.append({
                "fonte": "amostras do painel",
                "texto": (
                    f"carga chegou a {carga:.2f} por núcleo antes da queda — "
                    "havia processo esperando CPU."
                ),
            })

        return achados

    # ── Coleta ─────────────────────────────────────────────────────────

    def nivel(self) -> str:
        """Nível configurado. Valor estranho cai no resumido."""
        pedido = str(self._cfg("apuracao.nivel", NIVEL_PADRAO) or "")
        return pedido if pedido in NIVEIS else NIVEL_PADRAO

    async def apurar(
        self,
        host,
        incidente: Incidente,
        containers: dict | None = None,
        nivel: str | None = None,
        db=None,
    ) -> dict:
        """
        Uma chamada SSH, e o veredito. Nunca levanta: apuração que
        derruba o ciclo do monitor custaria a amostra, que vale mais.
        """
        fim = incidente.fim or datetime.now(timezone.utc)
        container = ""
        if incidente.tipo == "servico" and incidente.servico:
            # Serviço do compose não é nome de container — a tradução já
            # existe no resumo de saúde, e sem ela o `docker inspect`
            # falharia em silêncio.
            container = (containers or {}).get(incidente.servico, incidente.servico)

        nivel = nivel if nivel in NIVEIS else self.nivel()

        try:
            r = await self.stack.ssh.run(
                host,
                _comando(incidente.inicio, fim, container, nivel),
                sudo=True,
                timeout=TIMEOUT_S,
            )
            secoes = _secoes(r.stdout or "")
        except Exception as exc:
            log.warning("apuração do incidente %s falhou: %s", incidente.id, exc)
            return {
                "veredito": "Não consegui apurar",
                "confianca": "nenhuma",
                "reiniciou": None,
                "achados": [{
                    "fonte": "apuração",
                    "texto": f"a leitura no servidor falhou: {str(exc)[:200]}",
                }],
                "truncado": 0,
                "nivel": nivel,
                "em": datetime.now(timezone.utc).isoformat(),
            }

        resultado = self.interpretar(
            secoes, incidente.tipo, incidente.inicio, fim, nivel=nivel
        )

        # O que o próprio painel mediu antes da queda entra ANTES dos
        # achados do sistema: memória subindo é a pista que quem lê
        # procura primeiro, e ela não custa nada ao servidor.
        if db is not None:
            try:
                antes = await self.pressao_antes(
                    db, incidente.host_id, incidente.inicio, self.JANELA_ANTES_MIN
                )
                if antes:
                    resultado["achados"] = antes + resultado["achados"]
                    # Sem veredito do sistema mas com pressão medida, a
                    # correlação vira a melhor resposta disponível — dita
                    # como correlação, e com confiança baixa.
                    if resultado["confianca"] == "nenhuma":
                        resultado["veredito"] = (
                            "Não encontrei a causa no sistema, mas houve "
                            "pressão de recurso antes da queda"
                        )
                        resultado["confianca"] = "media"
            except Exception:
                log.exception("falha ao correlacionar amostras do incidente %s", incidente.id)

        return resultado

    async def apurar_fechados(
        self, db, host, eventos: list[dict], containers: dict | None = None
    ) -> int:
        """
        Apura os incidentes que fecharam nesta passada e **enriquece os
        eventos** com o veredito.

        Enriquecer antes do despacho é o que faz o aviso de retorno já
        chegar com a causa — em vez de uma segunda mensagem depois, que
        seria mais spam para dizer o que caberia na primeira.
        """
        if not self.habilitada():
            return 0

        retornos = [e for e in eventos if e.get("tipo") == "retorno"][:MAX_POR_CICLO]
        if not retornos:
            return 0

        feitos = 0
        for evento in retornos:
            try:
                r = await db.execute(
                    select(Incidente)
                    .where(
                        Incidente.host_id == host.id,
                        Incidente.servico == (evento.get("servico") or ""),
                        Incidente.fim.isnot(None),
                        Incidente.apuracao.is_(None),
                    )
                    .order_by(Incidente.fim.desc())
                    .limit(1)
                )
                incidente = r.scalars().first()
                if incidente is None:
                    continue

                apuracao = await self.apurar(
                    host, incidente, containers=containers, db=db
                )
                incidente.apuracao = apuracao
                incidente.apurado_em = datetime.now(timezone.utc)
                evento["apuracao"] = apuracao
                feitos += 1
            except Exception:
                log.exception("falha ao apurar retorno de %s", host.name)

        return feitos
