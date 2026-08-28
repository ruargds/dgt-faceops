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
    # A tarefa fica registrada no serviço para poder ser parada depois --
    # sem isso, "cancelar" seria so uma mentira no banco enquanto o SSH
    # continua copiando dezenas de GB.
    servico.registrar_tarefa(
        run.id,
        asyncio.create_task(
            servico.processar_em_segundo_plano(
                run.id,
                host.id,
                dados.perfil,
                dados.destinos,
                autor.username,
                dados.retencao_dias,
            )
        ),
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


@router.get("/backups/{run_id}/roteiro")
async def roteiro(
    run_id: int,
    _: User = Depends(require_permission("backups.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Os comandos para restaurar ESTE artefato NESTE servidor.

    O `docs/03_RESTORE.md` explica o procedimento e o manifesto diz o que o
    artefato tem. Nenhum dos dois responde a pergunta do incidente: *quais
    comandos eu digito, nesta maquina?*. Aqui sai o passo a passo com o
    caminho real da instalacao, o nome do projeto compose e so os passos
    que o artefato realmente exige.

    Nao executa nada. Restore sobrescreve producao: o painel monta o
    roteiro, quem decide e digita e gente.
    """
    import tarfile

    from app.services.roteiro_service import montar

    run = await db.get(BackupRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="execução não encontrada")
    if not run.artifact_name:
        raise HTTPException(status_code=400, detail="esta execução não gerou artefato")

    host = await db.get(Host, run.host_id) if run.host_id else None
    if host is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "esta execução é do próprio painel; a restauração dele está em "
                "docs/19_BACKUP_DO_PAINEL.md"
            ),
        )

    pasta = host.name
    base = None
    for d in (run.destinations or []):
        if d.get("type") == "local" and d.get("uri"):
            base = str(Path(d["uri"]).parent.parent)
            break
    caminho = StorageService.caminho_artefato(pasta, run.artifact_name, base)
    if caminho is None or not caminho.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                "o artefato não está no disco do painel — o roteiro é montado a "
                "partir do manifesto que vive dentro dele"
            ),
        )

    def _ler() -> str:
        with tarfile.open(caminho, "r:*") as tar:
            for membro in tar.getmembers():
                if membro.name.endswith("MANIFESTO.txt"):
                    arquivo = tar.extractfile(membro)
                    return arquivo.read().decode("utf-8", "replace") if arquivo else ""
        return ""

    try:
        manifesto = await asyncio.to_thread(_ler)
    except (tarfile.TarError, OSError) as exc:
        raise HTTPException(
            status_code=502, detail=f"não consegui abrir o artefato: {exc}"
        ) from exc

    if not manifesto:
        raise HTTPException(
            status_code=404, detail="este artefato não tem MANIFESTO.txt"
        )

    return montar(manifesto, host, run, str(caminho))


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


@router.post("/backups/{run_id}/cancelar")
async def cancelar(
    run_id: int,
    request: Request,
    autor: User = Depends(require_permission("backups.run")),
    db: AsyncSession = Depends(get_db),
):
    """
    Para uma execução em andamento.

    Cancelar fecha o canal SSH: o script morre no servidor no próximo
    write, e o staging remoto é limpo na execução seguinte. A execução fica
    marcada como **cancelada**, não como falha — falha é o sistema
    quebrando, cancelamento é decisão de quem opera, e misturar os dois
    estraga o histórico.
    """
    run = await db.get(BackupRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="execução não encontrada")
    if run.status not in ("executando", "pendente"):
        raise HTTPException(
            status_code=400,
            detail=f"esta execução já terminou (situação: {run.status})",
        )

    vivo = request.app.state.backups.cancelar(run_id)

    run.status = "cancelado"
    run.stage = "Cancelado"
    run.progress = 100
    run.finished_at = datetime.now(timezone.utc)
    run.error = (
        "cancelado por quem opera"
        if vivo
        else "cancelado; a tarefa já não estava viva neste processo"
    )
    await db.commit()

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="backups.run",
        target=f"execução #{run_id}",
        ip=client_ip(request),
        detail={"acao": "cancelar", "tarefa_viva": vivo},
    )
    return {"ok": True, "tarefa_viva": vivo}


@router.delete("/backups-falhas")
async def limpar_falhas(
    request: Request,
    autor: User = Depends(require_permission("backups.delete")),
    db: AsyncSession = Depends(get_db),
):
    """
    Remove do histórico as execuções que falharam e não deixaram artefato.

    Falha sem artefato não tem nada a proteger: é linha de erro ocupando a
    tela. Apagar uma a uma, quando são dez, é trabalho que o painel devia
    fazer sozinho.

    **Não toca em execução com artefato** — essa continua no histórico com
    o registro do que existiu, mesmo depois de o arquivo sair pela
    retenção.
    """
    from sqlalchemy import delete as _delete

    # Falha sem artefato e "expirada" sem artefato são a mesma coisa na
    # prática: linha que não dá para usar nem para baixar. A segunda nascia
    # das tentativas de exclusão da versão anterior, que marcavam expirado
    # e deixavam o registro encalhado.
    resultado = await db.execute(
        _delete(BackupRun).where(
            (BackupRun.status == "falha") | (BackupRun.expired.is_(True)),
            (BackupRun.artifact_name == "") | (BackupRun.artifact_name.is_(None)),
        )
    )
    removidas = resultado.rowcount or 0
    await db.commit()

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="backups.delete",
        target="histórico de falhas",
        ip=client_ip(request),
        detail={"acao": "limpar falhas sem artefato", "removidas": removidas},
    )
    return {"ok": True, "removidas": removidas}


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
    pasta = host.name if host is not None else "_painel"
    removido = False
    resultados_destinos: list[dict] = []

    if run.artifact_name:
        # Apaga em TODOS os destinos onde o artefato foi parar, e não só na
        # cópia local: o mesmo arquivo seguia no Azure e no rclone depois
        # de "apagado" — lixo no lugar mais caro de guardar.
        from app.models.destino import Destino as _Destino

        nomes_destinos = {
            d.get("name") for d in (run.destinations or []) if d.get("name")
        }
        if nomes_destinos:
            objetos = list(
                (
                    await db.execute(
                        select(_Destino).where(_Destino.nome.in_(nomes_destinos))
                    )
                ).scalars().all()
            )
            for destino in objetos:
                resultados_destinos.append(
                    await request.app.state.storage.apagar(
                        destino, pasta, run.artifact_name
                    )
                )
            removido = any(r.get("ok") for r in resultados_destinos)

        # Rede de segurança: a cópia no disco do painel some mesmo que o
        # destino não esteja mais cadastrado.
        base = None
        for d in (run.destinations or []):
            if d.get("type") == "local" and d.get("uri"):
                base = str(Path(d["uri"]).parent.parent)
                break
        caminho = StorageService.caminho_artefato(pasta, run.artifact_name, base)
        if caminho is not None and caminho.is_file():
            try:
                caminho.unlink()
                removido = True
            except OSError as exc:
                raise HTTPException(
                    status_code=500, detail=f"falha ao apagar o arquivo: {exc}"
                ) from exc

    # Apagar apaga: a linha sai do histórico e o arquivo sai de todos os
    # destinos. A versão anterior marcava `expired` e deixava a linha na
    # tela — quem clicou em apagar leu isso como "não funcionou", e estava
    # certo: para quem opera, um registro que continua listado não foi
    # apagado.
    #
    # O histórico do que existiu não se perde: a auditoria registra a
    # remoção, com o arquivo, o destino e quem mandou. É lá que esse tipo
    # de rastro pertence, não numa linha fantasma no meio das execuções.
    await db.delete(run)
    apagou_linha = True
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
            "destinos": resultados_destinos,
        },
    )
    return {
        "ok": True,
        "arquivo_removido": removido,
        "linha_removida": apagou_linha,
        "destinos": resultados_destinos,
        "sobrou": [r for r in resultados_destinos if not r.get("ok")],
    }


class BackupTodosIn(BaseModel):
    """
    Backup de todos os servidores de uma vez.

    `perfil` aceita os perfis normais ou **"auto"**, que escolhe por
    servidor o mais completo que aquela máquina suporta. Auto é o padrão
    porque o ambiente é heterogêneo: exigir um perfil único obrigaria a
    escolher o menor denominador comum, e o menor denominador comum de um
    ambiente distribuído é quase sempre `config`.
    """

    perfil: str = "auto"
    destinos: list[int] = []


@router.post("/backups-todos", status_code=202)
async def disparar_todos(
    dados: BackupTodosIn,
    request: Request,
    autor: User = Depends(require_permission("backups.run")),
    db: AsyncSession = Depends(get_db),
):
    """
    Dispara backup em todos os servidores habilitados, um por servidor.

    Cada execução é independente e o artefato de cada uma vai para a pasta
    do seu servidor no destino — "todos" não mistura nada, só evita a ida
    manual em quatro telas.

    O perfil `completo` **não** entra no modo automático em hipótese
    alguma: ele para o FindFace, e parar quatro servidores porque alguém
    clicou em "todos" seria o painel decidindo uma janela de manutenção
    sozinho. Quem quer completo dispara servidor a servidor, com o aceite.

    Servidor onde o perfil não se aplica é **pulado com motivo**, não
    disparado para falhar em 0s.
    """
    if dados.perfil not in ("auto",) and dados.perfil not in PROFILES:
        raise HTTPException(
            status_code=400, detail=f"perfil deve ser 'auto' ou um de {PROFILES}"
        )
    if dados.perfil == "completo":
        raise HTTPException(
            status_code=400,
            detail=(
                "o perfil completo PARA o FindFace e não roda em lote. "
                "Dispare servidor a servidor, com o aceite da janela."
            ),
        )

    hosts = list(
        (await db.execute(select(Host).where(Host.enabled.is_(True)))).scalars().all()
    )
    if not hosts:
        raise HTTPException(status_code=400, detail="nenhum servidor habilitado")

    servico = request.app.state.backups
    disparados: list[dict] = []
    pulados: list[dict] = []

    for host in hosts:
        # O que esta máquina suporta, descoberto na hora.
        try:
            inventario = await request.app.state.descoberta.inventariar(host)
            texto = json.dumps(inventario, ensure_ascii=False).lower()
            tem_findface = bool(
                inventario.get("ffmulti_dir") or "findface" in texto or "ffmulti" in texto
            )
            tem_banco = any(
                c in texto for c in ("postgres", "timescale", "tarantool", "mongo")
            )
        except Exception as exc:
            pulados.append({
                "host": host.name,
                "motivo": f"não consegui inventariar: {exc}"[:200],
            })
            continue

        if not tem_findface:
            pulados.append({
                "host": host.name,
                "motivo": "não hospeda o FindFace — nada a copiar aqui",
            })
            continue

        if dados.perfil == "auto":
            perfil = "essencial" if tem_banco else "config"
        else:
            perfil = dados.perfil
            if perfil == "essencial" and not tem_banco:
                pulados.append({
                    "host": host.name,
                    "motivo": "sem banco do FindFace: o perfil essencial não teria "
                    "o que despejar",
                })
                continue

        run = BackupRun(
            host_id=host.id,
            profile=perfil,
            status="pendente",
            stage="Na fila",
            triggered_by=f"lote:{autor.username}",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)

        servico.registrar_tarefa(
            run.id,
            asyncio.create_task(
                _executar_em_fundo(
                    servico, host, perfil, list(dados.destinos), autor.username, run.id
                )
            ),
        )
        disparados.append({"host": host.name, "perfil": perfil, "run_id": run.id})

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="backups.run",
        target="todos os servidores",
        ip=client_ip(request),
        detail={
            "acao": "backup em lote",
            "perfil": dados.perfil,
            "disparados": disparados,
            "pulados": pulados,
        },
    )

    return {"disparados": disparados, "pulados": pulados}


async def _executar_em_fundo(
    servico, host, perfil: str, destinos: list[int], usuario: str, run_id: int
) -> None:
    """
    Cada execução do lote roda em tarefa própria, com sessão de banco
    própria — a sessão da requisição fecha quando a resposta sai.
    """
    from app.db.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        run = await db.get(BackupRun, run_id)
        try:
            await servico.executar(
                db,
                host,
                perfil,
                destinos,
                disparado_por=f"lote:{usuario}",
                run=run,
            )
        except Exception:
            # `executar` já grava a falha no registro; aqui é só rede de
            # segurança para a tarefa não morrer em silêncio no log.
            log_erro = f"lote: execução falhou em {host.name}"
            import logging

            logging.getLogger("faceops.backup").exception(log_erro)


@router.get("/backups/recuperacao")
async def plano_de_recuperacao(
    _: User = Depends(require_permission("backups.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    O que dá para recuperar, por servidor.

    A pergunta que só aparece no pior dia: *"se eu precisar voltar agora,
    tenho o quê, de quando, e o que falta?"*. Responder isso lendo o
    histórico linha a linha, no meio de um incidente, é o pior momento
    possível.

    Por servidor: o artefato mais recente por perfil, a idade dele, o que
    aquele perfil recupera e o que ele deixa de fora. Sem executar nada —
    o restore continua sendo procedimento manual, e o manifesto de cada
    artefato traz o roteiro do fabricante.
    """
    from datetime import datetime, timezone

    RECUPERA = {
        "config": {
            "recupera": "configuração da instalação (configs/ e o compose)",
            "nao_recupera": "banco, vetores faciais e fotos de evento",
        },
        "essencial": {
            "recupera": "configuração, bancos (PostgreSQL/Timescale), vetores do "
            "Tarantool, MongoDB e etcd",
            "nao_recupera": "as fotos de evento (findface-upload)",
        },
        "completo": {
            "recupera": "tudo: configuração, bancos e o diretório de dados inteiro",
            "nao_recupera": "nada, dentro do que o perfil copia",
        },
        "painel": {
            "recupera": "o próprio painel: cadastro, credenciais cifradas, "
            "agendamentos, histórico e auditoria",
            "nao_recupera": "a SECRET_KEY, que fica fora do artefato de propósito",
        },
    }

    hosts = list((await db.execute(select(Host))).scalars().all())
    nomes = {h.id: h.name for h in hosts}
    agora = datetime.now(timezone.utc)

    execucoes = list(
        (
            await db.execute(
                select(BackupRun)
                .where(BackupRun.status == "sucesso", BackupRun.expired.is_(False))
                .order_by(BackupRun.started_at.desc())
                .limit(400)
            )
        ).scalars().all()
    )

    def idade(run) -> dict:
        quando = run.finished_at or run.started_at
        if quando is None:
            return {"dias": None, "texto": "—"}
        dias = (agora - quando).days
        return {
            "dias": dias,
            "texto": "hoje" if dias == 0 else f"há {dias} dia(s)",
            "em": quando.isoformat(),
        }

    por_host: dict = {}
    for run in execucoes:
        chave = run.host_id or 0  # 0 = painel
        alvo = por_host.setdefault(
            chave,
            {
                "host_id": run.host_id,
                "servidor": nomes.get(run.host_id, "o próprio painel"),
                "perfis": {},
            },
        )
        # O primeiro de cada perfil é o mais recente: a consulta já veio
        # ordenada do mais novo para o mais velho.
        if run.profile in alvo["perfis"]:
            continue
        alvo["perfis"][run.profile] = {
            "run_id": run.id,
            "artefato": run.artifact_name,
            "tamanho": run.size_bytes,
            "checksum": run.checksum_sha256,
            "destinos": run.destinations or [],
            "idade": idade(run),
            **RECUPERA.get(run.profile, {}),
        }

    # Servidor habilitado sem nenhum backup é o achado que importa aqui.
    for host in hosts:
        if host.enabled and host.id not in por_host:
            por_host[host.id] = {
                "host_id": host.id,
                "servidor": host.name,
                "perfis": {},
                "aviso": "nenhum backup com sucesso disponível para este servidor",
            }

    lista = sorted(
        por_host.values(), key=lambda x: (x["host_id"] is None, x["servidor"])
    )

    return {
        "em": agora.isoformat(),
        "servidores": lista,
        "sem_backup": [x["servidor"] for x in lista if not x["perfis"]],
        "observacao": (
            "O restore é manual e por servidor: cada artefato volta na máquina "
            "de onde saiu. O manifesto dentro de cada um traz o roteiro do "
            "fabricante, e a base do Tarantool não é compatível entre versões "
            "maiores do FindFace."
        ),
    }


@router.get("/backups/estimativa/{host_id}")
async def estimativa(
    host_id: int,
    request: Request,
    _: User = Depends(require_permission("backups.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Quanto vai ocupar, e se cabe — antes de disparar.

    A pergunta certa antes de um backup é "cabe?", e ela vinha sendo
    respondida descobrindo. O perfil completo num servidor de
    reconhecimento facial passa de centenas de GB; descobrir isso no meio
    da cópia significa disco cheio em produção — o incidente que este
    painel existe para evitar.

    Duas fontes, e a segunda vale mais: a **medição no servidor**
    (`configs/`, diretório de dados, tamanho dos bancos) e o **tamanho real
    das execuções anteriores** daquele perfil naquele servidor. Nenhuma
    estimativa de compressão ganha de um número observado.

    Uma execução SSH, com `du` limitado por tempo — `du` numa árvore com
    milhões de fotos de evento é caro, e estourar o prazo vira "não
    medido", nunca um número inventado.
    """
    from app.services.estimativa_service import EstimativaError

    host = await _host_ou_404(db, host_id)

    staging = "/var/tmp/faceops"
    try:
        staging = request.app.state.config.get("servidores.staging_remoto")
    except Exception:
        pass

    try:
        medicao = await request.app.state.estimativa.medir(
            host, request.app.state.stack, staging
        )
    except EstimativaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Tamanho real da última execução de cada perfil neste servidor.
    historico: dict[str, int] = {}
    anteriores = (
        await db.execute(
            select(BackupRun)
            .where(
                BackupRun.host_id == host.id,
                BackupRun.status == "sucesso",
                BackupRun.size_bytes > 0,
            )
            .order_by(BackupRun.started_at.desc())
            .limit(30)
        )
    ).scalars().all()
    for run in anteriores:
        historico.setdefault(run.profile, run.size_bytes)

    perfis = request.app.state.estimativa.estimar(medicao, historico)
    local = StorageService.espaco_local()

    return {
        "host": host.name,
        "medicao": medicao,
        "perfis": perfis,
        "livre_no_painel": local.get("livre_bytes"),
        "staging": staging,
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
