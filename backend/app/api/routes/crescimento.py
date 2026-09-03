"""
Crescimento — o que está subindo, quando estoura e quem está empurrando.

Duas famílias de rota, e a diferença entre elas é o custo:

* **análise e listagem** leem só o banco (as amostras que o coletor já
  gravou). Podem ser chamadas à vontade — abrir a tela não custa nada ao
  ambiente;
* **rastrear** abre UMA execução SSH no servidor. É clique, nunca
  intervalo de tela.

Nenhuma rota aqui altera estado no servidor. O rastreio só lê.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.database import get_db
from app.models.crescimento import Crescimento
from app.models.host import Host
from app.models.user import User

router = APIRouter(prefix="/api/crescimento", tags=["crescimento"])


@router.get("/analise/{host_id}")
async def analise(
    host_id: int,
    request: Request,
    horas: float | None = Query(default=None, ge=1, le=168),
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Tendência dos três recursos deste servidor, com projeção e dano
    previsto. Só banco — nenhuma ida ao servidor.
    """
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")

    dados = await request.app.state.crescimento.analisar(db, host_id, horas)
    dados["host"] = host.name
    dados["rotulo"] = host.rotulo
    return dados


@router.get("/containers/{host_id}")
async def containers(
    host_id: int,
    request: Request,
    horas: float = Query(default=6, ge=0.25, le=720),
    pontos: int = Query(default=180, ge=30, le=2000),
    limite: int = Query(default=24, ge=1, le=60),
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Memória por container ao longo do tempo — a série que responde "qual
    container está comendo a RAM".

    Lê só o banco: a coleta já roda `docker stats` a cada passada, e a
    série é o que ela gravou. Vem ordenada por quem mais CRESCEU, não por
    quem mais ocupa — são perguntas diferentes, e a segunda já está na
    tela de Recursos.
    """
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")

    dados = await request.app.state.crescimento.serie_containers(
        db, host_id, horas=horas, pontos=pontos, limite=limite
    )
    dados["host"] = host.name
    dados["rotulo"] = host.rotulo
    return dados


@router.get("")
async def listar(
    request: Request,
    host_id: int | None = Query(default=None),
    dias: int = Query(default=7, ge=1, le=180),
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """Vigilâncias abertas agora, mais as encerradas na janela."""
    return {
        "vigilancias": await request.app.state.crescimento.listar(
            db, host_id=host_id, dias=dias
        )
    }


@router.get("/{vigilancia_id}")
async def detalhe(
    vigilancia_id: int,
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Uma vigilância com o rastreio inteiro.

    Separado da listagem de propósito: a lista de sete dias não deve
    carregar o diagnóstico de cada uma junto.
    """
    from app.services.crescimento_service import CrescimentoService

    vig = await db.get(Crescimento, vigilancia_id)
    if vig is None:
        raise HTTPException(status_code=404, detail="vigilância não encontrada")
    return CrescimentoService.como_dict(vig, com_diagnostico=True)


@router.post("/{vigilancia_id}/rastrear")
async def rastrear_vigilancia(
    vigilancia_id: int,
    request: Request,
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Rastreia o culpado agora, sem esperar o intervalo automático.

    Vale para vigilância **aberta**: quando ela fechou, o servidor já
    devolveu o recurso e o rastreio de agora não diz nada sobre o que
    aconteceu antes.
    """
    from app.services.crescimento_service import CrescimentoService

    vig = await db.get(Crescimento, vigilancia_id)
    if vig is None:
        raise HTTPException(status_code=404, detail="vigilância não encontrada")
    if vig.fim is not None:
        raise HTTPException(
            status_code=400,
            detail="esta vigilância já foi encerrada — o rastreio de agora "
                   "não descreveria o período dela",
        )

    host = await db.get(Host, vig.host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não cadastrado")

    servico = request.app.state.crescimento
    await servico.rastrear_e_gravar(db, host, vig, "")
    await db.commit()
    return CrescimentoService.como_dict(vig, com_diagnostico=True)


@router.post("/rastrear/{host_id}")
async def rastrear_host(
    host_id: int,
    request: Request,
    recurso: str = Query(default="disco", pattern="^(disco|memoria|swap)$"),
    ponto: str = Query(default="", max_length=64),
    _: User = Depends(require_permission("metrics.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Rastreio avulso, sem vigilância aberta — "quero saber agora quem está
    com o disco deste servidor".

    Uma execução SSH, só leitura, com `timeout` em cada comando. O `du`
    que não terminar no prazo vira "não medido", nunca um número
    inventado.
    """
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")

    return await request.app.state.crescimento.rastrear(host, recurso, ponto)
