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

Com intervalo de 60 s e quatro servidores, isso é ~1,5% de um núcleo no
painel e nada mensurável nos servidores.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db.database import AsyncSessionLocal
from app.models.amostra import Amostra
from app.models.host import Host
from app.services.ssh_service import SSHError

log = logging.getLogger("faceops.monitor")


def _pior_disco(discos: list[dict]) -> tuple[float, str, float]:
    """O disco mais cheio — é o que dispara alerta."""
    if not discos:
        return 0.0, "", 0.0
    pior = max(discos, key=lambda d: d.get("percentual", 0))
    return (
        float(pior.get("percentual", 0)),
        str(pior.get("ponto", ""))[:64],
        round(pior.get("livre_bytes", 0) / (1024 ** 3), 2),
    )


class MonitorService:
    def __init__(self, metrics, stack, config=None) -> None:
        self.metrics = metrics
        self.stack = stack
        self.config = config
        self._tarefa: asyncio.Task | None = None
        self._rodando = False
        # Último erro por host, para a tela não repetir a mesma queixa
        self._ultimo_erro: dict[int, str] = {}
        self._ciclos = 0
        self._ultimo_ciclo: datetime | None = None

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
            "intervalo_s": int(self._cfg("monitor.intervalo_s", 60)),
            "erros": self._ultimo_erro,
        }

    # ── Laço ───────────────────────────────────────────────────────────

    async def _laco(self) -> None:
        # Espera antes do primeiro ciclo: na subida o painel já tem o que
        # fazer, e uma coleta a mais competiria com isso.
        await asyncio.sleep(20)

        while self._rodando:
            intervalo = max(15, int(self._cfg("monitor.intervalo_s", 60)))
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
                await asyncio.sleep(intervalo)
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

            await db.commit()

        self._ciclos += 1
        self._ultimo_ciclo = datetime.now(timezone.utc)

    async def _amostrar(self, db, host) -> None:
        amostra = Amostra(host_id=host.id)

        try:
            dados = await self.metrics.collect(host)
        except SSHError as exc:
            # Falha vira amostra também: buraco no gráfico é informação.
            amostra.erro = str(exc)[:255]
            self._ultimo_erro[host.id] = amostra.erro
            db.add(amostra)
            return
        except Exception as exc:
            amostra.erro = f"{type(exc).__name__}: {exc}"[:255]
            self._ultimo_erro[host.id] = amostra.erro
            db.add(amostra)
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

        amostra.mem_pct = round(float(mem.get("percentual", 0) or 0), 1)
        amostra.swap_pct = round(float(mem.get("swap_percentual", 0) or 0), 1)

        pct, ponto, livre = _pior_disco(dados.get("discos", []))
        amostra.disco_pct = round(pct, 1)
        amostra.disco_ponto = ponto
        amostra.disco_livre_gb = livre

        if gpus:
            g = gpus[0]
            amostra.gpu_pct = round(float(g.get("utilizacao_pct") or 0), 1)
            amostra.gpu_mem_pct = round(float(g.get("memoria_pct") or 0), 1)
            amostra.gpu_temp = round(float(g.get("temperatura_c") or 0), 1)

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
                    "carga": a.carga_por_nucleo,
                    "mem": a.mem_pct,
                    "swap": a.swap_pct,
                    "disco": a.disco_pct,
                    "disco_ponto": a.disco_ponto,
                    "disco_livre_gb": a.disco_livre_gb,
                    "gpu": a.gpu_pct,
                    "gpu_mem": a.gpu_mem_pct,
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

    async def alertas(self, db) -> list[dict]:
        """
        Alertas ativos, a partir da última amostra de cada host.

        Avaliado sob demanda, não guardado: o estado atual é sempre
        derivável das amostras, e uma tabela de alertas exigiria decidir
        quando um alerta "fecha" — complexidade sem retorno para quatro
        servidores.
        """
        limites = {
            "cpu": float(self._cfg("alerta.cpu_pct", 90)),
            "mem": float(self._cfg("alerta.mem_pct", 90)),
            "swap": float(self._cfg("alerta.swap_pct", 50)),
            "disco": float(self._cfg("alerta.disco_pct", 90)),
            "gpu_mem": float(self._cfg("alerta.gpu_mem_pct", 92)),
            "gpu_temp": float(self._cfg("alerta.gpu_temp", 85)),
        }

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

            def add(chave, nivel, texto, valor, limite, acao="", onde=""):
                # `acao` e `onde` existem para quem está de plantão às 3h
                # e nunca viu este sistema. Alerta que só diz o que está
                # errado obriga a pessoa a descobrir o que fazer — e é
                # exatamente aí que ela liga para alguém.
                saida.append({
                    "host_id": host.id,
                    "host": host.name,
                    "chave": chave,
                    "nivel": nivel,
                    "texto": texto,
                    "valor": valor,
                    "limite": limite,
                    "acao": acao,
                    "onde": onde,
                    "em": a.ts.isoformat(),
                })

            if a.erro:
                add("conexao", "critico",
                    f"sem contato com {host.name}", 0, 0,
                    acao="Confira se a máquina está ligada e se a rede está "
                         "de pé. Em Servidores, use 'Testar conexão' para "
                         "ver o erro exato.",
                    onde="Servidores")
                continue

            if a.disco_pct >= limites["disco"]:
                add("disco", "critico" if a.disco_pct >= 95 else "atencao",
                    f"disco {a.disco_ponto} em {a.disco_pct}% "
                    f"— só {a.disco_livre_gb} GB livres",
                    a.disco_pct, limites["disco"],
                    acao="Disco cheio para o banco de dados e o "
                         "reconhecimento para de gravar. Em Manutenção, use "
                         "'Diagnosticar' para ver o que está ocupando, e "
                         "'Arquivar log antigo' se for log.",
                    onde="Manutenção")

            if a.mem_pct >= limites["mem"]:
                add("memoria", "critico" if a.mem_pct >= 95 else "atencao",
                    f"memória em {a.mem_pct}%", a.mem_pct, limites["mem"],
                    acao="Perto do limite, o sistema mata containers por "
                         "falta de memória. Em Serviços, procure algum com "
                         "'morto por falta de memória'.",
                    onde="Serviços")

            if a.swap_pct >= limites["swap"]:
                add("swap", "atencao",
                    f"swap em {a.swap_pct}%",
                    a.swap_pct, limites["swap"],
                    acao="Swap em uso significa que a máquina está usando "
                         "disco como se fosse memória — o reconhecimento "
                         "fica lento. Não é urgente, mas indica que a VM "
                         "está pequena.",
                    onde="Recursos")

            if a.cpu_pct >= limites["cpu"]:
                add("cpu", "atencao",
                    f"carga em {a.carga_por_nucleo} por núcleo",
                    a.cpu_pct, limites["cpu"],
                    acao="Acima de 1,00 há processo esperando CPU. Em "
                         "Recursos, veja quais containers estão consumindo "
                         "mais.",
                    onde="Recursos")

            if a.gpu_mem_pct >= limites["gpu_mem"]:
                add("gpu_mem", "critico",
                    f"memória de vídeo em {a.gpu_mem_pct}%",
                    a.gpu_mem_pct, limites["gpu_mem"],
                    acao="Perto do limite, a próxima câmera causa falha e o "
                         "findface-video-worker entra em ciclo de reinício. "
                         "Em Serviços, confira a contagem de reinícios dele.",
                    onde="Serviços")

            if a.gpu_temp >= limites["gpu_temp"]:
                add("gpu_temp", "atencao",
                    f"GPU a {a.gpu_temp} °C", a.gpu_temp, limites["gpu_temp"],
                    acao="Acima de 85 °C a GPU reduz a própria velocidade "
                         "para não queimar, e o reconhecimento fica lento. "
                         "Costuma ser refrigeração do datacenter.",
                    onde="Recursos")

            if a.containers_problema > 0:
                add("servicos", "atencao",
                    f"{a.containers_problema} serviço(s) com problema",
                    a.containers_problema, 0,
                    acao="Em Serviços, veja qual não está rodando. O botão "
                         "de log mostra o motivo, e 'Reiniciar' resolve a "
                         "maioria dos casos sem afetar o resto.",
                    onde="Serviços")

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
