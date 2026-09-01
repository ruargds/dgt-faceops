"""
Histórico de indisponibilidade — abre e fecha sozinho.

Nasce de uma decisão simples: o ciclo do monitor contínuo (60 s) já
pergunta a cada host "quem está de pé" para desenhar os cartões
(`MonitorService._amostrar` → `StackService.health_summary`). Este serviço
só compara esse resultado com o que já estava aberto — nenhuma consulta
nova ao servidor, nenhum custo além de algumas linhas de banco quando algo
muda de estado.

Regra de fechamento: incidente que não aparece mais na lista de
problemáticos do ciclo atual está resolvido. Não há "reabrir" — se o
mesmo serviço cair nove segundos depois, é um incidente novo, com início
próprio. Juntar os dois seria mentir sobre quanto tempo ficou fora.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.models.incidente import Incidente

log = logging.getLogger("faceops.incidentes")

# Janela do laço de reinício. `RestartCount` do Docker é ACUMULADO desde
# que o container foi criado — um worker com 7 reinícios em três meses não
# tem problema nenhum, e tratá-lo como problema encheria a tela de alarme
# falso permanente. O que interessa é reinício acontecendo AGORA, então
# comparamos a contagem atual com a de até 30 min atrás: quando os
# reinícios param, a janela desliza e o incidente fecha sozinho.
JANELA_REINICIO_S = 30 * 60

REINICIOS_PADRAO = 5


def _causa_provavel(d: dict) -> str:
    """
    Hipótese heurística a partir do que o Docker já contou — nenhum
    modelo, nenhuma chamada externa: só os sinais que `stack_service` já
    coleta em toda passada, lidos com o vocabulário de quem opera o
    FindFace.
    """
    if d.get("motivo") == "loop":
        return (
            f"reiniciou {d.get('reinicios_janela', '?')}x nos últimos "
            f"{JANELA_REINICIO_S // 60} min — sinal de câmera problemática ou "
            "falta de recurso, comum no findface-video-worker."
        )
    if d.get("oom_killed"):
        return (
            "morto por falta de memória (OOM) — memória ou VRAM perto do "
            "limite; confira Recursos antes de reiniciar, ou ele cai de novo."
        )
    if d.get("exit_code"):
        return f"saiu com código de erro {d['exit_code']} — veja o log do container."
    if d.get("saude") == "unhealthy":
        return "healthcheck do container está falhando — veja o log para o motivo."
    if d.get("estado") and d["estado"] != "running":
        return "container parado — verifique se foi manual ou por falta de memória/disco."
    return ""


class IncidenteService:
    def __init__(self, config=None, limiares=None) -> None:
        self.config = config
        # Opcional: sem ele, o limite de reinício é o global do catálogo.
        self.limiares = limiares
        # (host_id, servico) -> [(timestamp, RestartCount)] dentro da janela.
        # Só memória: o baseline se refaz sozinho depois de um restart do
        # painel, e perder essa memória não perde nada que esteja no banco.
        self._hist_reinicios: dict[tuple[int, str], list[tuple[float, int]]] = {}

    def _cfg(self, chave: str, padrao):
        if self.config is None:
            return padrao
        try:
            return self.config.get(chave)
        except (KeyError, ValueError, TypeError):
            return padrao

    # ── Laço de reinício ───────────────────────────────────────────────

    def _flapping(
        self, host_id: int, reinicios: dict, agora: datetime, limites: dict
    ) -> list[dict]:
        """
        Quais serviços reiniciaram demais DENTRO da janela. Compara a
        contagem de agora com a mais antiga ainda na janela — não com o
        total acumulado do container, que só cresce e nunca voltaria a
        zero.
        """
        achados: list[dict] = []
        ts = agora.timestamp()
        corte = ts - JANELA_REINICIO_S
        padrao = float(self._cfg("alerta.servico_reinicios", REINICIOS_PADRAO))

        for servico, contagem in (reinicios or {}).items():
            chave = (host_id, servico)
            hist = [p for p in self._hist_reinicios.get(chave, []) if p[0] >= corte]
            hist.append((ts, int(contagem)))
            self._hist_reinicios[chave] = hist

            # Container recriado zera o RestartCount: delta negativo vira 0
            # em vez de virar um número sem sentido.
            delta = max(0, hist[-1][1] - hist[0][1])
            limite = float(limites.get(f"{servico}::servico_reinicios", padrao))
            if delta >= limite:
                achados.append({
                    "servico": servico,
                    "motivo": "loop",
                    "reinicios_janela": delta,
                })

        # Serviço que sumiu da leitura (container removido) não precisa
        # ocupar memória para sempre.
        vivos = {(host_id, s) for s in (reinicios or {})}
        for chave in [k for k in self._hist_reinicios if k[0] == host_id and k not in vivos]:
            self._hist_reinicios.pop(chave, None)

        return achados

    # ── Ciclo do monitor ─────────────────────────────────────────────────

    async def registrar_ciclo(
        self,
        db,
        host,
        host_ok: bool,
        doentes: list[dict],
        reinicios: dict | None = None,
        agora: datetime | None = None,
    ) -> list[dict]:
        """
        Chamado uma vez por host a cada ciclo do monitor, depois da
        amostra. Abre incidente para quem entrou em problema, fecha quem
        saiu.

        Devolve os eventos desta passada (o que abriu e o que fechou) —
        é o que alimenta a notificação, sem que a notificação precise
        reler o banco para descobrir o que mudou.
        """
        agora = agora or datetime.now(timezone.utc)

        r = await db.execute(
            select(Incidente).where(Incidente.host_id == host.id, Incidente.fim.is_(None))
        )
        abertos = {(i.tipo, i.servico): i for i in r.scalars().all()}

        limites = {}
        if self.limiares is not None and host_ok:
            try:
                limites = await self.limiares.resolver_lote(db, host.id)
            except Exception:
                log.exception("falha ao resolver limiares do host %s", host.id)

        atuais: dict[tuple[str, str], dict] = {}
        if not host_ok:
            atuais[("host", "")] = {
                "nivel": "critico",
                "texto": f"sem contato com {host.name}",
                "causa": "rede fora, VM desligada ou parada — confira o provedor antes de investigar o painel.",
            }
        else:
            # Laço de reinício primeiro: se o mesmo serviço também estiver
            # doente por outro motivo, o motivo mais específico prevalece.
            for d in self._flapping(host.id, reinicios or {}, agora, limites):
                atuais[("servico", d["servico"])] = {
                    "nivel": "atencao",
                    "texto": f"{d['servico']} reiniciando em laço",
                    "causa": _causa_provavel(d),
                }
            for d in doentes or []:
                nome = d.get("servico") or ""
                if not nome:
                    continue
                grave = bool(d.get("oom_killed")) or (d.get("exit_code") or 0) != 0
                atuais[("servico", nome)] = {
                    "nivel": "critico" if grave else "atencao",
                    "texto": f"{nome} com problema",
                    "causa": _causa_provavel(d),
                }

        eventos: list[dict] = []

        # Fecha quem sumiu da lista de problemas.
        for chave, incidente in abertos.items():
            if chave in atuais:
                continue
            # Máquina sem contato: NÃO sabemos nada dos serviços dela. Fechar
            # aqui registraria uma recuperação que ninguém observou — o
            # serviço "voltou" no exato instante em que o host caiu.
            if not host_ok and chave[0] == "servico":
                continue
            incidente.fim = agora
            inicio = incidente.inicio
            if inicio.tzinfo is None:
                inicio = inicio.replace(tzinfo=timezone.utc)
            incidente.duracao_s = max(0.0, (agora - inicio).total_seconds())
            eventos.append({
                "tipo": "retorno",
                "host_id": host.id,
                "host": host.name,
                "servico": incidente.servico,
                "nivel": incidente.nivel,
                "duracao_s": incidente.duracao_s,
                # A chave carrega o início: o mesmo serviço caindo de novo
                # amanhã é outro evento, e precisa avisar de novo.
                "chave": f"fim:{host.id}:{incidente.tipo}:{incidente.servico}:{inicio.isoformat()}",
            })

        # Abre quem é novo.
        for chave, info in atuais.items():
            if chave in abertos:
                continue
            tipo, servico = chave
            db.add(Incidente(
                host_id=host.id, tipo=tipo, servico=servico,
                nivel=info["nivel"], texto=info["texto"],
                causa_provavel=info["causa"], inicio=agora,
            ))
            eventos.append({
                "tipo": "queda",
                "host_id": host.id,
                "host": host.name,
                "servico": servico,
                "nivel": info["nivel"],
                "texto": info["texto"],
                "causa_provavel": info["causa"],
                "inicio": agora,
                "chave": f"ini:{host.id}:{tipo}:{servico}:{agora.isoformat()}",
            })

        return eventos

    # ── Consulta ───────────────────────────────────────────────────────

    async def listar_abertos(self, db, host_id: int | None = None) -> list[dict]:
        condicoes = [Incidente.fim.is_(None)]
        if host_id is not None:
            condicoes.append(Incidente.host_id == host_id)
        r = await db.execute(
            select(Incidente).where(*condicoes).order_by(Incidente.inicio)
        )
        agora = datetime.now(timezone.utc)
        saida = []
        for i in r.scalars().all():
            inicio = i.inicio if i.inicio.tzinfo else i.inicio.replace(tzinfo=timezone.utc)
            saida.append(_serializar(i, aberto_ha_s=(agora - inicio).total_seconds()))
        return saida

    async def listar_recentes(self, db, dias: int = 3, host_id: int | None = None) -> list[dict]:
        """Abertos + fechados na janela — para o painel 'serviços por máquina'."""
        desde = datetime.now(timezone.utc) - timedelta(days=max(1, dias))
        condicoes = [
            (Incidente.fim.is_(None)) | (Incidente.fim >= desde),
        ]
        if host_id is not None:
            condicoes.append(Incidente.host_id == host_id)
        r = await db.execute(
            select(Incidente).where(*condicoes).order_by(Incidente.inicio.desc())
        )
        agora = datetime.now(timezone.utc)
        saida = []
        for i in r.scalars().all():
            inicio = i.inicio if i.inicio.tzinfo else i.inicio.replace(tzinfo=timezone.utc)
            aberto_ha_s = (agora - inicio).total_seconds() if i.fim is None else None
            saida.append(_serializar(i, aberto_ha_s=aberto_ha_s))
        return saida

    # ── Reincidência ───────────────────────────────────────────────────

    async def reincidencia(self, db, dias: int = 14, minimo: int | None = None) -> list[dict]:
        """
        O que repete, com que frequência e em que horário.

        Um serviço que cai uma vez é um incidente; um que cai sete vezes
        em cinco dias, sempre de madrugada, é outra conversa — e era
        justamente essa que o painel não sabia ter. Nenhum modelo aqui:
        é contagem sobre a tabela de incidentes.
        """
        if minimo is None:
            minimo = int(self._cfg("alerta.reincidencia_min", 3))

        desde = datetime.now(timezone.utc) - timedelta(days=max(1, dias))
        r = await db.execute(
            select(Incidente)
            .where(Incidente.inicio >= desde)
            .order_by(Incidente.inicio)
        )
        linhas = list(r.scalars().all())

        agora = datetime.now(timezone.utc)
        meio = agora - timedelta(days=max(1, dias) / 2)

        grupos: dict[tuple[int, str, str], dict] = {}
        for i in linhas:
            inicio = i.inicio if i.inicio.tzinfo else i.inicio.replace(tzinfo=timezone.utc)
            chave = (i.host_id, i.tipo, i.servico)
            g = grupos.setdefault(chave, {
                "host_id": i.host_id,
                "tipo": i.tipo,
                "servico": i.servico,
                "ocorrencias": 0,
                "tempo_fora_s": 0.0,
                "aberto_agora": False,
                "primeira": inicio,
                "ultima": inicio,
                "horas": {},
                "recentes": 0,
                "anteriores": 0,
            })
            g["ocorrencias"] += 1
            g["tempo_fora_s"] += float(i.duracao_s or 0)
            g["aberto_agora"] = g["aberto_agora"] or i.fim is None
            g["primeira"] = min(g["primeira"], inicio)
            g["ultima"] = max(g["ultima"], inicio)
            g["horas"][inicio.hour] = g["horas"].get(inicio.hour, 0) + 1
            if inicio >= meio:
                g["recentes"] += 1
            else:
                g["anteriores"] += 1

        saida = []
        for g in grupos.values():
            if g["ocorrencias"] < minimo:
                continue

            # Faixa de horário só é informação quando concentra de verdade.
            # "cai a qualquer hora" é uma resposta legítima, e dizer um
            # horário falso seria pior que não dizer nada.
            horas_ordenadas = sorted(g["horas"].items(), key=lambda x: x[1], reverse=True)
            topo = horas_ordenadas[0]
            concentrada = topo[1] >= max(2, g["ocorrencias"] * 0.5)

            intervalo_h = None
            if g["ocorrencias"] > 1:
                janela = (g["ultima"] - g["primeira"]).total_seconds() / 3600
                intervalo_h = round(janela / (g["ocorrencias"] - 1), 1)

            if g["recentes"] > g["anteriores"]:
                tendencia = "piorando"
            elif g["recentes"] < g["anteriores"]:
                tendencia = "melhorando"
            else:
                tendencia = "estavel"

            saida.append({
                "host_id": g["host_id"],
                "tipo": g["tipo"],
                "servico": g["servico"],
                "ocorrencias": g["ocorrencias"],
                "tempo_fora_s": round(g["tempo_fora_s"], 1),
                "aberto_agora": g["aberto_agora"],
                "primeira": g["primeira"].isoformat(),
                "ultima": g["ultima"].isoformat(),
                "hora_tipica": topo[0] if concentrada else None,
                "intervalo_medio_h": intervalo_h,
                "tendencia": tendencia,
                "dias": dias,
            })

        saida.sort(key=lambda x: (x["ocorrencias"], x["tempo_fora_s"]), reverse=True)
        return saida

    # ── Faxina ─────────────────────────────────────────────────────────

    @staticmethod
    async def contar_antigas(db, dias: int) -> int:
        if dias <= 0:
            return 0
        from sqlalchemy import func

        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        r = await db.execute(
            select(func.count(Incidente.id)).where(
                Incidente.fim.isnot(None), Incidente.fim < corte
            )
        )
        return int(r.scalar() or 0)

    @staticmethod
    async def limpar(db, dias: int) -> int:
        """
        Só incidentes FECHADOS entram na conta — um incidente aberto é
        estado atual, não histórico, e apagá-lo faria a tela achar que o
        problema nunca existiu enquanto ele ainda está acontecendo.
        """
        if dias <= 0:
            return 0
        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        r = await db.execute(
            delete(Incidente).where(Incidente.fim.isnot(None), Incidente.fim < corte)
        )
        return r.rowcount or 0


def _serializar(i: Incidente, aberto_ha_s: float | None) -> dict:
    return {
        "id": i.id,
        "host_id": i.host_id,
        "tipo": i.tipo,
        "servico": i.servico,
        "nivel": i.nivel,
        "texto": i.texto,
        "causa_provavel": i.causa_provavel,
        "inicio": i.inicio.isoformat(),
        "fim": i.fim.isoformat() if i.fim else None,
        "duracao_s": i.duracao_s if i.fim else aberto_ha_s,
        "aberto": i.fim is None,
    }
