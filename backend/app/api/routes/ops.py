"""Métricas e controle dos serviços do FindFace Multi."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_permission
from app.db.database import get_db
from app.models.host import Host
from app.models.user import User
from app.schemas import AcaoContainerIn, AcaoStackIn, PowerContainerIn
from app.services import audit_service
from app.services.ssh_service import SSHError
from app.services.stack_service import StackError

router = APIRouter(prefix="/api", tags=["operacao"])


async def _host_ou_404(db: AsyncSession, host_id: int) -> Host:
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")
    if not host.enabled:
        raise HTTPException(status_code=400, detail=f"servidor '{host.name}' está desativado")
    return host


# ── Métricas ───────────────────────────────────────────────────────────


@router.get("/metrics/{host_id}")
async def metricas(
    host_id: int,
    request: Request,
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrato atual de RAM, GPU, disco e carga. Coleta na hora, direto da
    máquina — é o que o botão "Atualizar" chama.
    """
    host = await _host_ou_404(db, host_id)
    try:
        dados = await request.app.state.metrics.collect(host)
    except SSHError as exc:
        host.last_status = "erro"
        host.last_error = str(exc)[:2000]
        await db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    dados["coletado_em"] = datetime.now(timezone.utc).isoformat()
    host.last_seen_at = datetime.now(timezone.utc)
    host.last_status = "ok"
    host.last_error = ""
    await db.commit()
    return dados


