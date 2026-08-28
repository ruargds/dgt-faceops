"""
Log ao vivo e visões salvas.

O WebSocket usa o mesmo ticket de uso único do InTerminal: o navegador não
permite cabeçalho `Authorization` ao abrir WebSocket, e `?token=<jwt>` na
URL grava o token no log de acesso do nginx.
"""
import asyncio
import json
import logging
import secrets
import time

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_permission
from app.db.database import AsyncSessionLocal, get_db
from app.models.host import Host
from app.models.user import User
from app.models.visao_log import VisaoLog
from app.services import audit_service
from app.services.logs_service import LogError, SessaoLog, listar_containers
from app.services.ssh_service import SSHError

log = logging.getLogger("faceops.logs")

router = APIRouter(prefix="/api/logs", tags=["logs"])

TICKET_TTL = 30
_tickets: dict[str, dict] = {}


def _limpar_tickets() -> None:
    agora = time.monotonic()
    for chave in [t for t, d in _tickets.items() if d["expira_em"] < agora]:
        _tickets.pop(chave, None)


# ── Schemas ────────────────────────────────────────────────────────────


class CampoIn(BaseModel):
    caminho: str = Field(min_length=1, max_length=120)
    rotulo: str = ""
    corte_inicio: int | None = None
    corte_fim: int | None = None


