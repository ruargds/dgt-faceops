"""
Diagnóstico — o que repete, o que o log está dizendo, e o que fazer.

Três leituras, todas baratas: reincidência é contagem sobre a tabela de
incidentes; padrões de log já vêm agrupados do analisador; o casamento com
o catálogo de erros conhecidos é feito em memória. A única rota que fala
com servidor é a de análise sob demanda, e ela é explícita no clique.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.database import get_db
from app.models.host import Host
from app.models.user import User
from app.services.catalogo_erros import CATALOGO

router = APIRouter(prefix="/api/diagnostico", tags=["diagnostico"])


@router.get("/reincidencia")
async def reincidencia(
    request: Request,
    dias: int = Query(default=14, ge=1, le=90),
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """Quem repete: quantas vezes, quanto tempo fora, em que horário e a tendência."""
    itens = await request.app.state.incidentes.reincidencia(db, dias=dias)

    # Nome do host junto, para a tela não precisar de outra consulta.
    r = await db.execute(select(Host.id, Host.name))
    nomes = {i: n for i, n in r.all()}
    for i in itens:
        i["host"] = nomes.get(i["host_id"], f"#{i['host_id']}")
    return {"dias": dias, "itens": itens}


@router.get("/padroes")
async def padroes(
    request: Request,
    host_id: int | None = Query(default=None),
    dias: int = Query(default=7, ge=1, le=90),
    _: User = Depends(require_permission("services.view")),
    db: AsyncSession = Depends(get_db),
):
    """Erros do log agrupados por molde, com causa e ação quando reconhecidos."""
    itens = await request.app.state.analise.listar(db, host_id=host_id, dias=dias)

    r = await db.execute(select(Host.id, Host.name))
    nomes = {i: n for i, n in r.all()}
    for i in itens:
        i["host"] = nomes.get(i["host_id"], f"#{i['host_id']}")
    return {"dias": dias, "itens": itens}


@router.post("/analisar/{host_id}")
async def analisar(
    host_id: int,
    request: Request,
    servico: str = Query(..., min_length=1, max_length=160),
    _: User = Depends(require_permission("services.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Análise sob demanda de um serviço específico.

    Esta rota **lê o log no servidor** — por isso é POST e por isso só
    roda no clique. A leitura automática do ciclo cobre apenas serviço com
    incidente aberto.
    """
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")

    analisados = await request.app.state.analise.analisar_servicos(
        db, host, [servico], forcar=True,
    )
    await db.commit()
    return {
        "ok": True,
        "analisados": analisados,
        "itens": await request.app.state.analise.listar(db, host_id=host_id, dias=7),
    }


@router.get("/catalogo")
async def catalogo(
    _: User = Depends(require_permission("services.view")),
):
    """
    O que o painel sabe reconhecer. Serve para a tela mostrar a base de
    conhecimento por inteiro, e não só quando um erro casa.
    """
    return {
        "itens": [
            {
                "chave": p["chave"],
                "titulo": p["titulo"],
                "causa": p["causa"],
                "acao": p["acao"],
                "onde": p["onde"],
                "fonte": p.get("fonte", ""),
            }
            for p in CATALOGO
        ]
    }