@router.get("/metrics")
async def metricas_todos(
    request: Request,
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Coleta de todos os hosts ativos, em paralelo.

    Um host fora do ar não pode esconder os outros: cada falha vira um
    item com `ok: false` no lugar de derrubar a resposta inteira.
    """
    import asyncio

    resultado = await db.execute(select(Host).where(Host.enabled.is_(True)).order_by(Host.name))
    hosts = list(resultado.scalars().all())

    async def _coletar(host: Host) -> dict:
        try:
            dados = await request.app.state.metrics.collect(host)
            dados["ok"] = True
            dados["coletado_em"] = datetime.now(timezone.utc).isoformat()
            return dados
        except SSHError as exc:
            return {
                "ok": False,
                "host_id": host.id,
                "host": host.name,
                "erro": str(exc)[:400],
            }

    return await asyncio.gather(*(_coletar(h) for h in hosts))


@router.get("/metrics/{host_id}/armazenamento")
async def armazenamento(
    host_id: int,
    request: Request,
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Onde o disco do FindFace está sendo gasto. Ação separada porque `du`
    numa árvore com milhões de fotos leva minutos.
    """
    host = await _host_ou_404(db, host_id)
    try:
        return await request.app.state.metrics.storage_breakdown(host)
    except SSHError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Serviços ───────────────────────────────────────────────────────────


@router.get("/services/{host_id}")
async def listar_servicos(
    host_id: int,
    request: Request,
    _: User = Depends(require_permission("services.view")),
    db: AsyncSession = Depends(get_db),
):
    host = await _host_ou_404(db, host_id)
    try:
        return await request.app.state.stack.list_services(host)
    except (SSHError, StackError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/services/{host_id}/logs/{container}")
async def logs_container(
    host_id: int,
    container: str,
    request: Request,
    linhas: int = Query(default=200, ge=1, le=2000),
    _: User = Depends(require_permission("services.view")),
    db: AsyncSession = Depends(get_db),
):
    host = await _host_ou_404(db, host_id)
    try:
        return {"container": container, "log": await request.app.state.stack.logs(host, container, linhas)}
    except (SSHError, StackError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/services/{host_id}/restart")
async def reiniciar_container(
    host_id: int,
    dados: AcaoContainerIn,
    request: Request,
    autor: User = Depends(require_permission("services.restart")),
    db: AsyncSession = Depends(get_db),
):
    host = await _host_ou_404(db, host_id)
    try:
        saida = await request.app.state.stack.restart_container(host, dados.container)
    except (SSHError, StackError) as exc:
        await audit_service.registrar(
            db,
            usuario=autor.username,
            action="services.restart",
            target=f"{host.name}/{dados.container}",
            ip=client_ip(request),
            success=False,
            detail={"erro": str(exc)[:500]},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="services.restart",
        target=f"{host.name}/{dados.container}",
        ip=client_ip(request),
        detail=saida,
    )
    return saida


@router.post("/services/{host_id}/power")
async def parar_ou_subir_container(
    host_id: int,
    dados: PowerContainerIn,
    request: Request,
    autor: User = Depends(require_permission("services.power")),
    db: AsyncSession = Depends(get_db),
):
    """
    Para ou sobe UM container do FindFace.

    Separada do `restart` por permissão, e não por capricho: reiniciar
    volta sozinho, parar FICA parado. Um `findface-video-worker` parado
    por descuido é reconhecimento fora do ar até alguém notar — e ninguém
    nota, porque não há erro, só ausência.

    Daí a confirmação digitada em `stop`: o risco aqui não é errar a
    ação, é errar QUAL serviço. Digitar o nome prova que o dedo estava na
    linha certa. `start` não pede nada — subir o que estava parado não
    tem como piorar a situação.
    """
    host = await _host_ou_404(db, host_id)

    if dados.acao == "stop" and dados.confirmar.strip() != dados.container:
        raise HTTPException(
            status_code=400,
            detail="para parar um serviço, confirme digitando o nome do "
                   f"container: '{dados.container}'",
        )

    try:
        saida = await request.app.state.stack.container_action(
            host, dados.container, acao=dados.acao
        )
    except (SSHError, StackError) as exc:
        await audit_service.registrar(
            db,
            usuario=autor.username,
            action="services.power",
            target=f"{host.name}/{dados.container}",
            ip=client_ip(request),
            success=False,
            detail={"acao": dados.acao, "erro": str(exc)[:500]},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="services.power",
        target=f"{host.name}/{dados.container}",
        ip=client_ip(request),
        detail=saida,
    )
    return saida


@router.post("/services/{host_id}/stack")
async def acao_stack(
    host_id: int,
    dados: AcaoStackIn,
    request: Request,
    autor: User = Depends(require_permission("services.stack")),
    db: AsyncSession = Depends(get_db),
):
    """
    Para/sobe o stack inteiro do FindFace. Derruba o reconhecimento.

    Exige dupla confirmação: o operador digita o nome do servidor. Vale
    para `stop` e `restart` — `up` só religa o que já estava parado.
    """
    host = await _host_ou_404(db, host_id)

    if dados.acao in ("stop", "restart") and dados.confirmar_host.strip() != host.name:
        raise HTTPException(
            status_code=400,
            detail=(
                f"confirmação necessária: digite exatamente '{host.name}' para "
                f"executar '{dados.acao}'. Esta ação interrompe o reconhecimento "
                "facial neste servidor."
            ),
        )

    try:
        saida = await request.app.state.stack.stack_action(host, dados.acao)
    except (SSHError, StackError) as exc:
        await audit_service.registrar(
            db,
            usuario=autor.username,
            action="services.stack",
            target=host.name,
            ip=client_ip(request),
            success=False,
            level="critical",
            detail={"acao": dados.acao, "erro": str(exc)[:500]},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="services.stack",
        target=host.name,
        ip=client_ip(request),
        level="critical",
        detail={"acao": dados.acao, "duracao_ms": saida["duracao_ms"]},
    )
    return saida


# ── Visão geral ────────────────────────────────────────────────────────


@router.get("/painel")
async def painel(
    request: Request,
    _: User = Depends(require_permission("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Resumo da tela inicial: um cartão por servidor com saúde dos serviços
    e último backup. Não coleta métrica — isso fica no botão Atualizar.
    """
    import asyncio

    from app.services.backup_service import BackupService

    resultado = await db.execute(select(Host).order_by(Host.name))
    hosts = list(resultado.scalars().all())
    ultimos = await BackupService.ultimo_por_host(db)

    async def _resumo(host: Host) -> dict:
        if not host.enabled:
            saude = {"ok": False, "erro": "servidor desativado", "total": 0,
                     "rodando": 0, "com_problema": 0}
        else:
            saude = await request.app.state.stack.health_summary(host)

        ultimo = ultimos.get(host.id)
        return {
            "host_id": host.id,
            "nome": host.name,
            "descricao": host.description,
            "papel": host.role,
            "endereco": host.address,
            "ativo": host.enabled,
            "tem_gpu": host.has_gpu,
            "ultimo_contato": host.last_seen_at.isoformat() if host.last_seen_at else None,
            "status_conexao": host.last_status,
            "servicos": saude,
            "ultimo_backup": {
                "id": ultimo.id,
                "perfil": ultimo.profile,
                "status": ultimo.status,
                "em": ultimo.started_at.isoformat(),
                "tamanho_bytes": ultimo.size_bytes,
            } if ultimo else None,
        }

    cartoes = await asyncio.gather(*(_resumo(h) for h in hosts))

    from app.services.storage_service import StorageService

    return {
        "servidores": list(cartoes),
        "armazenamento_painel": StorageService.espaco_local(),
    }
