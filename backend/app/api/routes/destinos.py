"""
Destinos de backup — cadastrados pela web, não pelo `.env`.

Trocar destino é operação de rotina: credencial de nuvem vence, o cliente
muda de provedor, um bucket enche. Exigir editar arquivo e reiniciar
container para isso transformaria uma tarefa de dois minutos numa janela
de manutenção.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_permission
from app.core.vault import encrypt_secret, fingerprint
from app.db.database import get_db
from app.models.destino import Destino
from app.models.user import User
from app.schemas import DestinoIn, DestinoOut, DestinoUpdate
from app.services import audit_service

router = APIRouter(prefix="/api/destinos", tags=["destinos"])


def _para_out(d: Destino) -> DestinoOut:
    saida = DestinoOut.model_validate(d)
    saida.tem_credencial = bool(d.azure_conn_enc or d.rclone_conf_enc)
    return saida


def _validar_por_tipo(tipo: str, dados) -> None:
    """Cada tipo tem campos obrigatórios diferentes."""
    if tipo == "local" and not (dados.caminho or "").startswith("/"):
        raise HTTPException(
            status_code=400,
            detail="destino local exige um caminho absoluto (ex.: /data/backups)",
        )
    if tipo == "azure":
        if not dados.azure_container:
            raise HTTPException(status_code=400, detail="informe o container do Azure Blob")
        if getattr(dados, "azure_conn", None) is None and not getattr(dados, "id", None):
            raise HTTPException(
                status_code=400, detail="informe a connection string do Azure"
            )
    if tipo == "rclone":
        if not dados.rclone_remote:
            raise HTTPException(
                status_code=400,
                detail="informe o nome do remote (o que está entre colchetes no rclone.conf)",
            )


@router.get("", response_model=list[DestinoOut])
async def listar(
    _: User = Depends(require_permission("backups.view")),
    db: AsyncSession = Depends(get_db),
):
    resultado = await db.execute(select(Destino).order_by(Destino.nome))
    return [_para_out(d) for d in resultado.scalars().all()]


@router.post("", response_model=DestinoOut, status_code=201)
async def criar(
    dados: DestinoIn,
    request: Request,
    autor: User = Depends(require_permission("destinations.manage")),
    db: AsyncSession = Depends(get_db),
):
    existente = await db.execute(select(Destino).where(Destino.nome == dados.nome))
    if existente.scalars().first() is not None:
        raise HTTPException(status_code=409, detail=f"já existe destino '{dados.nome}'")

    _validar_por_tipo(dados.tipo, dados)

    if dados.tipo == "rclone" and not dados.rclone_conf:
        raise HTTPException(
            status_code=400,
            detail=(
                "cole o bloco de configuração do rclone. Gere com `rclone config` "
                "numa máquina qualquer e copie a seção do remote, incluindo a "
                "linha entre colchetes."
            ),
        )

    segredo = dados.azure_conn or dados.rclone_conf or ""

    destino = Destino(
        nome=dados.nome,
        descricao=dados.descricao,
        tipo=dados.tipo,
        enabled=dados.enabled,
        padrao=dados.padrao,
        retencao_dias=dados.retencao_dias,
        caminho=dados.caminho,
        azure_container=dados.azure_container,
        azure_tier=dados.azure_tier or "Cool",
        azure_conn_enc=encrypt_secret(dados.azure_conn or ""),
        rclone_remote=dados.rclone_remote,
        rclone_caminho=dados.rclone_caminho,
        rclone_conf_enc=encrypt_secret(dados.rclone_conf or ""),
        rclone_flags=dados.rclone_flags,
        cred_fingerprint=fingerprint(segredo),
        created_by=autor.username,
    )
    db.add(destino)
    await db.commit()
    await db.refresh(destino)

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="destinations.manage",
        target=destino.nome,
        ip=client_ip(request),
        detail={"acao": "criar", "tipo": destino.tipo},
    )
    return _para_out(destino)


@router.patch("/{destino_id}", response_model=DestinoOut)
async def atualizar(
    destino_id: int,
    dados: DestinoUpdate,
    request: Request,
    autor: User = Depends(require_permission("destinations.manage")),
    db: AsyncSession = Depends(get_db),
):
    destino = await db.get(Destino, destino_id)
    if destino is None:
        raise HTTPException(status_code=404, detail="destino não encontrado")

    alterados: list[str] = []
    for campo in (
        "nome", "descricao", "enabled", "padrao", "retencao_dias", "caminho",
        "azure_container", "azure_tier", "rclone_remote", "rclone_caminho",
        "rclone_flags",
    ):
        valor = getattr(dados, campo)
        if valor is not None and getattr(destino, campo) != valor:
            setattr(destino, campo, valor)
            alterados.append(campo)

    # Segredo em branco significa "manter" — mandar vazio apagaria o que
    # está no cofre. A tela só envia o que foi digitado.
    if dados.azure_conn:
        destino.azure_conn_enc = encrypt_secret(dados.azure_conn)
        destino.cred_fingerprint = fingerprint(dados.azure_conn)
        alterados.append("azure_conn")
    if dados.rclone_conf:
        destino.rclone_conf_enc = encrypt_secret(dados.rclone_conf)
        destino.cred_fingerprint = fingerprint(dados.rclone_conf)
        alterados.append("rclone_conf")

    # Credencial ou caminho mudou: o teste anterior não vale mais
    if alterados:
        destino.last_test_ok = False
        destino.last_test_error = "configuração alterada — teste de novo"

    await db.commit()
    await db.refresh(destino)

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="destinations.manage",
        target=destino.nome,
        ip=client_ip(request),
        detail={"acao": "atualizar", "campos": alterados},
    )
    return _para_out(destino)


@router.delete("/{destino_id}")
async def remover(
    destino_id: int,
    request: Request,
    autor: User = Depends(require_permission("destinations.manage")),
    db: AsyncSession = Depends(get_db),
):
    destino = await db.get(Destino, destino_id)
    if destino is None:
        raise HTTPException(status_code=404, detail="destino não encontrado")

    # Agendamento apontando para um destino removido falharia na hora de
    # rodar, de madrugada. Melhor recusar aqui.
    from app.models.backup import Schedule

    presos = await db.execute(select(Schedule))
    usando = [
        s.name for s in presos.scalars().all()
        if destino_id in (s.destinations or [])
    ]
    if usando:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{destino.nome}' está em uso por: {', '.join(usando)}. "
                "Ajuste esses agendamentos antes de remover."
            ),
        )

    nome = destino.nome
    await db.delete(destino)
    await db.commit()

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="destinations.manage",
        target=nome,
        ip=client_ip(request),
        level="critical",
        detail={"acao": "remover"},
    )
    return {"ok": True}


@router.post("/{destino_id}/testar")
async def testar(
    destino_id: int,
    request: Request,
    autor: User = Depends(require_permission("backups.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Grava um arquivo pequeno no destino, confere e apaga.

    Testar de verdade vale mais que validar credencial: permissão de
    escrita, container inexistente e cota estourada só aparecem na hora
    de gravar — e descobrir isso às 3h, no meio do backup, é a pior hora.
    """
    destino = await db.get(Destino, destino_id)
    if destino is None:
        raise HTTPException(status_code=404, detail="destino não encontrado")

    resultado = await request.app.state.storage.testar(destino)

    destino.last_test_at = datetime.now(timezone.utc)
    destino.last_test_ok = bool(resultado.get("ok"))
    destino.last_test_error = "" if resultado.get("ok") else str(resultado.get("detalhe", ""))[:2000]
    await db.commit()

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="destinations.testar",
        target=destino.nome,
        ip=client_ip(request),
        success=destino.last_test_ok,
        detail={"tipo": destino.tipo, "detalhe": str(resultado.get("detalhe", ""))[:400]},
    )
    return resultado
