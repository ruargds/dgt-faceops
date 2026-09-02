"""
Exportação para auditoria e operação.

CSV, não Excel. Três razões: abre em qualquer coisa, não exige biblioteca
no painel, e é o formato que qualquer ferramenta de análise ingere sem
conversão.

Cada exportação passa pela mesma permissão da tela correspondente e gera
registro de auditoria — exportar auditoria é, ela mesma, um ato auditável.
"""
import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_permission
from app.db.database import get_db
from app.models.amostra import Amostra
from app.models.audit import AuditLog, TerminalSession
from app.models.backup import BackupRun, Schedule
from app.models.host import Host
from app.models.user import User
from app.services import audit_service

router = APIRouter(prefix="/api/exportar", tags=["exportar"])

# Teto de linhas por exportação. Sem limite, um pedido de "toda a
# auditoria" num painel de três anos montaria centenas de MB em memória.
MAX_LINHAS = 100_000


def _csv(nome: str, cabecalho: list[str], linhas) -> StreamingResponse:
    buffer = io.StringIO()
    # BOM para o Excel abrir acentuação corretamente. Sem ele, "Configurações"
    # vira "ConfiguraÃ§Ãµes" na planilha — e alguém perde tempo com isso.
    buffer.write("﻿")
    escritor = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    escritor.writerow(cabecalho)
    for linha in linhas:
        escritor.writerow(linha)
    buffer.seek(0)

    carimbo = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{nome}-{carimbo}.csv"'
        },
    )


def _quando(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


@router.get("/auditoria")
async def auditoria(
    request: Request,
    dias: int = Query(default=30, ge=1, le=3650),
    nivel: str | None = Query(default=None),
    usuario: str | None = Query(default=None),
    # Os mesmos filtros da tela: exportar tem de trazer o que está à
    # vista. CSV que discorda da tela ninguém desconfia — e auditoria é
    # justamente onde discordar em silêncio é mais caro.
    busca: str | None = Query(default=None, max_length=120),
    action: str | None = Query(default=None),
    so_falhas: bool = Query(default=False),
    autor: User = Depends(require_permission("audit.view")),
    db: AsyncSession = Depends(get_db),
):
    """Trilha de auditoria. O que foi feito, por quem, quando e de onde."""
    desde = datetime.now(timezone.utc) - timedelta(days=dias)
    consulta = audit_service.aplicar_filtros(
        select(AuditLog).order_by(AuditLog.ts.desc()).limit(MAX_LINHAS),
        busca=busca, usuario=usuario, action=action, level=nivel,
        desde=desde, so_falhas=so_falhas,
    )

    registros = list((await db.execute(consulta)).scalars().all())

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="exportar",
        target="auditoria",
        ip=client_ip(request),
        detail={
            "dias": dias, "linhas": len(registros), "nivel": nivel or "todos",
            # Que filtro gerou este CSV também é fato auditável: sem isso,
            # dois arquivos com contagens diferentes ficam sem explicação.
            "busca": (busca or "")[:60], "acao": action or "todas",
            "so_falhas": so_falhas,
        },
    )

    return _csv(
        "faceops-auditoria",
        ["Data/hora", "Usuário", "IP", "Ação", "Alvo", "Nível", "Sucesso", "Detalhe"],
        (
            [
                _quando(r.ts), r.usuario, r.ip, r.action, r.target,
                r.level, "sim" if r.success else "NÃO",
                "; ".join(f"{k}={v}" for k, v in (r.detail or {}).items()),
            ]
            for r in registros
        ),
    )