class VisaoIn(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    descricao: str = ""
    host_id: int | None = None
    container: str = ""
    tail: int = Field(default=200, ge=0, le=5000)
    campos: list[CampoIn] = []
    exigir_campos: list[str] = []
    filtro: str = ""
    destacar: str = ""
    mostrar_nao_json: bool = True


class VisaoUpdate(BaseModel):
    nome: str | None = None
    descricao: str | None = None
    host_id: int | None = None
    container: str | None = None
    tail: int | None = Field(default=None, ge=0, le=5000)
    campos: list[CampoIn] | None = None
    exigir_campos: list[str] | None = None
    filtro: str | None = None
    destacar: str | None = None
    mostrar_nao_json: bool | None = None


# ── Containers disponíveis ─────────────────────────────────────────────


@router.get("/containers/{host_id}")
async def containers(
    host_id: int,
    request: Request,
    _: User = Depends(require_permission("services.view")),
    db: AsyncSession = Depends(get_db),
):
    """Todos os containers do host, agrupados por projeto compose."""
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")
    if not host.enabled:
        raise HTTPException(status_code=400, detail=f"'{host.name}' está desativado")
    try:
        return await listar_containers(request.app.state.ssh, host)
    except SSHError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Visões salvas ──────────────────────────────────────────────────────


@router.get("/visoes")
async def listar_visoes(
    _: User = Depends(require_permission("services.view")),
    db: AsyncSession = Depends(get_db),
):
    resultado = await db.execute(select(VisaoLog).order_by(VisaoLog.nome))
    return [
        {
            "id": v.id, "nome": v.nome, "descricao": v.descricao,
            "host_id": v.host_id, "container": v.container, "tail": v.tail,
            "campos": v.campos, "exigir_campos": v.exigir_campos,
            "filtro": v.filtro, "destacar": v.destacar,
            "mostrar_nao_json": v.mostrar_nao_json, "created_by": v.created_by,
        }
        for v in resultado.scalars().all()
    ]


@router.post("/visoes", status_code=201)
async def criar_visao(
    dados: VisaoIn,
    request: Request,
    autor: User = Depends(require_permission("services.view")),
    db: AsyncSession = Depends(get_db),
):
    existente = await db.execute(select(VisaoLog).where(VisaoLog.nome == dados.nome))
    if existente.scalars().first() is not None:
        raise HTTPException(status_code=409, detail=f"já existe visão '{dados.nome}'")

    visao = VisaoLog(
        nome=dados.nome,
        descricao=dados.descricao,
        host_id=dados.host_id,
        container=dados.container,
        tail=dados.tail,
        campos=[c.model_dump() for c in dados.campos],
        exigir_campos=dados.exigir_campos,
        filtro=dados.filtro,
        destacar=dados.destacar,
        mostrar_nao_json=dados.mostrar_nao_json,
        created_by=autor.username,
    )
    db.add(visao)
    await db.commit()
    await db.refresh(visao)

    await audit_service.registrar(
        db, usuario=autor.username, action="logs.visao",
        target=visao.nome, ip=client_ip(request),
        detail={"acao": "criar", "container": visao.container},
    )
    return {"id": visao.id, "nome": visao.nome}


@router.patch("/visoes/{visao_id}")
async def atualizar_visao(
    visao_id: int,
    dados: VisaoUpdate,
    autor: User = Depends(require_permission("services.view")),
    db: AsyncSession = Depends(get_db),
):
    visao = await db.get(VisaoLog, visao_id)
    if visao is None:
        raise HTTPException(status_code=404, detail="visão não encontrada")

    for campo in (
        "nome", "descricao", "host_id", "container", "tail",
        "exigir_campos", "filtro", "destacar", "mostrar_nao_json",
    ):
        valor = getattr(dados, campo)
        if valor is not None:
            setattr(visao, campo, valor)
    if dados.campos is not None:
        visao.campos = [c.model_dump() for c in dados.campos]

    await db.commit()
    return {"ok": True}


@router.delete("/visoes/{visao_id}")
async def remover_visao(
    visao_id: int,
    autor: User = Depends(require_permission("services.view")),
    db: AsyncSession = Depends(get_db),
):
    visao = await db.get(VisaoLog, visao_id)
    if visao is None:
        raise HTTPException(status_code=404, detail="visão não encontrada")
    await db.delete(visao)
    await db.commit()
    return {"ok": True}


# ── Stream ao vivo ─────────────────────────────────────────────────────


@router.post("/ticket/{host_id}")
async def emitir_ticket(
    host_id: int,
    container: str = Query(..., min_length=1, max_length=128),
    tail: int = Query(default=200, ge=0, le=5000),
    request: Request = None,
    autor: User = Depends(require_permission("services.view")),
    db: AsyncSession = Depends(get_db),
):
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")
    if not host.enabled:
        raise HTTPException(status_code=400, detail=f"'{host.name}' está desativado")

    _limpar_tickets()
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = {
        "usuario": autor.username,
        "host_id": host_id,
        "container": container,
        "tail": tail,
        "ip": client_ip(request) if request else "",
        "expira_em": time.monotonic() + TICKET_TTL,
    }
    return {"ticket": ticket, "expira_em_s": TICKET_TTL}


@router.websocket("/ws")
async def logs_ws(websocket: WebSocket, ticket: str = Query(...)):
    """
    Transmite `docker logs -f` linha a linha.

    Protocolo (JSON em texto):
      servidor -> {"tipo":"pronto","host":…,"container":…}
                  {"tipo":"linha","dados":"…"}
                  {"tipo":"descartadas","n":123}
                  {"tipo":"fim","motivo":"…"} | {"tipo":"erro","mensagem":"…"}
      cliente  -> {"tipo":"ping"}
    """
    _limpar_tickets()
    info = _tickets.pop(ticket, None)  # uso único
    await websocket.accept()

    if info is None or info["expira_em"] < time.monotonic():
        await websocket.send_text(json.dumps({
            "tipo": "erro",
            "mensagem": "Ticket inválido ou expirado. Abra o log novamente.",
        }))
        await websocket.close(code=4401)
        return

    async with AsyncSessionLocal() as db:
        host = await db.get(Host, info["host_id"])
        if host is None:
            await websocket.send_text(json.dumps({
                "tipo": "erro", "mensagem": "Servidor não encontrado.",
            }))
            await websocket.close(code=4404)
            return

        sessao = SessaoLog(
            host=host,
            container=info["container"],
            usuario=info["usuario"],
            ip=info["ip"],
            tail=info["tail"],
        )

        try:
            precisa_sudo = await websocket.app.state.ssh.docker_needs_sudo(host)
            await sessao.abrir(precisa_sudo)
        except (SSHError, LogError) as exc:
            await websocket.send_text(json.dumps({"tipo": "erro", "mensagem": str(exc)}))
            await websocket.close(code=4500)
            return

        chave = f"{info['usuario']}@{host.name}:{info['container']}:{time.time():.0f}"
        gerenciador = websocket.app.state.logs
        gerenciador.registrar(chave, sessao)

        await audit_service.registrar(
            db, usuario=info["usuario"], action="logs.stream",
            target=f"{host.name}/{info['container']}", ip=info["ip"],
            detail={"tail": info["tail"]},
        )

        await websocket.send_text(json.dumps({
            "tipo": "pronto", "host": host.name, "container": info["container"],
        }))

        motivo = "encerrado"
        ultimo_aviso = 0

        async def _bombear() -> None:
            nonlocal motivo, ultimo_aviso
            while True:
                linha = await sessao.ler()
                if linha is None:
                    motivo = "stream encerrado"
                    break
                if linha == "":
                    # Descartada pelo limite de taxa. Avisa no máximo a
                    # cada 2s para o próprio aviso não virar enxurrada.
                    agora = time.monotonic()
                    if agora - ultimo_aviso > 2:
                        ultimo_aviso = agora
                        await websocket.send_text(json.dumps({
                            "tipo": "descartadas", "n": sessao.linhas_descartadas,
                        }))
                    continue
                await websocket.send_text(json.dumps({"tipo": "linha", "dados": linha}))

        async def _receber() -> None:
            while True:
                bruto = await websocket.receive_text()
                try:
                    msg = json.loads(bruto)
                except json.JSONDecodeError:
                    continue
                if msg.get("tipo") == "ping":
                    await websocket.send_text(json.dumps({"tipo": "pong"}))

        tarefas = [asyncio.create_task(_bombear()), asyncio.create_task(_receber())]
        try:
            await asyncio.wait(tarefas, return_when=asyncio.FIRST_COMPLETED)
        except WebSocketDisconnect:
            motivo = "navegador desconectou"
        except Exception as exc:
            motivo = f"erro: {type(exc).__name__}"
            log.exception("erro no stream de log %s", chave)
        finally:
            for t in tarefas:
                t.cancel()
            await asyncio.gather(*tarefas, return_exceptions=True)
            await sessao.fechar()
            gerenciador.remover(chave)
            try:
                await websocket.send_text(json.dumps({"tipo": "fim", "motivo": motivo}))
                await websocket.close()
            except Exception:
                pass


@router.get("/ativos")
async def streams_ativos(
    request: Request,
    _: User = Depends(require_permission("services.view")),
):
    return request.app.state.logs.ativas()
