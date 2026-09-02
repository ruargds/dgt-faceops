"""Histórico de indisponibilidade — abre e fecha sozinho pelo monitor."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.database import get_db
from app.models.user import User

router = APIRouter(prefix="/api/incidentes", tags=["incidentes"])


@router.get("/abertos")
async def abertos(
    request: Request,
    host_id: int | None = Query(default=None),
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """O que está fora agora — serviço ou host, com causa provável e desde quando."""
    return {"incidentes": await request.app.state.incidentes.listar_abertos(db, host_id=host_id)}


@router.get("/recentes")
async def recentes(
    request: Request,
    dias: int = Query(default=3, ge=1, le=30),
    host_id: int | None = Query(default=None),
    servico: str | None = Query(default=None, max_length=120),
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Abertos + fechados na janela — para 'serviços por máquina' mostrar
    quem já voltou, e para o histórico de um serviço na tela de Serviços.

    Consulta local: a janela já foi gravada pelo ciclo do monitor. Abrir
    este histórico não abre SSH nenhum.
    """
    return {
        "incidentes": await request.app.state.incidentes.listar_recentes(
            db, dias=dias, host_id=host_id, servico=servico
        )
    }


@router.get("/{incidente_id}/apuracao")
async def apuracao(
    incidente_id: int,
    request: Request,
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    A apuração completa de um incidente — veredito e achados.

    Separada da listagem de propósito: a lista de 7 dias de um serviço
    não deve carregar o log de cada queda junto. A lista traz só o
    veredito; os achados vêm quando alguém abre.
    """
    from app.models.incidente import Incidente

    incidente = await db.get(Incidente, incidente_id)
    if incidente is None:
        raise HTTPException(status_code=404, detail="incidente não encontrado")

    return {
        "id": incidente.id,
        "host_id": incidente.host_id,
        "servico": incidente.servico,
        "tipo": incidente.tipo,
        "inicio": incidente.inicio.isoformat(),
        "fim": incidente.fim.isoformat() if incidente.fim else None,
        "apurado_em": incidente.apurado_em.isoformat() if incidente.apurado_em else None,
        # `None` aqui significa "nunca apurado", que é diferente de
        # "apurado e não achou nada" — o segundo grava um veredito. A
        # tela usa essa distinção para decidir se oferece "apurar agora".
        "apuracao": incidente.apuracao,
    }


@router.post("/{incidente_id}/apurar")
async def apurar_agora(
    incidente_id: int,
    request: Request,
    autor: User = Depends(require_permission("services.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Apura sob demanda um incidente já fechado.

    Existe para dois casos: o incidente é anterior a esta função, ou a
    apuração automática falhou porque o servidor ainda não atendia.

    Só para incidente FECHADO: enquanto está aberto, o que interessa é a
    tela de log ao vivo, e o journal do período ainda está crescendo.
    """
    from app.models.host import Host
    from app.models.incidente import Incidente

    incidente = await db.get(Incidente, incidente_id)
    if incidente is None:
        raise HTTPException(status_code=404, detail="incidente não encontrado")
    if incidente.fim is None:
        raise HTTPException(
            status_code=400,
            detail="este incidente ainda está aberto — a apuração roda quando ele fecha",
        )

    host = await db.get(Host, incidente.host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não cadastrado")

    servico = request.app.state.apuracao
    containers = {}
    try:
        saude = await request.app.state.stack.health_summary(host)
        containers = saude.get("containers") or {}
    except Exception:
        # Sem o mapa, a apuração ainda vale: só o `docker inspect` fica
        # de fora. Melhor uma apuração parcial que nenhuma.
        pass

    resultado = await servico.apurar(host, incidente, containers=containers)
    incidente.apuracao = resultado
    incidente.apurado_em = datetime.now(timezone.utc)
    await db.commit()
    return {"id": incidente.id, "apuracao": resultado}
