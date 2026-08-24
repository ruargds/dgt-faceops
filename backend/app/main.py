"""
DGT FaceOps — painel de operação do FindFace Multi.

Backup com recorrência programada, status e reinício de serviços, leitura
de RAM/GPU/disco e terminal SSH pelo navegador (InTerminal), para os
servidores NtechLab que a plataforma web nativa não cobre.
"""
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.routes import (
    audit, auth, backups, destinos, hosts, maintenance, ops, terminal,
)
from app.core.config import settings
from app.core.security import hash_password
from app.db.database import AsyncSessionLocal, Base, engine
from app.models import Destino, User  # noqa: F401 — registra todos os modelos
from app.services.backup_service import BackupService
from app.services.maintenance_service import MaintenanceService
from app.services.metrics_service import MetricsService
from app.services.scheduler_service import SchedulerService
from app.services.ssh_service import SSHService
from app.services.stack_service import StackService
from app.services.storage_service import StorageService
from app.services.terminal_service import TerminalManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
)
log = logging.getLogger("faceops")


async def _criar_tabelas() -> None:
    async with engine.begin() as conexao:
        await conexao.run_sync(Base.metadata.create_all)


async def _semear_admin() -> None:
    """Cria o admin inicial se o banco estiver vazio."""
    async with AsyncSessionLocal() as db:
        resultado = await db.execute(select(User).limit(1))
        if resultado.scalars().first() is not None:
            return

        usuario = User(
            username=settings.ADMIN_USER.strip().lower(),
            full_name="Administrador",
            hashed_password=hash_password(settings.ADMIN_PASSWORD),
            role="admin",
            is_active=True,
            is_super_admin=True,
            # Marca a senha de fábrica para a UI avisar até ser trocada
            senha_padrao=(settings.ADMIN_PASSWORD == "admin123"),
        )
        db.add(usuario)
        await db.commit()

        log.warning(
            "admin inicial criado: usuario='%s'. TROQUE A SENHA no primeiro acesso.",
            usuario.username,
        )


async def _semear_destino_local() -> None:
    """
    Cria o destino local padrão se não houver nenhum destino.

    Sem isto, um painel recém-instalado aceitaria disparar backup e
    falharia no fim, depois de já ter copiado o artefato do servidor —
    o pior momento possível para descobrir que falta configuração.
    """
    async with AsyncSessionLocal() as db:
        resultado = await db.execute(select(Destino).limit(1))
        if resultado.scalars().first() is not None:
            return

        db.add(Destino(
            nome="Disco do painel",
            descricao="Destino local criado automaticamente na primeira subida",
            tipo="local",
            caminho=settings.LOCAL_BACKUP_DIR,
            enabled=True,
            padrao=True,
            retencao_dias=settings.RETENTION_ESSENCIAL_DAYS,
            created_by="sistema",
        ))
        await db.commit()
        log.info("destino local padrao criado em %s", settings.LOCAL_BACKUP_DIR)


async def _varredor_de_ociosas(app: FastAPI) -> None:
    """Derruba sessões de terminal esquecidas abertas."""
    while True:
        try:
            await asyncio.sleep(60)
            encerradas = await app.state.terminals.varrer_ociosas()
            if encerradas:
                log.info("%d sessao(oes) de terminal encerradas por inatividade", encerradas)
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("erro no varredor de sessoes ociosas")


app = FastAPI(
    title="DGT FaceOps",
    description=(
        "Painel de operação do FindFace Multi 2.4.1 — backup com recorrência, "
        "serviços, recursos e terminal SSH."
    ),
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# O nginx serve o front na mesma origem; CORS aberto só ajudaria em
# desenvolvimento, então fica restrito ao servidor de dev do React.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def iniciar() -> None:
    await _criar_tabelas()
    await _semear_admin()
    await _semear_destino_local()

    ssh = SSHService()
    storage = StorageService()

    app.state.ssh = ssh
    app.state.storage = storage
    app.state.metrics = MetricsService(ssh)
    app.state.stack = StackService(ssh)
    app.state.manutencao = MaintenanceService(ssh)
    app.state.backups = BackupService(ssh, storage)
    app.state.terminals = TerminalManager()
    app.state.scheduler = SchedulerService(app.state.backups)

    await app.state.scheduler.start()
    app.state.tarefa_varredura = asyncio.create_task(_varredor_de_ociosas(app))

    log.info("DGT FaceOps pronto — fuso %s", settings.TZ)


@app.on_event("shutdown")
async def encerrar() -> None:
    tarefa = getattr(app.state, "tarefa_varredura", None)
    if tarefa is not None:
        tarefa.cancel()

    if hasattr(app.state, "scheduler"):
        await app.state.scheduler.shutdown()
    if hasattr(app.state, "terminals"):
        await app.state.terminals.encerrar_todas()
    if hasattr(app.state, "ssh"):
        await app.state.ssh.close_all()

    log.info("DGT FaceOps encerrado")


@app.exception_handler(ValueError)
async def erro_de_valor(_, exc: ValueError):
    """
    ValueError vindo do cofre significa SECRET_KEY trocada. Devolver 500
    genérico faria o operador caçar problema de rede por horas.
    """
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/api/saude")
async def saude():
    """Health check do painel — usado pelo healthcheck do container."""
    return {
        "ok": True,
        "servico": "dgt-faceops",
        "versao": app.version,
        "agendamentos": len(app.state.scheduler.scheduler.get_jobs())
        if hasattr(app.state, "scheduler") else 0,
        "terminais_ativos": len(app.state.terminals.ativas())
        if hasattr(app.state, "terminals") else 0,
    }


app.include_router(auth.router)
app.include_router(hosts.router)
app.include_router(ops.router)
app.include_router(backups.router)
app.include_router(destinos.router)
app.include_router(maintenance.router)
app.include_router(terminal.router)
app.include_router(audit.router)
