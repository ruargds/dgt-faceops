"""Configuração do painel, editável pela web."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_permission
from app.db.database import get_db
from app.models.user import User
from app.services import audit_service
from app.services.config_service import CATEGORIAS

router = APIRouter(prefix="/api/config", tags=["configuracao"])


class AlterarIn(BaseModel):
    # {"backup.retencao_essencial": 45, "terminal.gravar": true}
    valores: dict[str, object]


@router.get("")
async def listar(
    request: Request,
    _: User = Depends(require_permission("hosts.view")),
):
    """
    Catálogo com os valores atuais, agrupado por categoria.

    A tela se monta a partir daqui — rótulo, tipo, validação e ajuda vêm
    todos do catálogo, então adicionar uma opção nova não exige mexer no
    frontend.
    """
    itens = request.app.state.config.tudo()
    grupos = []
    for chave, (titulo, descricao) in CATEGORIAS.items():
        do_grupo = [i for i in itens if i["categoria"] == chave]
        if do_grupo:
            grupos.append({
                "categoria": chave,
                "titulo": titulo,
                "descricao": descricao,
                "itens": do_grupo,
            })
    return {"grupos": grupos}


@router.get("/publico")
async def publico(request: Request):
    """
    Identidade do painel, sem autenticação.

    A tela de login precisa do nome e do subtítulo antes de existir
    sessão. Só devolve isso — nenhuma outra configuração sai daqui.
    """
    cfg = request.app.state.config
    return {
        "nome": cfg.get("projeto.nome"),
        "subtitulo": cfg.get("projeto.subtitulo"),
        "cliente": cfg.get("projeto.cliente"),
    }


@router.patch("")
async def alterar(
    dados: AlterarIn,
    request: Request,
    autor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    """
    Altera várias opções de uma vez.

    Valida TODAS antes de gravar qualquer uma: salvar metade e recusar a
    outra metade deixaria o painel num estado que ninguém pediu.
    """
    cfg = request.app.state.config

    erros: list[str] = []
    for chave, valor in dados.valores.items():
        from app.services.config_service import POR_CHAVE

        item = POR_CHAVE.get(chave)
        if item is None:
            erros.append(f"opção desconhecida: {chave}")
            continue
        ok, erro = item.validar(valor)
        if not ok:
            erros.append(erro)

    if erros:
        raise HTTPException(status_code=400, detail="; ".join(erros))

    alterados: list[str] = []
    for chave, valor in dados.valores.items():
        ok, erro = await cfg.definir(db, chave, valor, autor.username)
        if ok:
            alterados.append(chave)

    await db.commit()

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="config.alterar",
        ip=client_ip(request),
        detail={"chaves": alterados},
    )
    return {"ok": True, "alterados": alterados}


@router.delete("/{chave:path}")
async def restaurar_padrao(
    chave: str,
    request: Request,
    autor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Volta uma opção ao padrão (do `.env` ou do catálogo)."""
    from app.services.config_service import POR_CHAVE

    if chave not in POR_CHAVE:
        raise HTTPException(status_code=404, detail=f"opção desconhecida: {chave}")

    await request.app.state.config.restaurar_padrao(db, chave)
    await db.commit()

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="config.alterar",
        target=chave,
        ip=client_ip(request),
        detail={"acao": "restaurar padrao"},
    )
    return {"ok": True, "valor": request.app.state.config.get(chave)}
