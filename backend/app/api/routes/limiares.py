"""
Limiares de alerta por host e/ou serviço — exceção sobre o catálogo global.

Mesmo desenho de permissão de `configuracoes.py`: ler é `hosts.view`,
editar é `users.manage`. "Restaurar padrão" aqui é o mesmo verbo que lá —
apagar a exceção, não gravar um valor "de fábrica".
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_permission
from app.db.database import get_db
from app.models.user import User
from app.services import audit_service
from app.services.limiar_service import CHAVES_HOST, CHAVES_SERVICO, CHAVES_VALIDAS

router = APIRouter(prefix="/api/limiares", tags=["limiares"])


@router.get("")
async def listar(
    request: Request,
    _: User = Depends(require_permission("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    """Todas as exceções cadastradas, mais as chaves aceitas — a tela se monta a partir daqui."""
    return {
        "overrides": await request.app.state.limiares.listar(db),
        "chaves_host": sorted(CHAVES_HOST),
        "chaves_servico": sorted(CHAVES_SERVICO),
    }


class SalvarIn(BaseModel):
    chave: str
    valor: float
    host_id: int | None = None
    servico: str = ""


@router.put("")
async def salvar(
    dados: SalvarIn,
    request: Request,
    autor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    if dados.chave not in CHAVES_VALIDAS:
        raise HTTPException(status_code=400, detail=f"chave desconhecida: {dados.chave}")

    try:
        resultado = await request.app.state.limiares.salvar(
            db, dados.chave, dados.valor, dados.host_id, dados.servico, autor.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await audit_service.registrar(
        db, usuario=autor.username, action="limiar.salvar", ip=client_ip(request),
        detail=resultado,
    )
    await db.commit()
    return {"ok": True, **resultado}


@router.delete("/{override_id}")
async def restaurar_padrao(
    override_id: int,
    request: Request,
    autor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Apaga a exceção — o limite volta a ser o padrão do catálogo global."""
    ok = await request.app.state.limiares.restaurar(db, override_id)
    if not ok:
        raise HTTPException(status_code=404, detail="exceção não encontrada")

    await audit_service.registrar(
        db, usuario=autor.username, action="limiar.restaurar", ip=client_ip(request),
        target=str(override_id),
    )
    await db.commit()
    return {"ok": True}
