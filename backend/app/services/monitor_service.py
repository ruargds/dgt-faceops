"""
Monitoramento contínuo — o coletor de fundo.

O painel nasceu com coleta sob demanda, no clique do botão, justamente
para não pesar. A vigilância contínua muda essa conta, então o desenho
aqui é todo sobre não onerar:

* **Uma execução SSH por host por ciclo.** A mesma que a tela usa, com
  tudo numa passada só. Tipicamente 1 s.
* **Sequencial, não paralelo.** Quatro handshakes simultâneos dariam um
  pico desnecessário. Espaçados, o custo se dilui.
* **Só hosts marcados.** Monitorar é escolha por servidor, não padrão.
* **Amostra estreita.** ~80 bytes por linha, só número.
* **Conexão reaproveitada.** O pool do SSH mantém a sessão viva entre
  ciclos; não há handshake novo a cada minuto.
* **Cede a vez.** Se uma coleta sob demanda ou um backup estiver
  rodando naquele host, o ciclo pula — quem está esperando na tela vem
  primeiro.

**Duas necessidades, uma cadência para cada.** Este painel não fica
aberto o dia inteiro: ele é consultado de vez em quando. Mas o que ele
faz atende a dois propósitos com exigências opostas:

* **Vigiar** — detectar queda e avisar no Telegram. Precisa rodar sem
  ninguém olhando, e é a razão de existir um laço.
* **Mostrar** — desenhar os gráficos e cartões. Só importa enquanto
  alguém está com a tela aberta.

Durante muito tempo os dois foram servidos pelo mesmo ciclo de 60 s,
dimensionado para a TELA. O resultado é que o painel fechado gastava
exatamente o mesmo que o painel aberto: 5.760 idas por dia, e outras
tantas linhas no banco, para ninguém ver.

Agora o laço tem duas velocidades:

| Situação | Intervalo | Por quê |
|---|---|---|
| alguém usando o painel | `monitor.intervalo_s` (60 s) | o gráfico precisa de pontos densos |
| ninguém há mais de 10 min | `monitor.intervalo_ocioso_s` (300 s) | vigiar não precisa de 60 s: uma queda detectada em 5 min avisa igual |

A troca é imediata nos dois sentidos. Abrir o painel **acorda o laço na
hora** — não se espera o resto do intervalo longo — então a primeira tela
já vem com leitura fresca.

Ganho no modo econômico, com quatro servidores: de 5.760 para 1.152 idas
por dia (80% a menos), e a mesma redução em linhas gravadas.

O que **não** muda no modo econômico: incidente continua sendo aberto e
fechado, aviso continua saindo no Telegram, backup e faxina continuam no
horário. Economia que desliga a vigilância não é economia — é desligar o
painel.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db.database import AsyncSessionLocal
from app.models.amostra import Amostra
from app.models.host import Host
from app.services.ssh_service import SSHError

# Catálogo único do que cada serviço faz (ver internos_service).
from app.services.internos_service import descrever

log = logging.getLogger("faceops.monitor")


def _pior_disco(discos: list[dict]) -> tuple[float, str, float, float]:
    """O disco mais cheio — é o que dispara alerta."""
    if not discos:
        return 0.0, "", 0.0, 0.0
    pior = max(discos, key=lambda d: d.get("percentual", 0))
    return (
        float(pior.get("percentual", 0)),
        str(pior.get("ponto", ""))[:64],
        round(pior.get("livre_bytes", 0) / (1024 ** 3), 2),
        round(pior.get("total_bytes", 0) / (1024 ** 3), 2),
    )


def _gb(mb: float) -> str:
    """
    MB para GB legível. "16384 MB" faz a pessoa dividir de cabeça no meio
    de um incidente; "16 GB" não.
    """
    gb = (mb or 0) / 1024
    return f"{gb:.1f} GB" if gb < 10 else f"{gb:.0f} GB"


class MonitorService:
    def __init__(
        self, metrics, stack, config=None, incidentes=None, limiares=None,
        analise=None, notificacoes=None, apuracao=None,
    ) -> None:
        self.metrics = metrics
        self.stack = stack
        self.config = config
        # Opcionais de propósito: um painel sem estas peças continua
        # monitorando exatamente como antes, só sem histórico de
        # indisponibilidade, sem limite por serviço e sem análise de log.
        self.incidentes = incidentes
        self.limiares = limiares
        self.analise = analise
        self.notificacoes = notificacoes
        # Apura a causa quando o incidente FECHA — o único momento em que
        # a máquina volta a poder ser perguntada.
        self.apuracao = apuracao
        self._tarefa: asyncio.Task | None = None
        self._rodando = False
        # Último erro por host, para a tela não repetir a mesma queixa
        self._ultimo_erro: dict[int, str] = {}
        self._ciclos = 0
        self._ultimo_ciclo: datetime | None = None
        # Versão do que a tela vê. Sobe quando o ciclo termina (dado novo)
        # e quando alguém mexe em servidor ou limiar (configuração nova).
        # É a chave do cache do resumo — ver `chave_cache`.
        self._versao = 0
        # Quando alguém falou com o painel pela última vez. É o que
        # decide a velocidade do laço — ver `modo()`.
        self._ultima_atividade: datetime | None = None
        # Acorda o laço no meio de uma espera longa. Sem isto, abrir o
        # painel no modo econômico mostraria dado de até 5 min atrás e
        # ficaria assim até a espera terminar.
        self._acordar: asyncio.Event | None = None

    def _cfg(self, chave: str, padrao):
        if self.config is None:
            return padrao
        try:
            return self.config.get(chave)
        except (KeyError, ValueError, TypeError):
            return padrao

    # ── Ciclo de vida ──────────────────────────────────────────────────

    async def iniciar(self) -> None:
        if self._tarefa is not None:
            return
        self._rodando = True
        self._tarefa = asyncio.create_task(self._laco())
        log.info("monitor contínuo iniciado")

    async def parar(self) -> None:
        self._rodando = False
        if self._tarefa is not None:
            self._tarefa.cancel()
            try:
                await self._tarefa
            except asyncio.CancelledError:
                pass
            self._tarefa = None

    def estado(self) -> dict:
        return {
            "ativo": self._rodando and self._tarefa is not None,
            "ciclos": self._ciclos,
            "ultimo_ciclo": self._ultimo_ciclo.isoformat() if self._ultimo_ciclo else None,
            "intervalo_s": self.intervalo_atual(),
            "intervalo_ativo_s": int(self._cfg("monitor.intervalo_s", 60)),
            "intervalo_ocioso_s": int(self._cfg("monitor.intervalo_ocioso_s", 300)),
            "modo": self.modo(),
            # De quanto em quanto tempo a TELA deve perguntar. Não faz
            # sentido a tela buscar a cada 10 s um dado que só muda a cada
            # 5 min — e é o servidor que sabe disso, não o navegador.
            "poll_s": max(10, self.intervalo_atual() // 4),
            "erros": self._ultimo_erro,
        }

    # ── Laço ───────────────────────────────────────────────────────────

    async def _laco(self) -> None:
        # Espera antes do primeiro ciclo: na subida o painel já tem o que
        # fazer, e uma coleta a mais competiria com isso.
        await asyncio.sleep(20)

        if self._acordar is None:
            self._acordar = asyncio.Event()

        while self._rodando:
            intervalo = self.intervalo_atual()
            try:
                if bool(self._cfg("monitor.ativo", True)):
                    await self._ciclo()
            except asyncio.CancelledError:
                break
            except Exception:
                # Um ciclo que falha não pode matar o laço — o
                # monitoramento tem que sobreviver a servidor fora do ar.
                log.exception("erro no ciclo do monitor")

            try:
                # Espera interrompível: alguém abrindo o painel acorda o
                # laço em vez de esperar o resto do intervalo longo.
                try:
                    await asyncio.wait_for(self._acordar.wait(), timeout=intervalo)
                except asyncio.TimeoutError:
                    pass
                finally:
                    self._acordar.clear()
            except asyncio.CancelledError:
                break

    async def _ciclo(self) -> None:
        async with AsyncSessionLocal() as db:
            resultado = await db.execute(
                select(Host).where(Host.enabled.is_(True), Host.monitorar.is_(True))
            )
            hosts = list(resultado.scalars().all())

            if not hosts:
                return

            for host in hosts:
                if not self._rodando:
                    break
                await self._amostrar(db, host)
                # Espaça os hosts dentro do ciclo. Quatro handshakes ao
                # mesmo tempo dariam um pico que não precisa existir.
                await asyncio.sleep(1)

            # Aviso de limite de recurso: uma leitura por CICLO (não por
            # host) sobre o que `alertas()` já sabe calcular — sem
            # duplicar a lógica de limiar em dois lugares.
            if self.notificacoes is not None:
                try:
                    metricas = [
                        {
                            "tipo": "metrica",
                            "host_id": a["host_id"],
                            # Rótulo, não nome técnico: é texto lido por
                            # gente no celular.
                            "host": a.get("rotulo") or a["host"],
                            "papel": a.get("papel", ""),
                            "servico": "",
                            "nivel": a["nivel"],
                            "texto": a["texto"],
                            "significa": a.get("significa", ""),
                            "acao": a.get("acao", ""),
                            # Limite não tem "início" observado como um
                            # incidente tem; a chave é a condição em si, e a
                            # janela de repetição decide se lembra de novo.
                            "chave": f"met:{a['host_id']}:{a['chave']}",
                        }
                        for a in await self.alertas(db)
                        if a["chave"] not in ("conexao", "servico", "servicos")
                    ]
                    await self.notificacoes.despachar(db, metricas)
                except Exception:
                    log.exception("falha ao notificar limites de recurso")

            await db.commit()

        self._ciclos += 1
        self._ultimo_ciclo = datetime.now(timezone.utc)
        self._versao += 1

    async def _registrar_incidentes(
        self,
        db,
        host,
        host_ok: bool,
        doentes: list[dict],
        reinicios: dict | None = None,
        containers: dict | None = None,
        erro: str = "",
    ) -> None:
        """
        Abre/fecha incidente a partir do que este ciclo já leu — sem SSH
        extra. Isolado do resto do ciclo: um erro aqui não pode derrubar a
        amostra, que é o dado que mais importa.
        """
        if self.incidentes is None:
            return
        try:
            eventos = await self.incidentes.registrar_ciclo(
                db, host, host_ok=host_ok, doentes=doentes,
                reinicios=reinicios, erro=erro,
            )
        except Exception:
            log.exception("falha ao registrar incidente do host %s", host.id)
            return

        # Apuração ANTES do aviso, e só quando a máquina está atendendo:
        # é o que faz o aviso de retorno já sair com a causa, em vez de
        # uma segunda mensagem depois dizendo o que caberia na primeira.
        # Nunca levanta, e tem teto por passada.
        if eventos and host_ok and self.apuracao is not None:
            try:
                await self.apuracao.apurar_fechados(
                    db, host, eventos, containers=containers,
                )
            except Exception:
                log.exception("falha ao apurar incidentes de %s", host.name)

        # Aviso externo é o último passo de propósito: se o Telegram
        # estiver fora, a amostra e o incidente já estão gravados. O
        # serviço nunca levanta — ver `NotificacaoService.despachar`.
        if eventos and self.notificacoes is not None:
            await self.notificacoes.despachar(db, eventos)

    async def _analisar_logs(self, db, host, containers: dict | None = None) -> None:
        """
        Lê o log SÓ de quem já está com incidente aberto.

        É o que mantém a promessa da tela: o painel não fica varrendo log
        de produção por conta própria. Enquanto está tudo de pé, nada é
        lido; quando algo cai, o log daquele container entra na análise —
        que é exatamente quando alguém iria abrir o log na mão.
        """
        if self.analise is None or self.incidentes is None:
            return
        try:
            abertos = await self.incidentes.listar_abertos(db, host_id=host.id)
            servicos = [i["servico"] for i in abertos if i["tipo"] == "servico" and i["servico"]]
            if servicos:
                await self.analise.analisar_servicos(
                    db, host, servicos, containers=containers,
                )
        except Exception:
            log.exception("falha ao analisar log do host %s", host.id)

    async def _amostrar(self, db, host) -> None:
        amostra = Amostra(host_id=host.id)

        try:
            dados = await self.metrics.collect(host)
        except SSHError as exc:
            # Falha vira amostra também: buraco no gráfico é informação.
            amostra.erro = str(exc)[:255]
            self._ultimo_erro[host.id] = amostra.erro
            db.add(amostra)
            # O erro REAL vai junto: é ele que distingue "recusou a
            # conexão" (sshd parado) de "não respondeu" (rede/VM fora) —
            # causas opostas com a mesma cara.
            await self._registrar_incidentes(
                db, host, host_ok=False, doentes=[], erro=amostra.erro,
            )
            return
        except Exception as exc:
            amostra.erro = f"{type(exc).__name__}: {exc}"[:255]
            self._ultimo_erro[host.id] = amostra.erro
            db.add(amostra)
            # O erro REAL vai junto: é ele que distingue "recusou a
            # conexão" (sshd parado) de "não respondeu" (rede/VM fora) —
            # causas opostas com a mesma cara.
            await self._registrar_incidentes(
                db, host, host_ok=False, doentes=[], erro=amostra.erro,
            )
            return

        self._ultimo_erro.pop(host.id, None)

        cpu = dados.get("cpu", {})
        mem = dados.get("memoria", {})
        gpus = dados.get("gpus", [])
        containers = dados.get("containers", [])

        carga = float(cpu.get("carga_por_nucleo", 0) or 0)
        amostra.carga_por_nucleo = round(carga, 3)
        # Carga por núcleo vira percentual para caber no mesmo eixo dos
        # outros. Acima de 1,0 por núcleo já é fila, então 100% é o teto
        # útil do gráfico.
        amostra.cpu_pct = round(min(carga * 100, 200), 1)

        # Ocupação real, quando a coleta conseguiu calcular. Zero aqui
        # significa "não medido" para o histórico antigo — a série devolve
        # nulo nesse caso em vez de desenhar uma máquina ociosa que nunca
        # existiu.
        uso = cpu.get("uso_pct")
        amostra.cpu_uso_pct = round(float(uso), 1) if uso is not None else 0.0

        amostra.mem_pct = round(float(mem.get("percentual", 0) or 0), 1)
        amostra.swap_pct = round(float(mem.get("swap_percentual", 0) or 0), 1)
        # Absolutos, para a tela poder dizer "12,6 GB de 16,0 GB" em vez
        # de só "78,8%".
        amostra.mem_total_mb = round((mem.get("total_bytes") or 0) / (1024 ** 2), 1)
        amostra.mem_usado_mb = round((mem.get("usado_bytes") or 0) / (1024 ** 2), 1)

        pct, ponto, livre, total_disco = _pior_disco(dados.get("discos", []))
        amostra.disco_pct = round(pct, 1)
        amostra.disco_ponto = ponto
        # Vazão de disco: é o que enxerga saturação. Ocupação em GB
        # não vê — disco vazio satura igual quando estoura o teto
        # de IOPS do provedor.
        io = dados.get("io") or {}
        amostra.disco_iops = float(io.get("iops") or 0)
        amostra.disco_util_pct = float(io.get("util_pct") or 0)
        amostra.disco_livre_gb = livre
        amostra.disco_total_gb = total_disco

        if gpus:
            g = gpus[0]
            amostra.gpu_pct = round(float(g.get("utilizacao_pct") or 0), 1)
            amostra.gpu_mem_pct = round(float(g.get("memoria_pct") or 0), 1)
            amostra.gpu_temp = round(float(g.get("temperatura_c") or 0), 1)
            amostra.gpu_mem_total_mb = round(
                (g.get("memoria_total_bytes") or 0) / (1024 ** 2), 1
            )
            amostra.gpu_mem_usado_mb = round(
                (g.get("memoria_usada_bytes") or 0) / (1024 ** 2), 1
            )
            # Modelo da placa fica no host: não muda entre amostras.
            nome_gpu = str(g.get("nome") or "")[:120]
            if nome_gpu and host.gpu_nome != nome_gpu:
                host.gpu_nome = nome_gpu

        amostra.containers_total = len(containers)
        amostra.coleta_ms = int(dados.get("coleta_ms", 0) or 0)

        # Estado dos serviços vem junto quando o host é do FindFace. Se
        # falhar, a amostra continua válida — métrica de máquina e estado
        # de container são coisas independentes.
        try:
            saude = await self.stack.health_summary(host)
            amostra.containers_rodando = int(saude.get("rodando", 0))
            amostra.containers_problema = int(saude.get("com_problema", 0))
            amostra.containers_total = int(saude.get("total", 0)) or amostra.containers_total
            await self._registrar_incidentes(
                db, host, host_ok=True,
                doentes=saude.get("servicos_doentes", []),
                reinicios=saude.get("reinicios", {}),
                # O mapa serviço->container: sem ele o `docker inspect` da
                # apuração procuraria "findface-video-worker" em vez de
                # "findface-multi-findface-video-worker-1", e falharia em
                # silêncio. Mesma armadilha já corrigida na análise de log.
                containers=saude.get("containers"),
            )
            await self._analisar_logs(db, host, containers=saude.get("containers"))

            # Catálogo de serviços do host, para a tela de notificações
            # listar sem SSH. Só grava quando muda — UPDATE por ciclo em
            # dado que não mudou é escrita à toa.
            vistos = sorted((saude.get("containers") or {}).keys())
            if vistos and list(host.servicos_conhecidos or []) != vistos:
                host.servicos_conhecidos = vistos
        except Exception:
            pass

        db.add(amostra)

        host.last_seen_at = datetime.now(timezone.utc)
        host.last_status = "ok"

    # ── Consulta ───────────────────────────────────────────────────────

    @staticmethod
    async def serie(db, host_id: int, horas: int = 6, pontos: int = 240) -> dict:
        """
        Série de um host, já reduzida ao número de pontos que a tela
        desenha.

        Reduzir aqui, e não no navegador, evita mandar 10 mil amostras
        para desenhar 240 pixels de largura.
        """
        horas = max(1, min(horas, 24 * 30))
        desde = datetime.now(timezone.utc) - timedelta(hours=horas)

        resultado = await db.execute(
            select(Amostra)
            .where(Amostra.host_id == host_id, Amostra.ts >= desde)
            .order_by(Amostra.ts)
        )
        linhas = list(resultado.scalars().all())
        if not linhas:
            return {"host_id": host_id, "horas": horas, "amostras": [], "total": 0}

        # Amostragem por passo. Média seria mais bonita e esconderia pico
        # — e é justamente o pico que interessa num gráfico de recurso.
        passo = max(1, len(linhas) // pontos)
        selecionadas = linhas[::passo]
        # O último ponto sempre entra: é o "agora" da tela.
        if selecionadas and selecionadas[-1] is not linhas[-1]:
            selecionadas.append(linhas[-1])

        return {
            "host_id": host_id,
            "horas": horas,
            "total": len(linhas),
            "amostras": [
                {
                    "ts": a.ts.isoformat(),
                    "cpu": a.cpu_pct,
                    # Nulo = amostra gravada antes de existir medição de
                    # uso. O gráfico deixa buraco; buraco é honesto.
                    "cpu_uso": a.cpu_uso_pct if a.cpu_uso_pct > 0 else None,
                    "carga": a.carga_por_nucleo,
                    "mem": a.mem_pct,
                    "mem_total_mb": a.mem_total_mb,
                    "mem_usado_mb": a.mem_usado_mb,
                    "swap": a.swap_pct,
                    "disco": a.disco_pct,
                    "disco_ponto": a.disco_ponto,
                    "disco_livre_gb": a.disco_livre_gb,
                    "disco_total_gb": a.disco_total_gb,
                    "gpu": a.gpu_pct,
                    "gpu_mem": a.gpu_mem_pct,
                    "gpu_mem_total_mb": a.gpu_mem_total_mb,
                    "gpu_mem_usado_mb": a.gpu_mem_usado_mb,
                    "gpu_temp": a.gpu_temp,
                    "cont_rodando": a.containers_rodando,
                    "cont_total": a.containers_total,
                    "cont_problema": a.containers_problema,
                    "coleta_ms": a.coleta_ms,
                    "erro": a.erro,
                }
                for a in selecionadas
            ],
        }

    # ── Cadência ───────────────────────────────────────────────────────

    def registrar_atividade(self) -> None:
        """
        Alguém falou com o painel.

        Chamada em toda requisição autenticada — inclusive no poll da
        tela, que é justamente o sinal de que há alguém com o Monitor
        aberto. Se estava no modo econômico, acorda o laço na hora: a
        primeira tela tem de vir com leitura fresca, não com o que sobrou
        de cinco minutos atrás.
        """
        estava_ocioso = self.modo() == "economico"
        self._ultima_atividade = datetime.now(timezone.utc)
        if estava_ocioso and self._acordar is not None:
            self._acordar.set()

    def modo(self) -> str:
        """`ativo` enquanto há gente usando; `economico` depois disso."""
        limite_min = int(self._cfg("monitor.ocioso_apos_min", 10))
        if limite_min <= 0 or self._ultima_atividade is None:
            # Sem nunca ter tido atividade, começa econômico: subir o
            # painel não é motivo para acelerar nada.
            return "ativo" if limite_min <= 0 else "economico"
        parado = datetime.now(timezone.utc) - self._ultima_atividade
        return "economico" if parado > timedelta(minutes=limite_min) else "ativo"

    def intervalo_atual(self) -> int:
        """Segundos entre ciclos, conforme o modo."""
        if self.modo() == "economico":
            return max(30, int(self._cfg("monitor.intervalo_ocioso_s", 300)))
        return max(15, int(self._cfg("monitor.intervalo_s", 60)))

    def chave_cache(self) -> str:
        """
        Identifica a versão atual do que a tela mostra.

        O resumo do Monitor era recalculado a CADA poll de 10 s, por aba
        aberta — cerca de 21 consultas ao banco, das quais cinco em cada
        seis eram trabalho jogado fora: os dados só mudam quando o
        coletor roda, a cada 60 s. Com três abas abertas, isso era mais
        de 6 consultas por segundo, para sempre, sem nada ter mudado.

        Com esta chave, N abas custam o mesmo que uma, e o recálculo
        acontece uma vez por ciclo.
        """
        return f"{self._ciclos}:{self._versao}"

    def invalidar(self) -> None:
        """
        Força o próximo resumo a ser recalculado.

        Chamada por quem muda o que a tela mostra fora do ciclo: cadastrar
        servidor, desativar, mexer em limiar. Sem isso, uma alteração
        ficaria invisível até o coletor passar — e "salvei e não mudou
        nada" é o tipo de dúvida que vira chamado.
        """
        self._versao += 1

    async def alertas(self, db) -> list[dict]:
        """
        Alertas ativos, a partir da última amostra de cada host.

        Avaliado sob demanda, não guardado: o estado atual é sempre
        derivável das amostras, e uma tabela de alertas exigiria decidir
        quando um alerta "fecha" — complexidade sem retorno para quatro
        servidores.

        Os limites de host podem ter exceção por servidor (`LimiarService`);
        os de serviço vêm sempre do incidente já aberto pelo ciclo do
        monitor — assim o alerta aponta a causa provável, não só "algo
        quebrou".
        """
        padrao = {
            "cpu_pct": float(self._cfg("alerta.cpu_pct", 90)),
            "mem_pct": float(self._cfg("alerta.mem_pct", 90)),
            "swap_pct": float(self._cfg("alerta.swap_pct", 50)),
            "disco_pct": float(self._cfg("alerta.disco_pct", 90)),
            "gpu_mem_pct": float(self._cfg("alerta.gpu_mem_pct", 92)),
            "gpu_temp": float(self._cfg("alerta.gpu_temp", 85)),
            "disco_util_pct": float(self._cfg("alerta.disco_util_pct", 85)),
            "disco_iops": float(self._cfg("alerta.disco_iops", 0)),
        }
        indisponivel_min = float(self._cfg("alerta.servico_indisponivel_min", 15))

        resultado = await db.execute(
            select(Host).where(Host.enabled.is_(True), Host.monitorar.is_(True))
        )
        hosts = list(resultado.scalars().all())

        saida: list[dict] = []
        for host in hosts:
            r = await db.execute(
                select(Amostra)
                .where(Amostra.host_id == host.id)
                .order_by(Amostra.ts.desc())
                .limit(1)
            )
            a = r.scalars().first()
            if a is None:
                continue

            # Uma consulta só cobre toda exceção de limite deste host —
            # geral ou por serviço. Sem override nenhum, o dicionário vem
            # vazio e tudo cai no padrão global.
            overrides = await self.limiares.resolver_lote(db, host.id) if self.limiares else {}
            limites = {k: overrides.get(f"::{k}", v) for k, v in padrao.items()}

            def add(chave, nivel, texto, valor, limite, acao="", onde="",
                    onde_aba="", significa="", extra=None):
                # `acao` e `onde` existem para quem está de plantão às 3h
                # e nunca viu este sistema. Alerta que só diz o que está
                # errado obriga a pessoa a descobrir o que fazer — e é
                # exatamente aí que ela liga para alguém. `onde_aba` é o
                # mesmo destino, mas em id de aba — a tela usa isso para
                # navegar de verdade, em vez de só escrever o nome.
                item = {
                    "host_id": host.id,
                    "host": host.name,
                    "rotulo": host.rotulo,
                    "papel": host.role,
                    "chave": chave,
                    "nivel": nivel,
                    "texto": texto,
                    "valor": valor,
                    "limite": limite,
                    # O que o número quer dizer na prática. Separado da
                    # `acao` porque são perguntas diferentes: "isso é
                    # grave?" e "o que eu faço?".
                    "significa": significa,
                    "acao": acao,
                    "onde": onde,
                    "onde_aba": onde_aba,
                    "em": a.ts.isoformat(),
                }
                if extra:
                    item.update(extra)
                saida.append(item)

            if a.erro:
                add("conexao", "critico",
                    "não respondeu ao monitoramento", 0, 0,
                    significa="Nada pode ser verificado nesta máquina agora "
                              "— inclusive o FindFace, que pode estar "
                              "rodando normal. Falha de rede dá este mesmo "
                              "aviso.",
                    acao="Confira se a máquina está ligada e se a rede está "
                         "de pé. Em Servidores, use 'Testar conexão' para "
                         "ver o erro exato.",
                    onde="Servidores", onde_aba="servidores")
                continue

            if a.disco_pct >= limites["disco_pct"]:
                add("disco", "critico" if a.disco_pct >= 95 else "atencao",
                    f"disco {a.disco_ponto} em {a.disco_pct}% — restam "
                    f"{a.disco_livre_gb} GB"
                    + (f" de {a.disco_total_gb} GB" if a.disco_total_gb else ""),
                    a.disco_pct, limites["disco_pct"],
                    significa="Quando o disco encher, o banco de dados para "
                              "de gravar e as passagens deixam de ser "
                              "registradas.",
                    acao="Disco cheio para o banco de dados e o "
                         "reconhecimento para de gravar. Em Manutenção, use "
                         "'Diagnosticar' para ver o que está ocupando, e "
                         "'Arquivar log antigo' se for log.",
                    onde="Manutenção", onde_aba="manutencao")

            if a.mem_pct >= limites["mem_pct"]:
                add("memoria", "critico" if a.mem_pct >= 95 else "atencao",
                    f"memória em {a.mem_pct}%"
                    + (f" — {_gb(a.mem_usado_mb)} de {_gb(a.mem_total_mb)} em uso"
                       if a.mem_total_mb else ""),
                    a.mem_pct, limites["mem_pct"],
                    significa="Perto do limite, o sistema começa a encerrar "
                              "serviços para liberar memória — e o serviço "
                              "encerrado para sem avisar.",
                    acao="Perto do limite, o sistema mata containers por "
                         "falta de memória. Em Serviços, procure algum com "
                         "'morto por falta de memória'.",
                    onde="Serviços", onde_aba="servicos")

            if a.swap_pct >= limites["swap_pct"]:
                add("swap", "atencao",
                    f"swap em {a.swap_pct}% — a máquina está usando disco "
                    "como se fosse memória",
                    a.swap_pct, limites["swap_pct"],
                    significa="Disco é muito mais lento que memória: nada "
                              "para de funcionar, mas o reconhecimento fica "
                              "lento. É sinal de VM pequena para a carga.",
                    acao="Swap em uso significa que a máquina está usando "
                         "disco como se fosse memória — o reconhecimento "
                         "fica lento. Não é urgente, mas indica que a VM "
                         "está pequena.",
                    onde="Recursos", onde_aba="recursos")

            if a.cpu_pct >= limites["cpu_pct"]:
                add("cpu", "atencao",
                    f"CPU sobrecarregada — {a.carga_por_nucleo} processo por "
                    "núcleo (o normal é abaixo de 1,00)",
                    a.cpu_pct, limites["cpu_pct"],
                    significa="Há processo esperando a vez de usar o "
                              "processador. Nada parou, mas tudo responde "
                              "mais devagar, inclusive o reconhecimento.",
                    acao="Em Recursos, veja quais containers estão "
                         "consumindo mais CPU.",
                    onde="Recursos", onde_aba="recursos")

            if a.gpu_mem_pct >= limites["gpu_mem_pct"]:
                add("gpu_mem", "critico",
                    f"memória de vídeo em {a.gpu_mem_pct}%"
                    + (f" — {a.gpu_mem_usado_mb:.0f} de {a.gpu_mem_total_mb:.0f} MB"
                       if a.gpu_mem_total_mb else ""),
                    a.gpu_mem_pct, limites["gpu_mem_pct"],
                    significa="Sem memória de vídeo sobrando, a próxima "
                              "câmera a conectar falha e o serviço de vídeo "
                              "entra em ciclo de reinício.",
                    acao="Perto do limite, a próxima câmera causa falha e o "
                         "findface-video-worker entra em ciclo de reinício. "
                         "Em Serviços, confira a contagem de reinícios dele.",
                    onde="Serviços", onde_aba="servicos")

            # Saturação de disco. O alerta que faltava: um pico de E/S
            # derrubou um servidor inteiro e o painel não tinha como ver,
            # porque media ocupação em GB e `iowait`, não vazão.
            if limites["disco_util_pct"] > 0 and a.disco_util_pct >= limites["disco_util_pct"]:
                add("disco_io", "critico" if a.disco_util_pct >= 95 else "atencao",
                    f"disco saturado — ocupado {a.disco_util_pct:.0f}% do tempo, "
                    f"{a.disco_iops:.0f} operações por segundo",
                    a.disco_util_pct, limites["disco_util_pct"],
                    significa="O disco está no limite do que aguenta. A fila "
                              "cresce, a latência dispara e TUDO que toca "
                              "disco trava junto — inclusive o SSH e o "
                              "systemd. A máquina parece cair, quando está "
                              "só esperando o disco.",
                    acao="Veja em Recursos quem está escrevendo. Se houver "
                         "backup em andamento, ele agora roda em prioridade "
                         "baixa de E/S — mas em disco de nuvem o teto de "
                         "IOPS é contratado, e a saída pode ser um disco "
                         "com teto maior.",
                    onde="Recursos", onde_aba="recursos")

            if limites["disco_iops"] > 0 and a.disco_iops >= limites["disco_iops"]:
                add("disco_iops", "atencao",
                    f"{a.disco_iops:.0f} operações de disco por segundo",
                    a.disco_iops, limites["disco_iops"],
                    significa="Perto do teto contratado do disco. Ao "
                              "encostar nele, o provedor enfileira e a "
                              "latência explode.",
                    acao="Em Recursos, veja o que está gerando E/S. Backup e "
                         "limpeza de eventos são os suspeitos usuais.",
                    onde="Recursos", onde_aba="recursos")

            if a.gpu_temp >= limites["gpu_temp"]:
                add("gpu_temp", "atencao",
                    f"GPU a {a.gpu_temp} °C", a.gpu_temp, limites["gpu_temp"],
                    significa="A partir de 85 °C a placa reduz a própria "
                              "velocidade para não queimar, e o "
                              "reconhecimento fica lento.",
                    acao="Acima de 85 °C a GPU reduz a própria velocidade "
                         "para não queimar, e o reconhecimento fica lento. "
                         "Costuma ser refrigeração do datacenter.",
                    onde="Recursos", onde_aba="recursos")

            # Serviço com problema: um alerta POR container, com a causa
            # provável e desde quando — em vez de "3 serviço(s) com
            # problema", que obrigava abrir Serviços para descobrir qual.
            if self.incidentes is not None:
                abertos = [
                    i for i in await self.incidentes.listar_abertos(db, host_id=host.id)
                    if i["tipo"] == "servico"
                ]
                for inc in abertos:
                    min_host = overrides.get(f"{inc['servico']}::servico_indisponivel_min", indisponivel_min)
                    grave = inc["nivel"] == "critico" or (inc["duracao_s"] or 0) >= min_host * 60
                    add("servico", "critico" if grave else "atencao",
                        inc["texto"],
                        inc["duracao_s"], min_host * 60,
                        significa=descrever(inc["servico"])[1],
                        acao=inc["causa_provavel"] or (
                            "Em Serviços, veja o log do container para o motivo. "
                            "'Reiniciar' resolve a maioria dos casos."
                        ),
                        onde="Serviços", onde_aba="servicos",
                        extra={
                            "servico": inc["servico"],
                            "desde": inc["inicio"],
                            # A tela mostra "há 6min" ao lado do texto; sem
                            # este campo ela recebia `undefined` e escrevia
                            # "há —".
                            "duracao_s": inc["duracao_s"],
                        })
            elif a.containers_problema > 0:
                # Sem o serviço de incidentes injetado (instalação antiga
                # ou teste), cai no aviso agregado de antes.
                add("servicos", "atencao",
                    f"{a.containers_problema} serviço(s) com problema",
                    a.containers_problema, 0,
                    acao="Em Serviços, veja qual não está rodando. O botão "
                         "de log mostra o motivo, e 'Reiniciar' resolve a "
                         "maioria dos casos sem afetar o resto.",
                    onde="Serviços", onde_aba="servicos")

        ordem = {"critico": 0, "atencao": 1}
        saida.sort(key=lambda x: (ordem.get(x["nivel"], 9), x["host"]))
        return saida

    @staticmethod
    async def contar_antigas(db, dias: int) -> int:
        """Quantas amostras a limpeza removeria. Só leitura."""
        if dias <= 0:
            return 0
        from sqlalchemy import func

        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        r = await db.execute(
            select(func.count(Amostra.id)).where(Amostra.ts < corte)
        )
        return int(r.scalar() or 0)

    @staticmethod
    async def limpar(db, dias: int) -> int:
        """Chamada pela faxina."""
        if dias <= 0:
            return 0
        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        r = await db.execute(delete(Amostra).where(Amostra.ts < corte))
        return r.rowcount or 0
