"""
Monitoramento contínuo — séries, alertas e estado do coletor.

Estas rotas são consultadas com frequência pela tela, então cada uma foi
feita para ser barata: leem do banco, nunca tocam num servidor. A ida ao
servidor acontece só no coletor de fundo, no ritmo dele.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.database import get_db
from app.models.amostra import Amostra
from app.models.host import Host
from app.models.user import User
from app.services.monitor_service import MonitorService

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


@router.get("/estado")
async def estado(
    request: Request,
    _: User = Depends(require_permission("metrics.view")),
):
    """Se o coletor está de pé, há quanto tempo e com que intervalo."""
    return request.app.state.monitor.estado()


@router.get("/alertas")
async def alertas(
    request: Request,
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Alertas ativos, derivados da última amostra de cada host.

    Consultado pela tela a cada poucos segundos — por isso lê do banco e
    não do servidor.
    """
    return {"alertas": await request.app.state.monitor.alertas(db)}


@router.get("/serie/{host_id}")
async def serie(
    host_id: int,
    horas: int = Query(default=6, ge=1, le=720),
    pontos: int = Query(default=240, ge=30, le=2000),
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Histórico de um servidor, já reduzido ao número de pontos da tela.

    Reduzir aqui evita mandar dez mil amostras para desenhar 240 pixels.
    """
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")

    dados = await MonitorService.serie(db, host_id, horas, pontos)
    dados["host"] = host.name
    dados["tem_gpu"] = host.has_gpu
    dados["monitorado"] = host.monitorar
    return dados


@router.get("/resumo")
async def resumo(
    request: Request,
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Última amostra de cada servidor, para a visão geral.

    Uma consulta só: a tela precisa de todos os cartões de uma vez, e N+1
    aqui seria N+1 a cada poucos segundos.
    """
    resultado = await db.execute(select(Host).order_by(Host.name))
    hosts = list(resultado.scalars().all())

    cartoes = []
    for host in hosts:
        r = await db.execute(
            select(Amostra)
            .where(Amostra.host_id == host.id)
            .order_by(Amostra.ts.desc())
            .limit(1)
        )
        a = r.scalars().first()

        cartoes.append({
            "host_id": host.id,
            "host": host.name,
            "papel": host.role,
            "endereco": host.address,
            "ativo": host.enabled,
            "monitorado": host.monitorar,
            "tem_gpu": host.has_gpu,
            "gpu_nome": host.gpu_nome,
            "amostra": None if a is None else {
                "ts": a.ts.isoformat(),
                "cpu": a.cpu_pct,
                "cpu_uso": a.cpu_uso_pct if a.cpu_uso_pct > 0 else None,
                "carga": a.carga_por_nucleo,
                "mem": a.mem_pct,
                "mem_total_mb": a.mem_total_mb,
                "mem_usado_mb": a.mem_usado_mb,
                "swap": a.swap_pct,
                "disco": a.disco_pct,
                "disco_ponto": a.disco_ponto,
                "disco_livre_gb": a.disco_livre_gb,
                "disco_total_gb": a.disco_total_gb,
                "gpu": a.gpu_pct,
                "gpu_mem": a.gpu_mem_pct,
                "gpu_mem_total_mb": a.gpu_mem_total_mb,
                "gpu_mem_usado_mb": a.gpu_mem_usado_mb,
                "gpu_temp": a.gpu_temp,
                "cont_rodando": a.containers_rodando,
                "cont_total": a.containers_total,
                "cont_problema": a.containers_problema,
                "coleta_ms": a.coleta_ms,
                "erro": a.erro,
            },
        })

    incidentes_abertos = []
    if hasattr(request.app.state, "incidentes"):
        incidentes_abertos = await request.app.state.incidentes.listar_abertos(db)

    return {
        "servidores": cartoes,
        "alertas": await request.app.state.monitor.alertas(db),
        "coletor": request.app.state.monitor.estado(),
        # Serviço por máquina, já com causa provável e desde quando — a
        # tela desenha o painel de indisponibilidade sem outra ida ao
        # servidor: os dados já saem juntos do mesmo poll de 10s.
        "incidentes_abertos": incidentes_abertos,
        "painel": await _resumo_do_painel(db),
    }


async def _resumo_do_painel(db: AsyncSession) -> dict:
    """
    A faixa de resumo do topo do Monitor.

    Tudo daqui é **local**: uma consulta no banco do painel e uma leitura
    de disco da própria VM. Nenhum SSH. Isso é deliberado — o Monitor é a
    tela inicial e atualiza a cada 10 s; se esta faixa fosse buscar em
    `/api/painel` (que faz `docker ps` por SSH em cada servidor), abrir o
    painel passaria a bater nas VMs de produção o tempo todo, exatamente
    o que o monitor contínuo foi desenhado para não fazer.

    Sobre permissão: a rota é liberada por `metrics.view` e devolve também
    backup e disco do painel, que são de `backups.view`. Hoje isso não
    expõe nada — **todo** perfil do catálogo que tem `metrics.view` tem
    `backups.view` junto (há um cenário em `tests/verificar.py` que trava
    essa premissa). Se algum dia existir um perfil só de métrica, é aqui
    que precisa entrar o filtro.
    """
    from app.services.backup_service import BackupService
    from app.services.storage_service import StorageService

    try:
        ultimos = await BackupService.ultimo_por_host(db)
        com_falha = sum(1 for u in ultimos.values() if u and u.status != "sucesso")
        sem_backup = 0
        r = await db.execute(select(Host).where(Host.enabled.is_(True)))
        for h in r.scalars().all():
            if h.id not in ultimos:
                sem_backup += 1
    except Exception:
        com_falha, sem_backup = 0, 0

    try:
        disco = StorageService.espaco_local()
    except Exception:
        disco = None

    return {
        "backups_com_falha": com_falha,
        "servidores_sem_backup": sem_backup,
        "armazenamento": disco,
    }


@router.get("/incidentes/recentes")
async def incidentes_recentes_resumo(
    request: Request,
    dias: int = Query(default=3, ge=1, le=30),
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Atalho de conveniência: os mesmos dados de `/api/incidentes/recentes`,
    sob o prefixo do monitor — assim a tela de Monitor usa um domínio só.
    """
    return {
        "incidentes": await request.app.state.incidentes.listar_recentes(db, dias=dias)
    }


@router.get("/pico")
async def horario_de_pico(
    host_id: int = Query(...),
    dias: int = Query(default=14, ge=1, le=90),
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Uso médio por hora do dia, nos últimos N dias — de onde vem o pico de
    cada métrica. Nenhum modelo, nenhuma chamada externa: é uma agregação
    sobre o histórico que o monitor já grava, com o mesmo custo de uma
    consulta de tela comum.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import extract, func

    desde = datetime.now(timezone.utc) - timedelta(days=dias)
    resultado = await db.execute(
        select(
            extract("hour", Amostra.ts).label("hora"),
            func.avg(Amostra.cpu_uso_pct).label("cpu"),
            func.avg(Amostra.mem_pct).label("mem"),
            func.avg(Amostra.gpu_pct).label("gpu"),
            func.count(Amostra.id).label("amostras"),
        )
        .where(Amostra.host_id == host_id, Amostra.ts >= desde)
        .group_by("hora")
        .order_by("hora")
    )
    linhas = resultado.all()
    horas = {int(h): {"cpu": round(c or 0, 1), "mem": round(m or 0, 1), "gpu": round(g or 0, 1), "amostras": n}
             for h, c, m, g, n in linhas}
    return {
        "host_id": host_id,
        "dias": dias,
        "horas": [horas.get(h, {"cpu": None, "mem": None, "gpu": None, "amostras": 0}) for h in range(24)],
    }
