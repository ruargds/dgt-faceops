"""Backups sob demanda, histórico, download e agendamentos."""
import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_permission
from app.db.database import get_db
from app.models.backup import BackupRun, Schedule
from app.models.host import Host
from app.models.user import User
from app.schemas import (
    BackupDetalheOut,
    BackupIn,
    BackupOut,
    ScheduleIn,
    ScheduleOut,
    ScheduleUpdate,
)
from app.services import audit_service
from app.services.scheduler_service import cron_legivel, validar_cron
from app.services.storage_service import StorageService

router = APIRouter(prefix="/api", tags=["backups"])


async def _host_ou_404(db: AsyncSession, host_id: int) -> Host:
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")
    return host


async def _nomes_hosts(db: AsyncSession) -> dict[int, str]:
    resultado = await db.execute(select(Host.id, Host.name))
    return {linha[0]: linha[1] for linha in resultado.all()}


# ── Execução ───────────────────────────────────────────────────────────


@router.post("/backups/{host_id}", response_model=BackupOut, status_code=202)
async def disparar(
    host_id: int,
    dados: BackupIn,
    request: Request,
    autor: User = Depends(require_permission("backups.run")),
    db: AsyncSession = Depends(get_db),
):
    """
    Dispara um backup. Responde 202 na hora e segue em segundo plano —
    um perfil completo leva horas e não caberia numa requisição HTTP.
    """
    host = await _host_ou_404(db, host_id)
    if not host.enabled:
        raise HTTPException(status_code=400, detail=f"servidor '{host.name}' está desativado")

    servico = request.app.state.backups
    if servico.ocupado(host.id):
        raise HTTPException(
            status_code=409,
            detail=f"já existe backup em andamento em '{host.name}'",
        )

    # O perfil completo para o stack. Exigir aceite explícito impede que
    # um clique distraído derrube o reconhecimento facial no meio do dia.
    if dados.perfil == "completo" and not dados.aceito_downtime:
        raise HTTPException(
            status_code=400,
            detail=(
                "O perfil 'completo' PARA o FindFace Multi enquanto copia o "
                "data/ (pode levar horas). Marque o aceite de janela de "
                "manutenção para prosseguir."
            ),
        )

    run = BackupRun(
        host_id=host.id,
        profile=dados.perfil,
        status="pendente",
        stage="Na fila",
        progress=0,
        triggered_by=autor.username,
        destinations=[],
        caused_downtime=(dados.perfil == "completo"),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="backups.run",
        target=f"{host.name}/{dados.perfil}",
        ip=client_ip(request),
        level="critical" if dados.perfil == "completo" else "info",
        detail={
            "perfil": dados.perfil,
            "destinos": dados.destinos,
            "run_id": run.id,
        },
    )

    # Segue em segundo plano reaproveitando este mesmo registro, para que
    # o id devolvido agora continue válido enquanto a UI acompanha.
    asyncio.create_task(
        servico.processar_em_segundo_plano(
            run.id,
            host.id,
            dados.perfil,
            dados.destinos,
            autor.username,
            dados.retencao_dias,
        )
    )

    saida = BackupOut.model_validate(run)
    saida.host_nome = host.name
    return saida


@router.get("/backups", response_model=list[BackupOut])
async def historico(
    host_id: int | None = Query(default=None),
    perfil: str | None = Query(default=None),
    status_filtro: str | None = Query(default=None, alias="status"),
    limite: int = Query(default=100, ge=1, le=500),
    _: User = Depends(require_permission("backups.view")),
    db: AsyncSession = Depends(get_db),
):
    consulta = select(BackupRun).order_by(BackupRun.started_at.desc()).limit(limite)
    if host_id is not None:
        consulta = consulta.where(BackupRun.host_id == host_id)
    if perfil:
        consulta = consulta.where(BackupRun.profile == perfil)
    if status_filtro:
        consulta = consulta.where(BackupRun.status == status_filtro)

    resultado = await db.execute(consulta)
    nomes = await _nomes_hosts(db)

    saida: list[BackupOut] = []
    for run in resultado.scalars().all():
        item = BackupOut.model_validate(run)
        item.host_nome = nomes.get(run.host_id, "?")
        saida.append(item)
    return saida


