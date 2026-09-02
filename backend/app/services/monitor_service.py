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


class MonitorService:
    def __init__(
        self, metrics, stack, config=None, incidentes=None, limiares=None,
        analise=None, notificacoes=None,
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

            # Aviso de limite de recurso: uma leitura por CICLO (não por
            # host) sobre o que `alertas()` já sabe calcular — sem
            # duplicar a lógica de limiar em dois lugares.
            if self.notificacoes is not None:
                try:
                    metricas = [
                        {
                            "tipo": "metrica",
                            "host_id": a["host_id"],
                            "host": a["host"],
                            "servico": "",
                            "nivel": a["nivel"],
                            "texto": a["texto"],
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

    async def _registrar_incidentes(
        self, db, host, host_ok: bool, doentes: list[dict], reinicios: dict | None = None
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
                db, host, host_ok=host_ok, doentes=doentes, reinicios=reinicios,
            )
        except Exception:
            log.exception("falha ao registrar incidente do host %s", host.id)
            return

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
            await self._registrar_incidentes(db, host, host_ok=False, doentes=[])
            return
        except Exception as exc:
            amostra.erro = f"{type(exc).__name__}: {exc}"[:255]
            self._ultimo_erro[host.id] = amostra.erro
            db.add(amostra)
            await self._registrar_incidentes(db, host, host_ok=False, doentes=[])
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

            def add(chave, nivel, texto, valor, limite, acao="", onde="", onde_aba="", extra=None):
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
                    "chave": chave,
                    "nivel": nivel,
                    "texto": texto,
                    "valor": valor,
                    "limite": limite,
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
                    f"sem contato com {host.rotulo}", 0, 0,
                    acao="Confira se a máquina está ligada e se a rede está "
                         "de pé. Em Servidores, use 'Testar conexão' para "
                         "ver o erro exato.",
                    onde="Servidores", onde_aba="servidores")
                continue

            if a.disco_pct >= limites["disco_pct"]:
                add("disco", "critico" if a.disco_pct >= 95 else "atencao",
                    f"disco {a.disco_ponto} em {a.disco_pct}% "
                    f"— só {a.disco_livre_gb} GB livres",
                    a.disco_pct, limites["disco_pct"],
                    acao="Disco cheio para o banco de dados e o "
                         "reconhecimento para de gravar. Em Manutenção, use "
                         "'Diagnosticar' para ver o que está ocupando, e "
                         "'Arquivar log antigo' se for log.",
                    onde="Manutenção", onde_aba="manutencao")

            if a.mem_pct >= limites["mem_pct"]:
                add("memoria", "critico" if a.mem_pct >= 95 else "atencao",
                    f"memória em {a.mem_pct}%", a.mem_pct, limites["mem_pct"],
                    acao="Perto do limite, o sistema mata containers por "
                         "falta de memória. Em Serviços, procure algum com "
                         "'morto por falta de memória'.",
                    onde="Serviços", onde_aba="servicos")

            if a.swap_pct >= limites["swap_pct"]:
                add("swap", "atencao",
                    f"swap em {a.swap_pct}%",
                    a.swap_pct, limites["swap_pct"],
                    acao="Swap em uso significa que a máquina está usando "
                         "disco como se fosse memória — o reconhecimento "
                         "fica lento. Não é urgente, mas indica que a VM "
                         "está pequena.",
                    onde="Recursos", onde_aba="recursos")

            if a.cpu_pct >= limites["cpu_pct"]:
                add("cpu", "atencao",
                    f"carga em {a.carga_por_nucleo} por núcleo",
                    a.cpu_pct, limites["cpu_pct"],
                    acao="Acima de 1,00 há processo esperando CPU. Em "
                         "Recursos, veja quais containers estão consumindo "
                         "mais.",
                    onde="Recursos", onde_aba="recursos")

            if a.gpu_mem_pct >= limites["gpu_mem_pct"]:
                add("gpu_mem", "critico",
                    f"memória de vídeo em {a.gpu_mem_pct}%",
                    a.gpu_mem_pct, limites["gpu_mem_pct"],
                    acao="Perto do limite, a próxima câmera causa falha e o "
                         "findface-video-worker entra em ciclo de reinício. "
                         "Em Serviços, confira a contagem de reinícios dele.",
                    onde="Serviços", onde_aba="servicos")

            if a.gpu_temp >= limites["gpu_temp"]:
                add("gpu_temp", "atencao",
                    f"GPU a {a.gpu_temp} °C", a.gpu_temp, limites["gpu_temp"],
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
                        f"{inc['servico']} — {inc['texto']}",
                        inc["duracao_s"], min_host * 60,
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
