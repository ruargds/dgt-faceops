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
    audit, auth, backups, configuracoes, descoberta, dispositivos, destinos,
    exportar, hosts, logs, maintenance, marca, monitor, ops, processos, terminal,
)
from app.core.config import settings
from app.core.security import hash_password
from app.db.database import AsyncSessionLocal, Base, engine
from app.models import (  # noqa: F401 — registra todos os modelos
    Amostra, Destino, LicencaAmostra, User, VisaoLog,
)
from app.services.backup_service import BackupService
from app.services.config_service import ConfigService
from app.services.descoberta_service import DescobertaService
from app.services.dispositivos_service import DispositivosService
from app.services.faxina_service import FaxinaService
from app.services.estimativa_service import EstimativaService
from app.services.ffapi_service import FFApiService
from app.services.configff_service import ConfigFFService
from app.services.licenca_service import LicencaService
from app.services.internos_service import InternosService
from app.services.limpeza_service import LimpezaService
from app.services.painel_backup_service import PainelBackupService
from app.services.rastreio_service import RastreioService
from app.services.processos_service import ProcessosService
from app.services.logs_service import LogManager
from app.services.maintenance_service import MaintenanceService
from app.services.metrics_service import MetricsService
from app.services.monitor_service import MonitorService
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


# Colunas acrescentadas depois da primeira versão. `create_all` cria
# tabela que falta, mas NÃO acrescenta coluna em tabela que já existe —
# um painel atualizado quebraria com "column does not exist".
#
# Alembic seria o caminho formal, mas para um punhado de colunas ele
# custa mais do que resolve: mais uma dependência, mais um passo no
# deploy, e um diretório de migrações para manter. `ADD COLUMN IF NOT
# EXISTS` é idempotente e roda em milissegundos.
COLUNAS_NOVAS = [
    ("users", "senha_padrao", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("users", "token_version", "INTEGER NOT NULL DEFAULT 1"),
    ("hosts", "host_key_pub", "TEXT NOT NULL DEFAULT ''"),
    ("hosts", "monitorar", "BOOLEAN NOT NULL DEFAULT TRUE"),
    ("hosts", "ff_api_url", "VARCHAR(255) NOT NULL DEFAULT ''"),
    ("hosts", "ff_api_token_enc", "TEXT NOT NULL DEFAULT ''"),
    ("amostras", "cpu_uso_pct", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
    ("hosts", "ff_api_user", "VARCHAR(120) NOT NULL DEFAULT ''"),
    ("hosts", "ff_api_pass_enc", "TEXT NOT NULL DEFAULT ''"),
    ("schedules", "tipo", "VARCHAR(16) NOT NULL DEFAULT 'backup'"),
    ("schedules", "parametros", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
]

# Alterações que não são "coluna nova". Escritas para serem idempotentes:
# rodar duas vezes não muda nada e não levanta erro.
ALTERACOES = [
    # O backup do painel não tem servidor de origem
    "ALTER TABLE backup_runs ALTER COLUMN host_id DROP NOT NULL",
    "ALTER TABLE schedules ALTER COLUMN host_id DROP NOT NULL",
]


async def _criar_tabelas() -> None:
    from sqlalchemy import text

    async with engine.begin() as conexao:
        await conexao.run_sync(Base.metadata.create_all)

    async with engine.begin() as conexao:
        for tabela, coluna, tipo in COLUNAS_NOVAS:
            try:
                await conexao.execute(
                    text(f"ALTER TABLE {tabela} ADD COLUMN IF NOT EXISTS {coluna} {tipo}")
                )
            except Exception as exc:
                # Tabela pode não existir ainda numa instalação parcial —
                # não pode impedir a subida.
                log.warning("coluna %s.%s: %s", tabela, coluna, exc)

    for comando in ALTERACOES:
        try:
            async with engine.begin() as conexao:
                await conexao.execute(text(comando))
        except Exception as exc:
            log.debug("alteracao ja aplicada ou nao aplicavel: %s (%s)", comando, exc)


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


async def _semear_visao_log() -> None:
    """
    Cria a visão de exemplo se não houver nenhuma.

    É o `docker logs -f | jq -r '…'` que a equipe já usava no shell,
    virando configuração compartilhada — serve de modelo para as próximas.
    """
    async with AsyncSessionLocal() as db:
        resultado = await db.execute(select(VisaoLog).limit(1))
        if resultado.scalars().first() is not None:
            return

        db.add(VisaoLog(
            nome="Integração — matches",
            descricao=(
                "Acompanha o consumidor de matches: hora, trace, dgtId, "
                "cartão e mensagem. Equivale ao jq que era rodado no shell."
            ),
            container="macthes-faces-consumer-1",
            tail=0,
            campos=[
                {"caminho": "timestamp", "rotulo": "hora",
                 "corte_inicio": 11, "corte_fim": 19},
                {"caminho": "trace_id", "rotulo": "trace",
                 "corte_inicio": None, "corte_fim": None},
                {"caminho": "context.dgtId", "rotulo": "dgtId",
                 "corte_inicio": None, "corte_fim": None},
                {"caminho": "context.matchedCardName", "rotulo": "cartão",
                 "corte_inicio": None, "corte_fim": None},
                {"caminho": "message", "rotulo": "mensagem",
                 "corte_inicio": None, "corte_fim": None},
            ],
            exigir_campos=["trace_id"],
            destacar="error|exception|timeout|refused|failed",
            mostrar_nao_json=False,
            created_by="sistema",
        ))
        await db.commit()
        log.info("visao de log de exemplo criada")


async def _varredor_de_ociosas(app: FastAPI) -> None:
    """Derruba sessões de terminal esquecidas abertas."""
    while True:
        try:
            await asyncio.sleep(60)
            encerradas = await app.state.terminals.varrer_ociosas()
            if encerradas:
                log.info("%d sessao(oes) de terminal encerradas por inatividade", encerradas)
            logs_fechados = await app.state.logs.varrer_ociosas()
            if logs_fechados:
                log.info("%d stream(s) de log encerrado(s) por inatividade", logs_fechados)
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("erro no varredor de sessoes ociosas")


# Documentação interativa só quando MODO_DEV está ligado. Exposta na
# internet, /api/docs e /api/openapi.json entregariam o mapa completo da
# API a qualquer um — não é vazamento de segredo, mas é reconhecimento de
# graça para quem procura o que atacar.
app = FastAPI(
    title="DGT FaceOps",
    description=(
        "Painel de operação do FindFace Multi 2.4.1 — backup com recorrência, "
        "serviços, recursos e terminal SSH."
    ),
    version="0.1.0",
    docs_url="/api/docs" if settings.MODO_DEV else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.MODO_DEV else None,
)

# O nginx serve o front na mesma origem, então em produção não há CORS a
# liberar. O CORS de localhost serve só ao servidor de dev do React e sai
# de cena quando MODO_DEV está desligado.
if settings.MODO_DEV:
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
    await _semear_visao_log()

    ssh = SSHService()
    storage = StorageService()

    # A configuracao carrega ANTES dos servicos: eles a recebem no
    # construtor e consultam em caminho quente (cada backup, cada stream).
    config = ConfigService()
    async with AsyncSessionLocal() as db:
        await config.carregar(db)
    app.state.config = config

    app.state.ssh = ssh
    app.state.storage = storage
    app.state.metrics = MetricsService(ssh)
    app.state.limpeza = LimpezaService(ssh)
    app.state.ffapi = FFApiService()
    # Licenca lida de dentro do servidor, por SSH. Preferida sobre a API:
    # o painel ja tem SSH em todos os hosts, e o NTLS atende localhost sem
    # pedir login -- uma credencial a menos para alguem errar.
    app.state.licenca = LicencaService(ssh)
    # Chaves do FindFace que so existem em arquivo (CLEANUP_SCHEDULE,
    # vms_cleanup). Lista fechada, copia antes, compila depois.
    app.state.configff = ConfigFFService(ssh)
    # Estado dos componentes internos do FindFace, pelas portas que o
    # manual documenta -- de dentro do servidor, sem agente instalado.
    app.state.internos = InternosService(ssh)
    # Quanto vai ocupar, antes de disparar: mede no servidor e cruza
    # com o tamanho real das execucoes anteriores.
    app.state.estimativa = EstimativaService(ssh)
    # Rastreio: junta licenca, componentes, disco, backup e seguranca num
    # so lugar e devolve achados com evidencia, impacto e acao.
    app.state.rastreio = RastreioService(
        app.state.licenca, app.state.internos, app.state.ffapi, config
    )
    app.state.dispositivos = DispositivosService(ssh, ffapi=app.state.ffapi)
    app.state.descoberta = DescobertaService(ssh)
    app.state.processos = ProcessosService(ssh)
    app.state.stack = StackService(ssh, config, limpeza=app.state.limpeza)
    app.state.manutencao = MaintenanceService(ssh)
    app.state.backups = BackupService(ssh, storage, config, stack=app.state.stack)
    app.state.terminals = TerminalManager()
    app.state.logs = LogManager()
    app.state.faxina = FaxinaService(config)
    app.state.painel_backup = PainelBackupService(storage, config)
    app.state.monitor = MonitorService(app.state.metrics, app.state.stack, config)
    app.state.scheduler = SchedulerService(
        app.state.backups,
        faxina=app.state.faxina,
        config=config,
        # Limpeza agendada: o agendador precisa do serviço de limpeza e do
        # de stack (para achar o container legacy do FindFace).
        limpeza=app.state.limpeza,
        stack=app.state.stack,
    )

    # Execução que ficou em "executando" quando o painel reiniciou está
    # morta: a tarefa vivia no processo que saiu. Deixá-la presa faria o
    # /api/saude reportar ocupado para sempre — e o atualizar.sh adiaria
    # atualização por causa de um backup que não existe mais.
    try:
        from sqlalchemy import update as _update

        # `BackupRun` precisa ser importado AQUI: no topo o modulo so
        # importa `app.models` para registrar as tabelas, e o nome nao fica
        # disponivel. Sem este import, o bloco levantava NameError -- que o
        # `except Exception` engolia, e a varredura de orfas nunca rodou.
        from app.models.backup import BackupRun as _BackupRun

        async with AsyncSessionLocal() as db_limpeza:
            resultado = await db_limpeza.execute(
                _update(_BackupRun)
                .where(_BackupRun.status.in_(("executando", "pendente")))
                .values(
                    status="falha",
                    stage="Interrompido",
                    progress=100,
                    error=(
                        "o painel reiniciou durante esta execução; a tarefa "
                        "morreu com o processo anterior"
                    ),
                )
            )
            await db_limpeza.commit()
            if resultado.rowcount:
                log.warning(
                    "%d execucao(oes) de backup orfas marcadas como falha",
                    resultado.rowcount,
                )
    except Exception:
        log.exception("nao consegui limpar execucoes orfas na subida")

    await app.state.scheduler.start()
    await app.state.monitor.iniciar()
    app.state.tarefa_varredura = asyncio.create_task(_varredor_de_ociosas(app))

    log.info("DGT FaceOps pronto — fuso %s", settings.TZ)


@app.on_event("shutdown")
async def encerrar() -> None:
    tarefa = getattr(app.state, "tarefa_varredura", None)
    if tarefa is not None:
        tarefa.cancel()

    if hasattr(app.state, "monitor"):
        await app.state.monitor.parar()
    if hasattr(app.state, "scheduler"):
        await app.state.scheduler.shutdown()
    if hasattr(app.state, "terminals"):
        await app.state.terminals.encerrar_todas()
    if hasattr(app.state, "logs"):
        await app.state.logs.encerrar_todas()
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
    """
    Health check e estado de ocupação.

    O `atualizar.sh` consulta isto ANTES de mexer em qualquer container:
    reiniciar o painel no meio de um backup mata a execução depois de ela
    já ter copiado dezenas de GB do servidor de produção.
    """
    import os

    from sqlalchemy import func, select as _select

    from app.models.backup import BackupRun

    executando = 0
    try:
        async with AsyncSessionLocal() as db:
            r = await db.execute(
                _select(func.count(BackupRun.id)).where(
                    BackupRun.status.in_(("executando", "pendente"))
                )
            )
            executando = int(r.scalar() or 0)
    except Exception:
        # Banco indisponível não pode derrubar o health check — o
        # healthcheck do container depende dele para não matar o painel.
        executando = -1

    terminais = len(app.state.terminals.ativas()) if hasattr(app.state, "terminals") else 0
    streams = len(app.state.logs.ativas()) if hasattr(app.state, "logs") else 0

    return {
        "ok": True,
        "servico": "dgt-faceops",
        "versao": app.version,
        "revisao": os.getenv("FACEOPS_REVISAO", "desconhecida"),
        "agendamentos": len(app.state.scheduler.scheduler.get_jobs())
        if hasattr(app.state, "scheduler") else 0,
        "backups_executando": executando,
        "terminais_ativos": terminais,
        "logs_ativos": streams,
        # Um único campo para o script decidir. Se estiver True, atualizar
        # agora interrompe trabalho de alguém.
        "ocupado": executando > 0 or terminais > 0 or streams > 0,
    }


app.include_router(auth.router)
app.include_router(configuracoes.router)
app.include_router(marca.router)
app.include_router(monitor.router)
app.include_router(dispositivos.router)
app.include_router(descoberta.router)
app.include_router(processos.router)
app.include_router(exportar.router)
app.include_router(hosts.router)
app.include_router(ops.router)
app.include_router(backups.router)
app.include_router(destinos.router)
app.include_router(logs.router)
app.include_router(maintenance.router)
app.include_router(terminal.router)
app.include_router(audit.router)