@router.get("/backups/{run_id}", response_model=BackupDetalheOut)
async def detalhe(
    run_id: int,
    _: User = Depends(require_permission("backups.view")),
    db: AsyncSession = Depends(get_db),
):
    """Detalhe com o log completo — a UI usa para acompanhar ao vivo."""
    run = await db.get(BackupRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="execução não encontrada")
    nomes = await _nomes_hosts(db)
    saida = BackupDetalheOut.model_validate(run)
    saida.host_nome = nomes.get(run.host_id, "?")
    return saida


@router.get("/backups/{run_id}/download")
async def baixar(
    run_id: int,
    request: Request,
    autor: User = Depends(require_permission("backups.download")),
    db: AsyncSession = Depends(get_db),
):
    run = await db.get(BackupRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="execução não encontrada")
    if run.status != "sucesso" or not run.artifact_name:
        raise HTTPException(status_code=400, detail="esta execução não gerou artefato")

    host = await db.get(Host, run.host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor do backup não existe mais")

    # O caminho vem do destino local registrado na própria execução —
    # destinos são configuráveis, então assumir um diretório fixo
    # quebraria depois de qualquer mudança de configuração.
    base = None
    for d in (run.destinations or []):
        if d.get("type") == "local" and d.get("status") == "ok" and d.get("uri"):
            base = str(Path(d["uri"]).parent.parent)
            break

    caminho = StorageService.caminho_artefato(host.name, run.artifact_name, base)
    if caminho is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "artefato não está no disco do painel. Pode ter expirado pela "
                "retenção ou ter sido enviado só para a nuvem."
            ),
        )

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="backups.download",
        target=f"{host.name}/{run.artifact_name}",
        ip=client_ip(request),
        detail={"run_id": run_id, "bytes": run.size_bytes},
    )
    return FileResponse(
        path=str(caminho),
        filename=run.artifact_name,
        media_type="application/gzip",
    )


@router.delete("/backups/{run_id}")
async def remover(
    run_id: int,
    request: Request,
    autor: User = Depends(require_permission("backups.delete")),
    db: AsyncSession = Depends(get_db),
):
    run = await db.get(BackupRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="execução não encontrada")

    host = await db.get(Host, run.host_id)
    removido = False
    if host is not None and run.artifact_name:
        base = None
        for d in (run.destinations or []):
            if d.get("type") == "local" and d.get("uri"):
                base = str(Path(d["uri"]).parent.parent)
                break
        caminho = StorageService.caminho_artefato(host.name, run.artifact_name, base)
        if caminho is not None:
            try:
                caminho.unlink()
                removido = True
            except OSError as exc:
                raise HTTPException(
                    status_code=500, detail=f"falha ao apagar o arquivo: {exc}"
                ) from exc

    run.expired = True
    await db.commit()

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="backups.delete",
        target=f"{host.name if host else '?'}/{run.artifact_name}",
        ip=client_ip(request),
        level="critical",
        detail={"run_id": run_id, "arquivo_removido": removido},
    )
    return {"ok": True, "arquivo_removido": removido}


@router.get("/backups-armazenamento")
async def armazenamento_painel(
    _: User = Depends(require_permission("backups.view")),
):
    return StorageService.espaco_local()


# ── Agendamentos ───────────────────────────────────────────────────────


def _para_out(agendamento: Schedule, nomes: dict[int, str], proxima) -> ScheduleOut:
    saida = ScheduleOut.model_validate(agendamento)
    saida.host_nome = nomes.get(agendamento.host_id, "?")
    saida.cron_legivel = cron_legivel(agendamento.cron)
    if proxima is not None:
        saida.next_run_at = proxima
    return saida


@router.get("/schedules", response_model=list[ScheduleOut])
async def listar_agendamentos(
    request: Request,
    _: User = Depends(require_permission("schedules.view")),
    db: AsyncSession = Depends(get_db),
):
    resultado = await db.execute(select(Schedule).order_by(Schedule.name))
    nomes = await _nomes_hosts(db)
    agendador = request.app.state.scheduler
    return [
        _para_out(a, nomes, agendador.proxima_execucao(a.id))
        for a in resultado.scalars().all()
    ]


