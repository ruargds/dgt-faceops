"""
InTerminal — terminal SSH pelo navegador.

Autenticação por **ticket de uso único**, não pelo JWT na URL. O navegador
não deixa mandar cabeçalho `Authorization` ao abrir um WebSocket, e o
caminho fácil (`?token=<jwt>`) grava o token no log de acesso do nginx,
no histórico do navegador e em qualquer proxy no meio. O ticket vale 30
segundos, serve uma vez só e não abre nada além daquele host.
"""
import asyncio
import json
import logging
import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_permission
from app.core.permissions import permissions_for
from app.db.database import AsyncSessionLocal, get_db
from app.models.audit import TerminalSession
from app.models.host import Host
from app.models.user import User
from app.schemas import CredencialTerminalIn, SessaoTerminalOut
from app.services import audit_service
from app.services.ssh_service import SSHError
from app.services.terminal_service import SessaoTerminal

log = logging.getLogger("faceops.terminal")

router = APIRouter(prefix="/api/terminal", tags=["terminal"])

TICKET_TTL = 30  # segundos

# ticket -> {usuario, host_id, permite_sudo, ssh_usuario, ssh_senha, expira_em}
#
# A senha da sessão mora AQUI e em nenhum outro lugar: memória do processo,
# 30 segundos, um uso. Não vai para o banco, para a auditoria nem para a
# gravação da sessão.
_tickets: dict[str, dict] = {}


def _limpar_tickets() -> None:
    agora = time.monotonic()
    for chave in [t for t, d in _tickets.items() if d["expira_em"] < agora]:
        _tickets.pop(chave, None)


@router.post("/ticket/{host_id}")
async def emitir_ticket(
    host_id: int,
    request: Request,
    credencial: CredencialTerminalIn | None = None,
    autor: User = Depends(require_permission("terminal.use")),
    db: AsyncSession = Depends(get_db),
):
    """
    Emite o ticket que o WebSocket vai consumir.

    Aceita o login e a senha digitados na tela para ESTA sessão — quem
    opera entra com a própria conta no servidor, como faria no PuTTY, em
    vez de herdar a conta de serviço do painel. Senha vazia mantém o
    comportamento antigo: credencial do cofre.
    """
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")
    if not host.enabled:
        raise HTTPException(status_code=400, detail=f"servidor '{host.name}' está desativado")

    ssh_usuario = (credencial.usuario if credencial else "").strip() or host.ssh_user
    ssh_senha = credencial.senha if credencial else ""

    _limpar_tickets()
    ticket = secrets.token_urlsafe(32)
    permissoes = permissions_for(autor.role, autor.is_super_admin)
    _tickets[ticket] = {
        "usuario": autor.username,
        "host_id": host_id,
        "permite_sudo": "terminal.sudo" in permissoes,
        "ip": client_ip(request),
        "ssh_usuario": ssh_usuario,
        "ssh_senha": ssh_senha,
        "expira_em": time.monotonic() + TICKET_TTL,
    }
    return {
        "ticket": ticket,
        "expira_em_s": TICKET_TTL,
        "host": host.name,
        "usuario_ssh": ssh_usuario,
    }


