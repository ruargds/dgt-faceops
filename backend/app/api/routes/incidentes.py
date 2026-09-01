"""Histórico de indisponibilidade — abre e fecha sozinho pelo monitor."""
from fastapi import APIRouter, Depends, Query, Request
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
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """Abertos + fechados na janela — para 'serviços por máquina' mostrar quem já voltou."""
    return {
        "incidentes": await request.app.state.incidentes.listar_recentes(
            db, dias=dias, host_id=host_id
        )
    }