@router.post("/schedules", response_model=ScheduleOut, status_code=201)
async def criar_agendamento(
    dados: ScheduleIn,
    request: Request,
    autor: User = Depends(require_permission("schedules.manage")),
    db: AsyncSession = Depends(get_db),
):
    host = await _host_ou_404(db, dados.host_id)

    try:
        validar_cron(dados.cron)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if dados.perfil == "completo" and not dados.allow_downtime:
        raise HTTPException(
            status_code=400,
            detail=(
                "Agendamento com perfil 'completo' precisa do aceite de janela "
                "de manutenção — ele PARA o FindFace Multi durante a cópia."
            ),
        )

    agendamento = Schedule(
        name=dados.name,
        host_id=dados.host_id,
        profile=dados.perfil,
        cron=dados.cron,
        destinations=dados.destinos,
        retention_days=dados.retencao_dias,
        enabled=dados.enabled,
        allow_downtime=dados.allow_downtime,
        created_by=autor.username,
    )
    db.add(agendamento)
    await db.commit()
    await db.refresh(agendamento)

    await request.app.state.scheduler.sincronizar()

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="schedules.manage",
        target=f"{host.name}/{dados.name}",
        ip=client_ip(request),
        detail={
            "acao": "criar",
            "cron": dados.cron,
            "legivel": cron_legivel(dados.cron),
            "perfil": dados.perfil,
        },
    )

    nomes = await _nomes_hosts(db)
    return _para_out(
        agendamento, nomes, request.app.state.scheduler.proxima_execucao(agendamento.id)
    )


@router.patch("/schedules/{schedule_id}", response_model=ScheduleOut)
async def atualizar_agendamento(
    schedule_id: int,
    dados: ScheduleUpdate,
    request: Request,
    autor: User = Depends(require_permission("schedules.manage")),
    db: AsyncSession = Depends(get_db),
):
    agendamento = await db.get(Schedule, schedule_id)
    if agendamento is None:
        raise HTTPException(status_code=404, detail="agendamento não encontrado")

    if dados.cron is not None:
        try:
            validar_cron(dados.cron)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        agendamento.cron = dados.cron

    if dados.name is not None:
        agendamento.name = dados.name
    if dados.perfil is not None:
        agendamento.profile = dados.perfil
    if dados.destinos is not None:
        agendamento.destinations = dados.destinos
    if dados.retencao_dias is not None:
        agendamento.retention_days = dados.retencao_dias
    if dados.allow_downtime is not None:
        agendamento.allow_downtime = dados.allow_downtime
    if dados.enabled is not None:
        agendamento.enabled = dados.enabled

    if agendamento.profile == "completo" and not agendamento.allow_downtime:
        raise HTTPException(
            status_code=400,
            detail="perfil 'completo' exige aceite de janela de manutenção",
        )

    await db.commit()
    await db.refresh(agendamento)
    await request.app.state.scheduler.sincronizar()

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="schedules.manage",
        target=agendamento.name,
        ip=client_ip(request),
        detail={"acao": "atualizar", "cron": agendamento.cron},
    )

    nomes = await _nomes_hosts(db)
    return _para_out(
        agendamento, nomes, request.app.state.scheduler.proxima_execucao(schedule_id)
    )


@router.delete("/schedules/{schedule_id}")
async def remover_agendamento(
    schedule_id: int,
    request: Request,
    autor: User = Depends(require_permission("schedules.manage")),
    db: AsyncSession = Depends(get_db),
):
    agendamento = await db.get(Schedule, schedule_id)
    if agendamento is None:
        raise HTTPException(status_code=404, detail="agendamento não encontrado")

    nome = agendamento.name
    await db.delete(agendamento)
    await db.commit()
    await request.app.state.scheduler.sincronizar()

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="schedules.manage",
        target=nome,
        ip=client_ip(request),
        detail={"acao": "remover"},
    )
    return {"ok": True}


@router.post("/schedules/{schedule_id}/executar", status_code=202)
async def executar_agora(
    schedule_id: int,
    request: Request,
    autor: User = Depends(require_permission("backups.run")),
    db: AsyncSession = Depends(get_db),
):
    """Roda um agendamento fora de hora, sem mexer na recorrência."""
    agendamento = await db.get(Schedule, schedule_id)
    if agendamento is None:
        raise HTTPException(status_code=404, detail="agendamento não encontrado")

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="backups.run",
        target=agendamento.name,
        ip=client_ip(request),
        detail={"acao": "executar_agora", "schedule_id": schedule_id},
    )

    asyncio.create_task(request.app.state.scheduler.rodar_agora(schedule_id))
    return {"ok": True, "mensagem": f"Agendamento '{agendamento.name}' disparado."}
