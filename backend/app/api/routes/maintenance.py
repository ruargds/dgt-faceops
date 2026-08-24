"""
Manutenção de disco e log — pela web, sem linha de comando.

Toda ação que escreve tem modo simulação, e a UI mostra o conteúdo exato
dos arquivos antes de aplicar. Nada aqui reinicia o FindFace.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_permission
from app.db.database import get_db
from app.models.host import Host
from app.models.user import User
from app.services import audit_service
from app.services.maintenance_service import ManutencaoError
from app.services.ssh_service import SSHError

router = APIRouter(prefix="/api/manutencao", tags=["manutencao"])


class ContencaoIn(BaseModel):
    simular: bool = True
    # Dupla confirmação quando for aplicar de verdade
    confirmar_host: str = ""


class ArquivarIn(BaseModel):
    destino: str = Field(min_length=1, max_length=255)
    simular: bool = True
    incluir_ativo: bool = False
    confirmar_host: str = ""


async def _host_ou_404(db: AsyncSession, host_id: int) -> Host:
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")
    if not host.enabled:
        raise HTTPException(status_code=400, detail=f"servidor '{host.name}' está desativado")
    return host


@router.get("/{host_id}")
async def diagnostico(
    host_id: int,
    request: Request,
    _: User = Depends(require_permission("maintenance.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Diagnóstico de disco e log. Só leitura.

    Demora ~20s: mede o crescimento do syslog amostrando o tamanho duas
    vezes. É o único número que diz se a contenção vale a pena — e
    depois, se ela funcionou.
    """
    host = await _host_ou_404(db, host_id)
    try:
        return await request.app.state.manutencao.diagnostico(host)
    except SSHError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{host_id}/contencao")
async def contencao(
    host_id: int,
    dados: ContencaoIn,
    request: Request,
    autor: User = Depends(require_permission("maintenance.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Aplica (ou simula) a contenção de log.

    Simular exige só `maintenance.view` — ver o que mudaria não muda nada.
    Aplicar exige `maintenance.apply` e confirmação por digitação.
    """
    host = await _host_ou_404(db, host_id)

    if not dados.simular:
        from app.core.permissions import permissions_for

        if "maintenance.apply" not in permissions_for(autor.role, autor.is_super_admin):
            raise HTTPException(
                status_code=403,
                detail=f"Seu perfil ({autor.role}) pode simular, mas não aplicar. "
                       "Necessária a permissão 'maintenance.apply'.",
            )
        if dados.confirmar_host.strip() != host.name:
            raise HTTPException(
                status_code=400,
                detail=f"confirmação necessária: digite exatamente '{host.name}'. "
                       "Esta ação escreve configuração de sistema e reinicia o "
                       "rsyslog e o journald (o FindFace não é afetado).",
            )

    try:
        resultado = await request.app.state.manutencao.aplicar_contencao(
            host, simular=dados.simular
        )
    except (SSHError, ManutencaoError) as exc:
        if not dados.simular:
            await audit_service.registrar(
                db,
                usuario=autor.username,
                action="maintenance.apply",
                target=host.name,
                ip=client_ip(request),
                success=False,
                level="critical",
                detail={"acao": "contencao", "erro": str(exc)[:600]},
            )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not dados.simular:
        await audit_service.registrar(
            db,
            usuario=autor.username,
            action="maintenance.apply",
            target=host.name,
            ip=client_ip(request),
            level="critical",
            detail={
                "acao": "contencao de log",
                "arquivos": [a["caminho"] for a in resultado["alteracoes"]],
            },
        )
    return resultado


@router.post("/{host_id}/arquivar")
async def arquivar(
    host_id: int,
    dados: ArquivarIn,
    request: Request,
    autor: User = Depends(require_permission("maintenance.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Move log rotacionado para um disco com folga. **Não apaga nada.**

    O `syslog` ativo só entra se `incluir_ativo` — e mesmo assim é
    copiado antes de ser zerado.
    """
    host = await _host_ou_404(db, host_id)

    if not dados.simular:
        from app.core.permissions import permissions_for

        if "maintenance.apply" not in permissions_for(autor.role, autor.is_super_admin):
            raise HTTPException(
                status_code=403,
                detail=f"Seu perfil ({autor.role}) pode simular, mas não aplicar.",
            )
        if dados.confirmar_host.strip() != host.name:
            raise HTTPException(
                status_code=400,
                detail=f"confirmação necessária: digite exatamente '{host.name}'.",
            )

    try:
        resultado = await request.app.state.manutencao.arquivar_logs(
            host,
            dados.destino,
            simular=dados.simular,
            incluir_ativo=dados.incluir_ativo,
        )
    except (SSHError, ManutencaoError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not dados.simular:
        await audit_service.registrar(
            db,
            usuario=autor.username,
            action="maintenance.apply",
            target=host.name,
            ip=client_ip(request),
            level="critical",
            detail={
                "acao": "arquivar log",
                "destino": dados.destino,
                "arquivos": len(resultado["candidatos"]),
                "bytes": resultado["total_bytes"],
                "incluiu_ativo": dados.incluir_ativo,
            },
        )
    return resultado