@router.websocket("/ws")
async def terminal_ws(
    websocket: WebSocket,
    ticket: str = Query(...),
    colunas: int = Query(default=120, ge=20, le=500),
    linhas: int = Query(default=32, ge=5, le=200),
):
    """
    Ponte WebSocket ↔ PTY.

    Protocolo (JSON em texto nos dois sentidos):
      cliente -> {"tipo":"in","dados":"ls\\n"} | {"tipo":"resize","colunas":..,"linhas":..}
      servidor -> {"tipo":"out","dados":"..."} | {"tipo":"fim","motivo":"..."}
                  {"tipo":"erro","mensagem":"..."}
    """
    _limpar_tickets()
    info = _tickets.pop(ticket, None)  # uso único: some ao ser consumido

    await websocket.accept()

    if info is None or info["expira_em"] < time.monotonic():
        await websocket.send_text(json.dumps({
            "tipo": "erro",
            "mensagem": "Ticket inválido ou expirado. Abra o terminal novamente.",
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

        sessao = SessaoTerminal(
            host=host,
            usuario=info["usuario"],
            ip=info["ip"],
            permite_sudo=info["permite_sudo"],
            ssh_usuario=info.get("ssh_usuario", ""),
            ssh_senha=info.get("ssh_senha", ""),
        )
        # O ticket já foi consumido; a senha não tem mais motivo para
        # continuar no dicionário enquanto a sessão vive.
        info["ssh_senha"] = ""

        try:
            await sessao.abrir(colunas, linhas)
        except SSHError as exc:
            await websocket.send_text(json.dumps({
                "tipo": "erro", "mensagem": str(exc),
            }))
            await websocket.close(code=4500)
            await audit_service.registrar(
                db,
                usuario=info["usuario"],
                action="terminal.open",
                target=host.name,
                ip=info["ip"],
                success=False,
                detail={"erro": str(exc)[:400], "login_ssh": sessao.login},
            )
            return

        registro = TerminalSession(
            host_id=host.id,
            usuario=info["usuario"],
            ip=info["ip"],
            recording_path=sessao.caminho_gravacao,
        )
        db.add(registro)
        await db.commit()
        await db.refresh(registro)

        await audit_service.registrar(
            db,
            usuario=info["usuario"],
            action="terminal.open",
            target=host.name,
            ip=info["ip"],
            detail={
                "sessao_id": registro.id,
                "gravacao": bool(sessao.caminho_gravacao),
                # Sob que conta a pessoa entrou no servidor, e se usou a
                # credencial dela ou a do cofre. Nunca a senha.
                "login_ssh": sessao.login,
                "credencial": "sessao" if sessao.credencial_propria else "cofre",
            },
        )

        chave = f"{info['usuario']}@{host.name}:{registro.id}"
        gerenciador = websocket.app.state.terminals
        gerenciador.registrar(chave, sessao)

        await websocket.send_text(json.dumps({
            "tipo": "pronto",
            "host": host.name,
            "usuario_ssh": sessao.login,
            "sudo": info["permite_sudo"],
            "gravando": bool(sessao.caminho_gravacao),
        }))

        motivo = "encerrada"

        async def _do_shell_para_navegador() -> None:
            nonlocal motivo
            while True:
                dados = await sessao.ler()
                if dados is None:
                    motivo = "shell encerrado"
                    break
                await websocket.send_text(json.dumps({"tipo": "out", "dados": dados}))

        async def _do_navegador_para_shell() -> None:
            while True:
                bruto = await websocket.receive_text()
                try:
                    msg = json.loads(bruto)
                except json.JSONDecodeError:
                    continue

                tipo = msg.get("tipo")
                if tipo == "in":
                    await sessao.escrever(msg.get("dados", ""))
                elif tipo == "resize":
                    await sessao.redimensionar(
                        msg.get("colunas", 120), msg.get("linhas", 32)
                    )
                elif tipo == "ping":
                    await websocket.send_text(json.dumps({"tipo": "pong"}))

        tarefas = [
            asyncio.create_task(_do_shell_para_navegador()),
            asyncio.create_task(_do_navegador_para_shell()),
        ]
        try:
            # A primeira que terminar encerra a sessão: shell fechado ou
            # navegador desconectado, tanto faz — não deixar PTY órfão.
            await asyncio.wait(tarefas, return_when=asyncio.FIRST_COMPLETED)
        except WebSocketDisconnect:
            motivo = "navegador desconectou"
        except Exception as exc:
            motivo = f"erro: {type(exc).__name__}"
            log.exception("erro na sessão de terminal %s", chave)
        finally:
            for tarefa in tarefas:
                tarefa.cancel()
            await asyncio.gather(*tarefas, return_exceptions=True)

            await sessao.fechar(motivo)
            gerenciador.remover(chave)

            registro.ended_at = datetime.now(timezone.utc)
            registro.bytes_in = sessao.bytes_in
            registro.bytes_out = sessao.bytes_out
            registro.sudo_used = sessao.sudo_usado
            registro.end_reason = motivo[:64]
            await db.commit()

            await audit_service.registrar(
                db,
                usuario=info["usuario"],
                action="terminal.close",
                target=host.name,
                ip=info["ip"],
                detail={
                    "sessao_id": registro.id,
                    "motivo": motivo,
                    "bytes_enviados": sessao.bytes_in,
                    "bytes_recebidos": sessao.bytes_out,
                    "usou_sudo": sessao.sudo_usado,
                },
            )

            try:
                await websocket.send_text(json.dumps({"tipo": "fim", "motivo": motivo}))
                await websocket.close()
            except Exception:
                pass


@router.get("/ativas")
async def sessoes_ativas(
    request: Request,
    _: User = Depends(require_permission("terminal.sessions.view")),
):
    """Quem está com terminal aberto agora."""
    return request.app.state.terminals.ativas()


@router.get("/sessoes", response_model=list[SessaoTerminalOut])
async def historico_sessoes(
    host_id: int | None = Query(default=None),
    limite: int = Query(default=100, ge=1, le=500),
    _: User = Depends(require_permission("terminal.sessions.view")),
    db: AsyncSession = Depends(get_db),
):
    consulta = (
        select(TerminalSession)
        .order_by(TerminalSession.started_at.desc())
        .limit(limite)
    )
    if host_id is not None:
        consulta = consulta.where(TerminalSession.host_id == host_id)

    resultado = await db.execute(consulta)
    nomes_res = await db.execute(select(Host.id, Host.name))
    nomes = {linha[0]: linha[1] for linha in nomes_res.all()}

    saida: list[SessaoTerminalOut] = []
    for sessao in resultado.scalars().all():
        item = SessaoTerminalOut.model_validate(sessao)
        item.host_nome = nomes.get(sessao.host_id, "?")
        saida.append(item)
    return saida


@router.get("/sessoes/{sessao_id}/gravacao")
async def baixar_gravacao(
    sessao_id: int,
    _: User = Depends(require_permission("terminal.sessions.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Baixa a gravação em asciicast v2.

    Reproduzir com:  asciinema play arquivo.cast
    """
    from pathlib import Path

    from fastapi.responses import FileResponse

    from app.core.config import settings

    sessao = await db.get(TerminalSession, sessao_id)
    if sessao is None or not sessao.recording_path:
        raise HTTPException(status_code=404, detail="gravação não encontrada")

    caminho = Path(sessao.recording_path).resolve()
    base = Path(settings.TERMINAL_SESSION_DIR).resolve()
    # O caminho vem do banco, mas conferir mesmo assim: uma linha adulterada
    # não pode virar leitura de arquivo arbitrário.
    if not str(caminho).startswith(str(base)) or not caminho.is_file():
        raise HTTPException(status_code=404, detail="arquivo de gravação indisponível")

    return FileResponse(
        path=str(caminho), filename=caminho.name, media_type="application/x-asciicast"
    )
