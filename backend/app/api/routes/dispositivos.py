"""Câmeras do FindFace: quantas, quando falaram, quanto geram."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_permission
from app.db.database import get_db
from app.models.host import Host
from app.models.user import User
from app.services import audit_service
from app.services.dispositivos_service import DispositivosError, PERIODOS
from app.services.ssh_service import SSHError

router = APIRouter(prefix="/api/dispositivos", tags=["dispositivos"])


@router.get("/{host_id}/licenca")
async def licenca(
    host_id: int,
    request: Request,
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Licenciamento do FindFace daquele servidor: liberado, em uso, restante.

    Consulta barata e só leitura — vai pela API HTTP da NtechLab, sem SSH.
    Exige URL e token cadastrados no servidor (aba Servidores → API do
    FindFace); sem isso responde 400 dizendo o que falta, porque o limite
    de licença não está no banco lido por SSH.
    """
    from app.services.ffapi_service import FFApiError, configurado
    from app.services.licenca_service import LicencaError

    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")

    # SSH primeiro: o NTLS atende em localhost sem pedir login, e o painel
    # já tem SSH com sudo neste servidor. A API fica como alternativa para
    # quem preferir não abrir shell — e para o caso de o NTLS rodar em
    # outra máquina que não esta.
    erro_ssh = ""
    try:
        return await request.app.state.licenca.ler(host)
    except (LicencaError, SSHError) as exc:
        erro_ssh = str(exc)

    if not configurado(host):
        raise HTTPException(
            status_code=502,
            detail=(
                f"{erro_ssh} E a API do FindFace não está cadastrada neste "
                "servidor: informe usuário e senha em Servidores → editar → "
                "API do FindFace para tentar por lá."
            ),
        )

    try:
        return await request.app.state.ffapi.licenca(host)
    except FFApiError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"pelo servidor: {erro_ssh} | pela API: {exc}",
        ) from exc


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
        dados["lido_de"] = host.name
        dados["aviso"] = ""
    except (SSHError, DispositivosError) as exc:
        # O FindFace desta instalacao e distribuido: o servidor escolhido na
        # tela pode nao ser o que roda o PostgreSQL. O painel tem SSH em
        # todos, entao procura nos outros em vez de mandar o operador achar
        # o host certo na Topologia e voltar aqui.
        erros = [f"{host.name}: {exc}"]
        dados = None
        resultado = await db.execute(
            select(Host).where(Host.enabled.is_(True), Host.id != host.id)
        )
        for alternativo in resultado.scalars().all():
            try:
                dados = await request.app.state.dispositivos.listar(
                    alternativo, request.app.state.stack, periodo
                )
            except (SSHError, DispositivosError) as outro:
                erros.append(f"{alternativo.name}: {outro}")
                continue
            dados["lido_de"] = alternativo.name
            dados["aviso"] = (
                f"'{host.name}' nao tem o banco do FindFace; estes numeros "
                f"vieram de '{alternativo.name}'."
            )
            break

        if dados is None:
            raise HTTPException(
                status_code=502, detail=" | ".join(erros)
            ) from exc

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
