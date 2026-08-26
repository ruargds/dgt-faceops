"""Câmeras do FindFace: quantas, quando falaram, quanto geram."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_permission
from app.db.database import get_db
from app.models.host import Host
from app.models.user import User
from app.services import audit_service
from app.services.dispositivos_service import DispositivosError, PERIODOS
from app.services.ssh_service import SSHError

router = APIRouter(prefix="/api/dispositivos", tags=["dispositivos"])


@router.get("/{host_id}")
async def listar(
    host_id: int,
    request: Request,
    periodo: str = Query(default="dia"),
    autor: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Câmeras cadastradas, última comunicação e volume de eventos.

    Consulta pesada — lê o banco do FindFace e agrega. Fica **sob
    demanda**, nunca no coletor contínuo: contar evento a cada minuto
    seria justamente o tipo de peso que o painel promete não criar.
    """
    if periodo not in PERIODOS:
        raise HTTPException(
            status_code=400,
            detail=f"período deve ser um de {sorted(PERIODOS)}",
        )

    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")
    if not host.enabled:
        raise HTTPException(status_code=400, detail=f"'{host.name}' está desativado")

    try:
        dados = await request.app.state.dispositivos.listar(
            host, request.app.state.stack, periodo
        )
    except (SSHError, DispositivosError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="dispositivos.consultar",
        target=host.name,
        ip=client_ip(request),
        detail={"periodo": periodo, "cameras": dados["total_cameras"]},
    )
    return dados


@router.post("/{host_id}/redescobrir")
async def redescobrir(
    host_id: int,
    request: Request,
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Refaz a descoberta do esquema do banco.

    Necessário depois de atualizar o FindFace: nomes de tabela mudam
    entre versões, e o esquema fica guardado em memória.
    """
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")
    try:
        return await request.app.state.dispositivos.descobrir(
            host, request.app.state.stack, forcar=True
        )
    except (SSHError, DispositivosError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