@router.get("/backups")
async def backups(
    request: Request,
    dias: int = Query(default=90, ge=1, le=3650),
    autor: User = Depends(require_permission("backups.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Histórico de execuções — a prova de que os backups rodaram.

    É o relatório que se apresenta quando alguém pergunta se o ambiente
    está protegido.
    """
    desde = datetime.now(timezone.utc) - timedelta(days=dias)
    registros = list((await db.execute(
        select(BackupRun)
        .where(BackupRun.started_at >= desde)
        .order_by(BackupRun.started_at.desc())
        .limit(MAX_LINHAS)
    )).scalars().all())

    nomes = {h[0]: h[1] for h in (await db.execute(select(Host.id, Host.name))).all()}

    def linha(r):
        duracao = ""
        if r.finished_at and r.started_at:
            duracao = str(int((r.finished_at - r.started_at).total_seconds()))
        destinos = "; ".join(
            f"{d.get('nome', d.get('type'))}:{d.get('status')}"
            for d in (r.destinations or [])
        )
        return [
            _quando(r.started_at),
            "Painel" if r.host_id is None else nomes.get(r.host_id, "?"),
            r.profile, r.status, r.stage,
            str(r.size_bytes), duracao,
            r.artifact_name, r.checksum_sha256[:16],
            destinos, r.triggered_by,
            "sim" if r.caused_downtime else "não", str(r.downtime_seconds),
            (r.error or "")[:300],
        ]

    await audit_service.registrar(
        db, usuario=autor.username, action="exportar", target="backups",
        ip=client_ip(request), detail={"dias": dias, "linhas": len(registros)},
    )

    return _csv(
        "faceops-backups",
        ["Início", "Servidor", "Perfil", "Situação", "Etapa", "Bytes",
         "Duração (s)", "Artefato", "Checksum", "Destinos", "Disparado por",
         "Causou parada", "Parada (s)", "Erro"],
        (linha(r) for r in registros),
    )


@router.get("/agendamentos")
async def agendamentos(
    request: Request,
    autor: User = Depends(require_permission("schedules.view")),
    db: AsyncSession = Depends(get_db),
):
    """As rotinas programadas e o resultado da última execução de cada."""
    registros = list((await db.execute(
        select(Schedule).order_by(Schedule.name)
    )).scalars().all())
    nomes = {h[0]: h[1] for h in (await db.execute(select(Host.id, Host.name))).all()}

    await audit_service.registrar(
        db, usuario=autor.username, action="exportar", target="agendamentos",
        ip=client_ip(request), detail={"linhas": len(registros)},
    )

    return _csv(
        "faceops-agendamentos",
        ["Nome", "Servidor", "Perfil", "Cron", "Ativo", "Retenção (dias)",
         "Aceita parada", "Última execução", "Último resultado", "Próxima",
         "Criado por"],
        (
            [
                r.name,
                "Painel" if r.host_id is None else nomes.get(r.host_id, "?"),
                r.profile, r.cron, "sim" if r.enabled else "não",
                str(r.retention_days), "sim" if r.allow_downtime else "não",
                _quando(r.last_run_at), r.last_status, _quando(r.next_run_at),
                r.created_by,
            ]
            for r in registros
        ),
    )


@router.get("/sessoes")
async def sessoes(
    request: Request,
    dias: int = Query(default=90, ge=1, le=3650),
    autor: User = Depends(require_permission("terminal.sessions.view")),
    db: AsyncSession = Depends(get_db),
):
    """Quem abriu terminal em qual servidor, quando e por quanto tempo."""
    desde = datetime.now(timezone.utc) - timedelta(days=dias)
    registros = list((await db.execute(
        select(TerminalSession)
        .where(TerminalSession.started_at >= desde)
        .order_by(TerminalSession.started_at.desc())
        .limit(MAX_LINHAS)
    )).scalars().all())
    nomes = {h[0]: h[1] for h in (await db.execute(select(Host.id, Host.name))).all()}

    await audit_service.registrar(
        db, usuario=autor.username, action="exportar", target="sessoes",
        ip=client_ip(request), detail={"dias": dias, "linhas": len(registros)},
    )

    return _csv(
        "faceops-sessoes-terminal",
        ["Início", "Fim", "Duração (s)", "Usuário", "IP", "Servidor",
         "Usou sudo", "Enviado", "Recebido", "Encerrou por", "Gravação"],
        (
            [
                _quando(r.started_at), _quando(r.ended_at),
                str(int((r.ended_at - r.started_at).total_seconds()))
                if r.ended_at else "",
                r.usuario, r.ip, nomes.get(r.host_id, "?"),
                "sim" if r.sudo_used else "não",
                str(r.bytes_in), str(r.bytes_out),
                r.end_reason, r.recording_path,
            ]
            for r in registros
        ),
    )


@router.get("/monitor/{host_id}")
async def monitor(
    host_id: int,
    request: Request,
    horas: int = Query(default=168, ge=1, le=8760),
    autor: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """Histórico bruto de monitoramento — para análise fora do painel."""
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")

    desde = datetime.now(timezone.utc) - timedelta(hours=horas)
    registros = list((await db.execute(
        select(Amostra)
        .where(Amostra.host_id == host_id, Amostra.ts >= desde)
        .order_by(Amostra.ts)
        .limit(MAX_LINHAS)
    )).scalars().all())

    await audit_service.registrar(
        db, usuario=autor.username, action="exportar",
        target=f"monitor/{host.name}", ip=client_ip(request),
        detail={"horas": horas, "linhas": len(registros)},
    )

    return _csv(
        f"faceops-monitor-{host.name}",
        ["Data/hora", "Carga por núcleo", "CPU %", "Memória %", "Swap %",
         "Disco %", "Ponto de montagem", "Disco livre (GB)",
         "GPU %", "GPU memória %", "GPU °C",
         "Containers rodando", "Containers total", "Com problema",
         "Coleta (ms)", "Erro"],
        (
            [
                _quando(a.ts), str(a.carga_por_nucleo), str(a.cpu_pct),
                str(a.mem_pct), str(a.swap_pct), str(a.disco_pct),
                a.disco_ponto, str(a.disco_livre_gb),
                str(a.gpu_pct), str(a.gpu_mem_pct), str(a.gpu_temp),
                str(a.containers_rodando), str(a.containers_total),
                str(a.containers_problema), str(a.coleta_ms), a.erro,
            ]
            for a in registros
        ),
    )


@router.get("/dispositivos/{host_id}")
async def dispositivos(
    host_id: int,
    request: Request,
    periodo: str = Query(default="mes"),
    autor: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """Câmeras com volume de eventos e última comunicação."""
    from app.services.dispositivos_service import DispositivosError, PERIODOS
    from app.services.ssh_service import SSHError as _SSHError

    if periodo not in PERIODOS:
        raise HTTPException(status_code=400, detail="período inválido")

    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")

    try:
        dados = await request.app.state.dispositivos.listar(
            host, request.app.state.stack, periodo
        )
    except (_SSHError, DispositivosError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await audit_service.registrar(
        db, usuario=autor.username, action="exportar",
        target=f"dispositivos/{host.name}", ip=client_ip(request),
        detail={"periodo": periodo, "cameras": dados["total_cameras"]},
    )

    return _csv(
        f"faceops-cameras-{host.name}",
        ["Câmera", "ID", "Grupo", "Ativa", f"Eventos ({dados['periodo_rotulo']})",
         "Fatia %", "Volume estimado (bytes)", "Último evento", "Por tipo"],
        (
            [
                c["nome"], c["id"], c["grupo"],
                "sim" if c["ativo"] else "não",
                str(c["eventos"]), str(c["fatia_pct"]),
                str(c["bytes_estimados"]),
                c["ultimo_evento"] or "nunca",
                "; ".join(f"{k}={v}" for k, v in c["por_tipo"].items()),
            ]
            for c in dados["cameras"]
        ),
    )
