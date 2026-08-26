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
            "amostra": None if a is None else {
                "ts": a.ts.isoformat(),
                "cpu": a.cpu_pct,
                "carga": a.carga_por_nucleo,
                "mem": a.mem_pct,
                "swap": a.swap_pct,
                "disco": a.disco_pct,
                "disco_ponto": a.disco_ponto,
                "disco_livre_gb": a.disco_livre_gb,
                "gpu": a.gpu_pct,
                "gpu_mem": a.gpu_mem_pct,
                "gpu_temp": a.gpu_temp,
                "cont_rodando": a.containers_rodando,
                "cont_total": a.containers_total,
                "cont_problema": a.containers_problema,
                "coleta_ms": a.coleta_ms,
                "erro": a.erro,
            },
        })

    return {
        "servidores": cartoes,
        "alertas": await request.app.state.monitor.alertas(db),
        "coletor": request.app.state.monitor.estado(),
    }
