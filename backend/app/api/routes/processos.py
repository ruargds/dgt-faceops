"""Processos ao vivo — um htop explicado, por servidor."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.database import get_db
from app.models.host import Host
from app.models.user import User
from app.services.processos_service import ProcessosError
from app.services.ssh_service import SSHError

router = APIRouter(prefix="/api/processos", tags=["processos"])


@router.get("/{host_id}")
async def snapshot(
    host_id: int,
    request: Request,
    limite: int = Query(default=25, ge=5, le=60),
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Foto instantânea dos processos: CPU, memória, carga e top consumidores.

    Sob demanda — a tela chama de poucos em poucos segundos enquanto está
    aberta. Não entra no coletor contínuo.
    """
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")
    if not host.enabled:
        raise HTTPException(status_code=400, detail=f"'{host.name}' está desativado")

    try:
        return await request.app.state.processos.snapshot(host, limite)
    except (SSHError, ProcessosError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
