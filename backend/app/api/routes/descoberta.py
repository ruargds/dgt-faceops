"""Descoberta — inventário do que roda em cada servidor."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_permission
from app.db.database import get_db
from app.models.host import Host
from app.models.user import User
from app.services import audit_service
from app.services.descoberta_service import DescobertaError
from app.services.ssh_service import SSHError

router = APIRouter(prefix="/api/descoberta", tags=["descoberta"])


@router.get("/rastreio")
async def rastreio(
    request: Request,
    host_id: int | None = None,
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Rastreio de falhas: o que esta comprometendo o funcionamento agora.

    Junta licenca, componentes internos, disco, coleta, backup e seguranca
    e devolve ACHADOS -- cada um com a evidencia que o servidor deu, o
    impacto em operacao e a acao. So leitura: nada aqui reinicia, apaga ou
    conserta sozinho.

    Sob demanda, duas execucoes SSH por servidor. Nao entra em laco de
    fundo.
    """
    return await request.app.state.rastreio.rodar(db, host_id)


@router.get("/internos/{host_id}")
async def internos(
    host_id: int,
    request: Request,
    _: User = Depends(require_permission("services.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Estado dos componentes internos do FindFace naquele servidor.

    Le de DENTRO da maquina, pela porta que o manual do fabricante
    documenta para cada componente (extraction-api 18666, sf-api 18411,
    video-manager 18810, ntls 3185...). Sem agente instalado: o painel ja
    tem SSH, que e o mesmo alcance que um agente teria, e um binario nosso
    em servidor de reconhecimento facial e peca a mais para auditar,
    versionar e manter.

    So leitura -- nenhum comando muda estado.
    """
    from app.services.internos_service import InternosError

    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor nao encontrado")

    try:
        return await request.app.state.internos.ler(host)
    except InternosError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc



@router.get("/{host_id}")
async def inventariar(
    host_id: int,
    request: Request,
    autor: User = Depends(require_permission("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Varredura completa do servidor: containers, bancos, portas, GPU, disco.

    Sob demanda — é uma sondagem por SSH, não entra no coletor contínuo.
    Serve tanto para o FindFace distribuído quanto para tudo num servidor só.
    """
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")
    if not host.enabled:
        raise HTTPException(status_code=400, detail=f"'{host.name}' está desativado")

    try:
        dados = await request.app.state.descoberta.inventariar(host)
    except (SSHError, DescobertaError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="descoberta.inventariar",
        target=host.name,
        ip=client_ip(request),
        detail={
            "containers": dados["docker"]["total_containers"],
            "servicos_dados": [s["tipo"] for s in dados["servicos_dados"]],
        },
    )
    return dados


@router.post("/{host_id}/cloudflared/reiniciar")
async def reiniciar_cloudflared(
    host_id: int,
    request: Request,
    autor: User = Depends(require_permission("services.restart")),
    db: AsyncSession = Depends(get_db),
):
    """Reinicia o Cloudflare Tunnel — e só ele — neste servidor."""
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")
    if not host.enabled:
        raise HTTPException(status_code=400, detail=f"'{host.name}' está desativado")

    try:
        resultado = await request.app.state.descoberta.reiniciar_cloudflared(host)
    except (SSHError, DescobertaError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="descoberta.cloudflared.reiniciar",
        target=host.name,
        ip=client_ip(request),
        level="critical",
        detail=resultado,
    )
    return {"ok": True, **resultado}


@router.get("/topologia/mapa")
async def topologia(
    request: Request,
    autor: User = Depends(require_permission("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Mapa de dependências do FindFace distribuído entre os servidores.

    Varre todos os servidores habilitados e mostra qual camada
    (vídeo, extração, busca, vetores, dados, app) roda em qual máquina.
    """
    resultado = await db.execute(
        select(Host).where(Host.enabled == True).order_by(Host.name)  # noqa: E712
    )
    hosts = list(resultado.scalars().all())

    try:
        dados = await request.app.state.descoberta.topologia(hosts)
    except (SSHError, DescobertaError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="descoberta.topologia",
        target="todos",
        ip=client_ip(request),
        detail={"servidores": len(hosts), "distribuido": dados.get("distribuido")},
    )
    return dados
