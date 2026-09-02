"""Consulta do log de auditoria."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.database import get_db
from app.models.audit import AuditLog
from app.models.user import User
from app.schemas import AuditOut
from app.services import audit_service

router = APIRouter(prefix="/api/auditoria", tags=["auditoria"])


@router.get("", response_model=list[AuditOut])
async def listar(
    busca: str | None = Query(default=None, max_length=120),
    usuario: str | None = Query(default=None),
    action: str | None = Query(default=None),
    level: str | None = Query(default=None),
    desde: datetime | None = Query(default=None),
    ate: datetime | None = Query(default=None),
    so_falhas: bool = Query(default=False),
    limite: int = Query(default=200, ge=1, le=1000),
    _: User = Depends(require_permission("audit.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    A trilha, filtrada. Os filtros vêm de `audit_service.aplicar_filtros`
    — os mesmos que a exportação usa, para o CSV não trazer coisa
    diferente do que está na tela.
    """
    consulta = audit_service.aplicar_filtros(
        select(AuditLog).order_by(AuditLog.ts.desc()).limit(limite),
        busca=busca, usuario=usuario, action=action, level=level,
        desde=desde, ate=ate, so_falhas=so_falhas,
    )
    resultado = await db.execute(consulta)
    return [AuditOut.model_validate(r) for r in resultado.scalars().all()]


@router.get("/filtros")
async def filtros(
    dias: int = Query(default=90, ge=1, le=3650),
    _: User = Depends(require_permission("audit.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Quem e o quê existem de fato no log — para a tela oferecer escolha em
    vez de campo em branco.

    Sai do próprio log, não do catálogo de permissões nem da tabela de
    usuários: usuário apagado continua tendo agido, e ação que nunca
    aconteceu como opção só dá resultado vazio para quem escolhe.

    `usuario`, `action` e `ts` são indexados, então isto é varredura de
    índice — barato o suficiente para carregar junto com a tela.
    """
    corte = datetime.now(timezone.utc) - timedelta(days=dias)

    usuarios = await db.execute(
        select(AuditLog.usuario)
        .where(AuditLog.ts >= corte, AuditLog.usuario != "")
        .distinct()
        .order_by(AuditLog.usuario)
    )
    acoes = await db.execute(
        select(AuditLog.action)
        .where(AuditLog.ts >= corte)
        .distinct()
        .order_by(AuditLog.action)
    )
    return {
        "dias": dias,
        "usuarios": [u for (u,) in usuarios.all()],
        "acoes": [a for (a,) in acoes.all()],
    }


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
