"""Câmeras do FindFace: quantas, quando falaram, quanto geram."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
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
        dados = await request.app.state.licenca.ler(host)
    except (LicencaError, SSHError) as exc:
        erro_ssh = str(exc)
    else:
        # A leitura por SSH traz a licenca, nao a contagem de cameras --
        # o NTLS nao sabe quantas cameras existem. Quando a API estiver
        # cadastrada, completa aqui, inclusive separando detector externo.
        if configurado(host):
            try:
                dados.update(await request.app.state.ffapi.contagens(host))
                dados["cameras_cadastradas"] = dados.get("cameras_total")
            except FFApiError:
                pass
        return dados

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


class RetencaoIn(BaseModel):
    """
    Nova politica de rotatividade do FindFace.

    `dias` chega por campo, em DIAS -- a API do fabricante fala em segundos
    e a conversao fica no painel: pedir segundos a quem opera e convite a
    apagar cinco anos achando que apagou cinco dias.
    """

    dias: dict[str, float] = Field(default_factory=dict)
    chaves: dict[str, bool] = Field(default_factory=dict)
    confirmar_host: str = ""


@router.get("/{host_id}/retencao")
async def retencao(
    host_id: int,
    request: Request,
    _: User = Depends(require_permission("maintenance.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Politica de rotatividade do proprio FindFace. So leitura.

    E a resposta para "o disco enche toda semana": em vez de apagar o
    passado, ajustar por quanto tempo cada coisa fica. Quadro completo de
    evento sem correspondencia e o campo que mais devolve disco.
    """
    from app.services.ffapi_service import FFApiError, configurado

    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor nao encontrado")
    if not configurado(host):
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{host.name}' nao tem a API do FindFace cadastrada. A politica "
                "de retencao so existe por essa via -- informe usuario e senha "
                "em Servidores -> editar -> API do FindFace."
            ),
        )

    try:
        return await request.app.state.ffapi.retencao(host)
    except FFApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.patch("/{host_id}/retencao")
async def salvar_retencao(
    host_id: int,
    dados: RetencaoIn,
    request: Request,
    autor: User = Depends(require_permission("maintenance.apply")),
    db: AsyncSession = Depends(get_db),
):
    """
    Grava a politica de rotatividade. **Destrutiva no tempo.**

    Nada e apagado no instante do clique: o FindFace passa a remover o que
    ficar mais velho que o novo prazo, no ritmo dele. Ainda assim exige
    digitar o nome do servidor -- diminuir um prazo joga fora dado de
    producao que nenhum backup essencial recupera.
    """
    from app.services.ffapi_service import FFApiError

    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor nao encontrado")

    if dados.confirmar_host.strip() != host.name:
        raise HTTPException(
            status_code=400,
            detail=(
                f"confirmacao necessaria: digite exatamente '{host.name}'. "
                "Reduzir um prazo faz o FindFace apagar o que passar dele."
            ),
        )

    try:
        resultado = await request.app.state.ffapi.salvar_retencao(
            host, dados.dias, dados.chaves
        )
    except FFApiError as exc:
        await audit_service.registrar(
            db,
            usuario=autor.username,
            action="retencao.salvar",
            target=host.name,
            ip=client_ip(request),
            success=False,
            level="critical",
            detail={"erro": str(exc)[:400]},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="retencao.salvar",
        target=host.name,
        ip=client_ip(request),
        level="critical",
        detail={"dias": dados.dias, "chaves": dados.chaves},
    )
    return resultado
