"""Backups sob demanda, histórico, download e agendamentos."""
import asyncio
import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import client_ip, require_permission
from app.db.database import get_db
from app.models.backup import PROFILES, BackupRun, Schedule
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

# Tickets de download de uso único. O artefato de backup pode ter dezenas
# de GB — baixar por fetch+blob bufferaria tudo na memória do navegador.
# Com ticket, o navegador NAVEGA direto para a rota (streaming pelo nginx)
# e a autenticação viaja no ticket, não num header que a navegação não
# manda. Vale 60s, serve uma vez, libera só aquele arquivo.
_download_tickets: dict[str, dict] = {}
_TICKET_TTL = 60


def _limpar_tickets_download() -> None:
    agora = time.monotonic()
    for t in [k for k, v in _download_tickets.items() if v["expira_em"] < agora]:
        _download_tickets.pop(t, None)


async def _resolver_artefato(db: AsyncSession, run_id: int):
    """(caminho, filename) do artefato no disco do painel, ou HTTPException."""
    run = await db.get(BackupRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="execução não encontrada")
    if run.status != "sucesso" or not run.artifact_name:
        raise HTTPException(status_code=400, detail="esta execução não gerou artefato")

    host = await db.get(Host, run.host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor do backup não existe mais")

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
    return caminho, run.artifact_name, run, host



async def _host_ou_404(db: AsyncSession, host_id: int) -> Host:
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")
    return host


async def _nomes_hosts(db: AsyncSession) -> dict[int, str]:
    resultado = await db.execute(select(Host.id, Host.name))
    return {linha[0]: linha[1] for linha in resultado.all()}


# ── Backup do próprio painel ───────────────────────────────────────────


class BackupPainelIn(BaseModel):
    destinos: list[int] = []


@router.post("/backups-painel", response_model=BackupOut, status_code=202)
async def backup_do_painel(
    dados: BackupPainelIn,
    request: Request,
    autor: User = Depends(require_permission("backups.run")),
    db: AsyncSession = Depends(get_db),
):
    """
    Salva o banco do PRÓPRIO painel.

    Protege o que nenhum outro backup cobre: cadastro dos servidores,
    credenciais cifradas, destinos, agendamentos, histórico e auditoria.
    São alguns MB — irrisório perto de recadastrar tudo.

    A SECRET_KEY não vai no artefato, de propósito: ela decifra o que
    está lá dentro. Guarde o `.env` separado.
    """
    servico = request.app.state.painel_backup
    if servico.ocupado():
        raise HTTPException(
            status_code=409, detail="já existe um backup do painel em andamento"
        )

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="backups.run",
        target="painel",
        ip=client_ip(request),
        detail={"perfil": "painel", "destinos": dados.destinos},
    )

    run = await servico.executar(db, dados.destinos, disparado_por=autor.username)
    saida = BackupOut.model_validate(run)
    saida.host_nome = "Painel"
    return saida


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
        item.host_nome = (
            "Painel" if run.host_id is None else nomes.get(run.host_id, "?")
        )
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
    saida.host_nome = (
        "Painel" if run.host_id is None else nomes.get(run.host_id, "?")
    )
    return saida


@router.post("/backups/{run_id}/download-ticket")
async def emitir_ticket_download(
    run_id: int,
    request: Request,
    autor: User = Depends(require_permission("backups.download")),
    db: AsyncSession = Depends(get_db),
):
    """
    Emite um ticket de uso único para baixar o artefato.

    O navegador usa este ticket na URL de download e baixa em streaming —
    sem carregar o arquivo (que pode ter dezenas de GB) na memória, e sem
    depender de um header que a navegação não envia.
    """
    caminho, filename, run, host = await _resolver_artefato(db, run_id)

    _limpar_tickets_download()
    ticket = secrets.token_urlsafe(32)
    _download_tickets[ticket] = {
        "caminho": str(caminho),
        "filename": filename,
        "expira_em": time.monotonic() + _TICKET_TTL,
    }

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="backups.download",
        target=f"{host.name}/{run.artifact_name}",
        ip=client_ip(request),
        detail={"run_id": run_id, "bytes": run.size_bytes},
    )
    return {"ticket": ticket, "expira_em_s": _TICKET_TTL, "arquivo": filename}


