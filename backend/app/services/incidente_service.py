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
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.models.incidente import Incidente

# Sem isto, um serviço batendo (RestartCount subindo) fica marcado como
# "de pé" porque em algum instante do ciclo ele está `running` — a fila do
# monitor pode até não ver o `restarting`. O limiar vem do LimiarService;
# este é só o piso absoluto que dispensa configuração.
REINICIOS_PADRAO = 5


def _causa_provavel(d: dict) -> str:
    """
    Hipótese heurística a partir do que o Docker já contou — nenhum
    modelo, nenhuma chamada externa: só os sinais que `stack_service` já
    coleta em toda passada, lidos com o vocabulário de quem opera o
    FindFace.
    """
    if d.get("oom_killed"):
        return (
            "morto por falta de memória (OOM) — memória ou VRAM perto do "
            "limite; confira Recursos antes de reiniciar, ou ele cai de novo."
        )
    if d.get("exit_code"):
        return f"saiu com código de erro {d['exit_code']} — veja o log do container."
    if d.get("saude") == "unhealthy":
        return "healthcheck do container está falhando — veja o log para o motivo."
    reinicios = d.get("reinicios") or 0
    if reinicios >= REINICIOS_PADRAO:
        return (
            f"reiniciando repetidamente ({reinicios}x) — sinal de câmera "
            "problemática ou falta de recurso, comum no findface-video-worker."
        )
    if d.get("estado") and d["estado"] != "running":
        return "container parado — verifique se foi manual ou por falta de memória/disco."
    return ""


class IncidenteService:
    # ── Ciclo do monitor ─────────────────────────────────────────────────

    async def registrar_ciclo(self, db, host, host_ok: bool, doentes: list[dict]) -> None:
        """
        Chamado uma vez por host a cada ciclo do monitor, depois da
        amostra. Abre incidente para quem entrou em problema, fecha quem
        saiu.
        """
        r = await db.execute(
            select(Incidente).where(Incidente.host_id == host.id, Incidente.fim.is_(None))
        )
        abertos = {(i.tipo, i.servico): i for i in r.scalars().all()}

        atuais: dict[tuple[str, str], dict] = {}
        if not host_ok:
            atuais[("host", "")] = {
                "nivel": "critico",
                "texto": f"sem contato com {host.name}",
                "causa": "rede fora, VM desligada ou parada — confira o provedor antes de investigar o painel.",
            }
        else:
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

        agora = datetime.now(timezone.utc)

        # Fecha quem sumiu da lista de problemas.
        for chave, incidente in abertos.items():
            if chave not in atuais:
                incidente.fim = agora
                inicio = incidente.inicio
                if inicio.tzinfo is None:
                    inicio = inicio.replace(tzinfo=timezone.utc)
                incidente.duracao_s = max(0.0, (agora - inicio).total_seconds())

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
        from datetime import timedelta

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

    # ── Faxina ─────────────────────────────────────────────────────────

    @staticmethod
    async def contar_antigas(db, dias: int) -> int:
        if dias <= 0:
            return 0
        from datetime import timedelta

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
        from datetime import timedelta

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
