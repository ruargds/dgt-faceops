"""
Configuração de aviso por Telegram.

Segurança, em três regras que valem para todas as rotas daqui:

1. **O token nunca sai.** Entra por PUT, é cifrado com Fernet e some. As
   respostas trazem o nome do bot e a impressão digital — o suficiente
   para saber QUAL token está guardado, nunca o token.
2. **Só administrador.** Configurar aviso é decidir quem recebe alarme de
   produção; fica em `users.manage`, junto de usuários e configuração.
3. **Tudo auditado.** Trocar conta, mudar regra e apagar regra viram
   registro em auditoria, com autor e IP.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_permission
from app.core.vault import encrypt_secret, fingerprint
from app.db.database import get_db
from app.models.host import Host
from app.models.notificacao import NotificacaoConta, NotificacaoRegra
from app.models.user import User
from app.services import audit_service, telegram_service
from app.services.notificacao_service import NotificacaoService

router = APIRouter(prefix="/api/notificacoes", tags=["notificacoes"])

NIVEIS = ("atencao", "critico")


def _conta_publica(conta: NotificacaoConta | None) -> dict:
    """O que a tela pode ver. Nunca inclui o token."""
    if conta is None:
        return {"configurado": False, "ativo": False}
    return {
        "configurado": bool(conta.bot_token_enc),
        "ativo": conta.ativo,
        "bot_nome": conta.bot_nome,
        "chat_id": conta.chat_id,
        "token_fingerprint": conta.token_fingerprint,
        # `updated_at` só existe depois que a linha vai ao banco; antes
        # disso é None, e a tela não pode quebrar por causa do carimbo.
        "atualizado_em": conta.updated_at.isoformat() if conta.updated_at else None,
        "atualizado_por": conta.created_by,
    }


@router.get("/conta")
async def ver_conta(
    _: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    return _conta_publica(await NotificacaoService.conta(db))


class ContaIn(BaseModel):
    # Vazio = manter o token atual. Trocar de conta é mandar um novo.
    bot_token: str = Field(default="", max_length=200)
    chat_id: str = Field(default="", max_length=64)
    ativo: bool = True


@router.put("/conta")
async def salvar_conta(
    dados: ContaIn,
    request: Request,
    autor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    """
    Cria ou troca a conta de envio.

    Quando vem token novo, ele é validado no Telegram (`getMe`) ANTES de
    ser guardado: token errado tem que falhar aqui, na tela de
    configuração, e não silenciosamente às 3h da manhã quando algo cair.
    """
    conta = await NotificacaoService.conta(db)
    if conta is None:
        conta = NotificacaoConta()
        db.add(conta)

    token_novo = (dados.bot_token or "").strip()
    if token_novo:
        try:
            eu = await telegram_service.quem_sou(token_novo)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"token recusado pelo Telegram: {exc}") from None
        conta.bot_nome = str(eu.get("username") or eu.get("first_name") or "")[:120]
        conta.bot_token_enc = encrypt_secret(token_novo)
        conta.token_fingerprint = fingerprint(token_novo)

    if dados.chat_id:
        conta.chat_id = dados.chat_id.strip()
    conta.ativo = dados.ativo
    conta.created_by = autor.username

    if conta.ativo and not (conta.bot_token_enc and conta.chat_id):
        raise HTTPException(status_code=400, detail="informe o token do bot e o id do grupo antes de ativar")

    await db.flush()
    await audit_service.registrar(
        db, usuario=autor.username, action="notificacao.conta", ip=client_ip(request),
        detail={
            "bot": conta.bot_nome, "chat_id": conta.chat_id, "ativo": conta.ativo,
            "token_trocado": bool(token_novo),
        },
    )
    await db.commit()
    return _conta_publica(conta)


@router.post("/testar")
async def testar(
    request: Request,
    autor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Manda uma mensagem agora — prova que token e grupo estão certos."""
    try:
        resultado = await request.app.state.notificacoes.enviar_teste(db, autor.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"o Telegram recusou: {exc}") from None
    await db.commit()
    return resultado


@router.get("/regras")
async def listar_regras(
    _: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    """
    As regras e o catálogo de servidores/serviços para montar a tela.

    A lista de serviços vem do que o coletor já viu em cada host
    (`hosts.servicos_conhecidos`) — sem SSH: esta é uma tela de
    configuração, e ela não pode custar uma ida a quatro servidores de
    produção só para desenhar caixas de seleção.
    """
    r = await db.execute(select(NotificacaoRegra).order_by(NotificacaoRegra.host_id))
    regras = [
        {
            "id": x.id, "host_id": x.host_id, "servico": x.servico,
            "nivel_minimo": x.nivel_minimo, "avisar_retorno": x.avisar_retorno,
            "ativo": x.ativo,
        }
        for x in r.scalars().all()
    ]

    rh = await db.execute(select(Host).where(Host.enabled.is_(True)).order_by(Host.name))
    hosts = [
        {
            "id": h.id, "nome": h.name, "papel": h.role,
            "servicos": sorted(h.servicos_conhecidos or []),
        }
        for h in rh.scalars().all()
    ]
    return {"regras": regras, "hosts": hosts, "niveis": list(NIVEIS)}


class RegraIn(BaseModel):
    host_id: int | None = None
    servico: str = Field(default="", max_length=160)
    nivel_minimo: str = "critico"
    avisar_retorno: bool = True
    ativo: bool = True


@router.put("/regras")
async def salvar_regra(
    dados: RegraIn,
    request: Request,
    autor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    if dados.nivel_minimo not in NIVEIS:
        raise HTTPException(status_code=400, detail=f"nível inválido: {dados.nivel_minimo}")
    if dados.host_id is not None and await db.get(Host, dados.host_id) is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")

    servico = (dados.servico or "").strip()
    r = await db.execute(
        select(NotificacaoRegra).where(
            NotificacaoRegra.host_id == dados.host_id,
            NotificacaoRegra.servico == servico,
        )
    )
    regra = r.scalars().first()
    if regra is None:
        regra = NotificacaoRegra(host_id=dados.host_id, servico=servico)
        db.add(regra)

    regra.nivel_minimo = dados.nivel_minimo
    regra.avisar_retorno = dados.avisar_retorno
    regra.ativo = dados.ativo
    regra.created_by = autor.username
    await db.flush()

    await audit_service.registrar(
        db, usuario=autor.username, action="notificacao.regra", ip=client_ip(request),
        detail={
            "host_id": dados.host_id, "servico": servico,
            "nivel": regra.nivel_minimo, "ativo": regra.ativo,
        },
    )
    await db.commit()
    return {"ok": True, "id": regra.id}


@router.delete("/regras/{regra_id}")
async def remover_regra(
    regra_id: int,
    request: Request,
    autor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(delete(NotificacaoRegra).where(NotificacaoRegra.id == regra_id))
    if not r.rowcount:
        raise HTTPException(status_code=404, detail="regra não encontrada")

    await audit_service.registrar(
        db, usuario=autor.username, action="notificacao.regra",
        target=str(regra_id), ip=client_ip(request), detail={"acao": "remover"},
    )
    await db.commit()
    return {"ok": True}


@router.get("/envios")
async def envios(
    limite: int = Query(default=30, ge=1, le=200),
    _: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    """O que já foi mandado — é a resposta para 'não recebi'."""
    return {"envios": await NotificacaoService.ultimos(db, limite=limite)}