@router.get("/backups/download")
async def baixar_por_ticket(ticket: str = Query(...)):
    """Baixa o artefato em streaming. Autenticação pelo ticket, uso único."""
    _limpar_tickets_download()
    info = _download_tickets.pop(ticket, None)
    if info is None or info["expira_em"] < time.monotonic():
        raise HTTPException(status_code=401, detail="ticket inválido ou expirado")
    caminho = Path(info["caminho"])
    if not caminho.is_file():
        raise HTTPException(status_code=404, detail="artefato não está mais no disco")
    return FileResponse(
        path=str(caminho),
        filename=info["filename"],
        media_type="application/gzip",
    )


@router.get("/backups/{run_id}/download")
async def baixar(
    run_id: int,
    request: Request,
    autor: User = Depends(require_permission("backups.download")),
    db: AsyncSession = Depends(get_db),
):
    """Download direto por header Authorization — para clientes de API."""
    caminho, filename, run, host = await _resolver_artefato(db, run_id)
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
        filename=filename,
        media_type="application/gzip",
    )


@router.get("/backups/{run_id}/manifesto")
async def manifesto(
    run_id: int,
    _: User = Depends(require_permission("backups.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    O manifesto de dentro do artefato, sem precisar baixar e extrair.

    Ele traz o que o backup contem, a versao das imagens do FindFace e o
    roteiro de restauracao do fabricante -- e e o que se deve ler ANTES de
    restaurar. A versao das imagens importa: a base do Tarantool nao e
    compativel entre versoes maiores, e restaurar num sistema de outra
    versao nao devolve o reconhecimento.
    """
    import tarfile

    run = await db.get(BackupRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="execução não encontrada")
    if not run.artifact_name:
        raise HTTPException(
            status_code=400, detail="esta execução não gerou artefato"
        )

    host = await db.get(Host, run.host_id) if run.host_id else None
    base = None
    for d in (run.destinations or []):
        if d.get("type") == "local" and d.get("uri"):
            base = str(Path(d["uri"]).parent.parent)
            break
    caminho = StorageService.caminho_artefato(
        host.name if host else "_painel", run.artifact_name, base
    )
    if caminho is None or not caminho.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                "o artefato não está mais no disco do painel (pode ter ido para "
                "a nuvem ou sido removido pela retenção). O manifesto vive dentro "
                "dele."
            ),
        )

    def _ler() -> str:
        with tarfile.open(caminho, "r:*") as tar:
            for membro in tar.getmembers():
                if membro.name.endswith("MANIFESTO.txt"):
                    arquivo = tar.extractfile(membro)
                    if arquivo is None:
                        return ""
                    return arquivo.read().decode("utf-8", "replace")[:20000]
        return ""

    try:
        texto = await asyncio.to_thread(_ler)
    except (tarfile.TarError, OSError) as exc:
        raise HTTPException(
            status_code=502, detail=f"não consegui abrir o artefato: {exc}"
        ) from exc

    if not texto:
        raise HTTPException(
            status_code=404,
            detail="este artefato não tem MANIFESTO.txt (backup de versão antiga?)",
        )
    return {"run_id": run_id, "arquivo": run.artifact_name, "manifesto": texto}


@router.post("/backups-importar", response_model=BackupOut, status_code=201)
async def importar(
    request: Request,
    arquivo: UploadFile = File(...),
    host_id: int | None = Form(default=None),
    autor: User = Depends(require_permission("backups.run")),
    db: AsyncSession = Depends(get_db),
):
    """
    Traz um artefato de fora para o painel.

    Serve ao backup feito na mão no servidor, ao que veio de outra
    instalação e ao que alguém guardou fora. Depois de importado, ele
    aparece no histórico como qualquer outro: dá para baixar, ler o
    manifesto e apagar.

    **Não restaura nada.** Importar é só guardar e catalogar — restaurar é
    outra decisão, com procedimento próprio.
    """
    nome = Path(arquivo.filename or "backup.tar.gz").name
    if not nome.endswith((".tar.gz", ".tgz", ".tar")):
        raise HTTPException(
            status_code=400,
            detail="o artefato precisa ser .tar.gz (é o formato que o painel gera)",
        )

    host = await db.get(Host, host_id) if host_id else None
    pasta = host.name if host is not None else "_importado"

    destino_dir = Path(settings.LOCAL_BACKUP_DIR) / pasta
    destino = destino_dir / nome
    if destino.exists():
        destino = destino_dir / f"{Path(nome).stem}_importado_{int(time.time())}.tar.gz"

    # Grava em pedaços e calcula o checksum no mesmo passe: artefato de
    # backup tem dezenas de GB, e ler duas vezes seria dobrar o custo.
    import hashlib

    soma = hashlib.sha256()
    tamanho = 0
    try:
        destino_dir.mkdir(parents=True, exist_ok=True)
        with destino.open("wb") as saida:
            while True:
                pedaco = await arquivo.read(1024 * 1024)
                if not pedaco:
                    break
                tamanho += len(pedaco)
                soma.update(pedaco)
                await asyncio.to_thread(saida.write, pedaco)
    except OSError as exc:
        try:
            destino.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(
            status_code=500, detail=f"falha ao gravar no disco do painel: {exc}"
        ) from exc

    run = BackupRun(
        host_id=host.id if host is not None else None,
        profile="config",
        status="sucesso",
        stage="Importado",
        progress=100,
        artifact_name=destino.name,
        size_bytes=tamanho,
        checksum_sha256=soma.hexdigest(),
        triggered_by=f"importado:{autor.username}",
        destinations=[{
            "type": "local",
            "name": "disco do painel",
            "ok": True,
            "uri": str(destino),
        }],
        log=(
            f"[importado] arquivo enviado pelo painel por {autor.username}\n"
            f"[importado] {tamanho} bytes, sha256 {soma.hexdigest()}"
        ),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="backups.run",
        target=f"{pasta}/{destino.name}",
        ip=client_ip(request),
        detail={
            "acao": "importar artefato",
            "bytes": tamanho,
            "sha256": soma.hexdigest(),
        },
    )

    saida = BackupOut.model_validate(run)
    saida.host_nome = host.name if host is not None else "importado"
    return saida


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

    # Execução sem artefato — falha, quase sempre — sai do histórico de
    # verdade. O `expired` existe para lembrar que houve um artefato e ele
    # foi embora; onde nunca houve artefato, marcar expirado só deixa a
    # linha de erro presa na tela para sempre, sem nada a proteger.
    apagou_linha = False
    if not run.artifact_name:
        await db.delete(run)
        apagou_linha = True
    else:
        run.expired = True
    await db.commit()

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="backups.delete",
        target=f"{host.name if host else '?'}/{run.artifact_name or 'execução falha'}",
        ip=client_ip(request),
        level="critical",
        detail={
            "run_id": run_id,
            "arquivo_removido": removido,
            "linha_removida": apagou_linha,
        },
    )
    return {
        "ok": True,
        "arquivo_removido": removido,
        "linha_removida": apagou_linha,
    }


@router.get("/backups/perfis/{host_id}")
async def perfis_do_host(
    host_id: int,
    request: Request,
    _: User = Depends(require_permission("backups.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Que perfis de backup fazem sentido NESTE servidor.

    Oferecer os três em qualquer máquina produz falha garantida: backup
    'essencial' num servidor de FTP gasta um SSH, some em 0s e suja o
    histórico com um erro que nunca foi surpresa para ninguém.

    A resposta sai do que a máquina TEM, descoberto na hora: diretório da
    instalação, bancos e serviços rodando ali. Cada perfil vem com o motivo
    de estar disponível ou não — perfil cinza sem explicação gera chamado.
    """
    host = await _host_ou_404(db, host_id)

    # Descoberta é leitura só; uma execução SSH.
    try:
        inventario = await request.app.state.descoberta.inventariar(host)
    except Exception as exc:
        # Sem inventário não dá para afirmar o que a máquina tem. Melhor
        # liberar tudo com o aviso do que travar o operador com base numa
        # leitura que falhou.
        return {
            "host": host.name,
            "aviso": f"não consegui inventariar este servidor ({exc}). "
            "Os perfis estão liberados, mas confira o resultado.",
            "perfis": [
                {"id": p, "disponivel": True, "motivo": "não verificado"}
                for p in PROFILES
            ],
        }

    texto = json.dumps(inventario, ensure_ascii=False).lower()
    tem_findface = bool(
        inventario.get("ffmulti_dir")
        or "findface" in texto
        or "ffmulti" in texto
    )
    tem_banco = any(
        chave in texto for chave in ("postgres", "timescale", "tarantool", "mongo")
    )

    def motivo(disponivel: bool, quando_sim: str, quando_nao: str) -> str:
        return quando_sim if disponivel else quando_nao

    perfis = [
        {
            "id": "config",
            "disponivel": tem_findface,
            "motivo": motivo(
                tem_findface,
                "há instalação do FindFace neste servidor",
                "não encontrei instalação do FindFace aqui — nada a copiar",
            ),
        },
        {
            "id": "essencial",
            "disponivel": tem_findface and tem_banco,
            "motivo": motivo(
                tem_findface and tem_banco,
                "há banco de dados do FindFace neste servidor",
                "sem banco do FindFace aqui: o perfil essencial não teria o "
                "que despejar",
            ),
        },
        {
            "id": "completo",
            "disponivel": tem_findface,
            "motivo": motivo(
                tem_findface,
                "PARA o FindFace durante a cópia — exige janela",
                "não encontrei instalação do FindFace aqui",
            ),
        },
    ]

    return {
        "host": host.name,
        "ffmulti_dir": inventario.get("ffmulti_dir") or host.ffmulti_dir,
        "tem_findface": tem_findface,
        "tem_banco": tem_banco,
        "aviso": ""
        if tem_findface
        else f"'{host.name}' não hospeda o FindFace. Só o backup do painel "
        "faz sentido aqui — veja em Topologia onde a aplicação está.",
        "perfis": perfis,
    }


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

    if dados.tipo == "backup" and dados.perfil == "completo" and not dados.allow_downtime:
        raise HTTPException(
            status_code=400,
            detail=(
                "Agendamento com perfil 'completo' precisa do aceite de janela "
                "de manutenção — ele PARA o FindFace Multi durante a cópia."
            ),
        )

    if dados.tipo == "limpeza" and not dados.como_configurado and not dados.itens:
        raise HTTPException(
            status_code=400,
            detail=(
                "Limpeza agendada precisa de ao menos um tipo de dado, ou do modo "
                "'como configurado na plataforma'. O manual da NtechLab é claro: "
                "'You must provide at least one of the mentioned arguments'."
            ),
        )

    parametros: dict = {}
    if dados.tipo == "limpeza":
        parametros = {
            "como_configurado": dados.como_configurado,
            "itens": [i.model_dump() for i in dados.itens],
        }

    agendamento = Schedule(
        name=dados.name,
        host_id=dados.host_id,
        profile=dados.perfil,
        tipo=dados.tipo,
        parametros=parametros,
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
            "tipo": dados.tipo,
            "cron": dados.cron,
            "legivel": cron_legivel(dados.cron),
            "perfil": dados.perfil if dados.tipo == "backup" else "—",
            "limpeza": parametros or None,
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
