"""Consulta do log de auditoria."""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.database import get_db
from app.models.audit import AuditLog
from app.models.user import User
from app.schemas import AuditOut

router = APIRouter(prefix="/api/auditoria", tags=["auditoria"])


@router.get("", response_model=list[AuditOut])
async def listar(
    usuario: str | None = Query(default=None),
    action: str | None = Query(default=None),
    level: str | None = Query(default=None),
    desde: datetime | None = Query(default=None),
    limite: int = Query(default=200, ge=1, le=1000),
    _: User = Depends(require_permission("audit.view")),
    db: AsyncSession = Depends(get_db),
):
    consulta = select(AuditLog).order_by(AuditLog.ts.desc()).limit(limite)
    if usuario:
        consulta = consulta.where(AuditLog.usuario == usuario)
    if action:
        consulta = consulta.where(AuditLog.action == action)
    if level:
        consulta = consulta.where(AuditLog.level == level)
    if desde:
        consulta = consulta.where(AuditLog.ts >= desde)

    resultado = await db.execute(consulta)
    return [AuditOut.model_validate(r) for r in resultado.scalars().all()]


@router.get("/resumo")
async def resumo(
    _: User = Depends(require_permission("audit.view")),
    db: AsyncSession = Depends(get_db),
):
    """Contagem por nível e as ações críticas recentes."""
    from sqlalchemy import func

    contagem = await db.execute(
        select(AuditLog.level, func.count(AuditLog.id)).group_by(AuditLog.level)
    )
    criticas = await db.execute(
        select(AuditLog)
        .where(AuditLog.level == "critical")
        .order_by(AuditLog.ts.desc())
        .limit(20)
    )
    return {
        "por_nivel": {linha[0]: linha[1] for linha in contagem.all()},
        "criticas_recentes": [
            AuditOut.model_validate(r) for r in criticas.scalars().all()
        ],
    }
