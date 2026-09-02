"""
Configuração de aviso por Telegram — bot, destinos e regras.

Desenho em duas peças, como no Zabbix, no Grafana e no Alertmanager:
**destino** é para onde, **regra** é o que mandar para lá. Ver
`notificacao_service`.

Segurança, em três regras que valem para todas as rotas daqui:

1. **O token nunca sai.** Entra por PUT, é cifrado com Fernet e some. As
   respostas trazem o nome do bot e a impressão digital — o suficiente
   para saber QUAL token está guardado, nunca o token.
2. **Só administrador.** Configurar aviso é decidir quem recebe alarme de
   produção; fica em `users.manage`, junto de usuários e configuração.
3. **Tudo auditado.** Trocar conta, criar/mudar/apagar destino e regra
   viram registro em auditoria, com autor e IP.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_permission
from app.core.vault import decrypt_secret, encrypt_secret, fingerprint
from app.db.database import get_db
from app.models.host import Host
from app.models.notificacao import (
    NotificacaoConta, NotificacaoDestino, NotificacaoRegra,
)
from app.models.user import User
from app.services import audit_service, telegram_service
from app.services.notificacao_service import TIPOS, TIPOS_VALIDOS, NotificacaoService

router = APIRouter(prefix="/api/notificacoes", tags=["notificacoes"])

NIVEIS = ("atencao", "critico")
TIPOS_DESTINO = ("grupo", "individual")


def _conta_publica(conta: NotificacaoConta | None) -> dict:
    """O que a tela pode ver. Nunca inclui o token."""
    if conta is None:
        return {"configurado": False, "ativo": False}
    return {
        "configurado": bool(conta.bot_token_enc),
        "ativo": conta.ativo,
        "bot_nome": conta.bot_nome,
        "token_fingerprint": conta.token_fingerprint,
        # `updated_at` só existe depois que a linha vai ao banco; antes
        # disso é None, e a tela não pode quebrar por causa do carimbo.
        "atualizado_em": conta.updated_at.isoformat() if conta.updated_at else None,
        "atualizado_por": conta.created_by,
    }


def _destino_publico(d: NotificacaoDestino) -> dict:
    return {
        "id": d.id, "nome": d.nome, "tipo": d.tipo, "chat_id": d.chat_id,
        "ativo": d.ativo, "observacao": d.observacao,
    }


# ── O bot ──────────────────────────────────────────────────────────────


@router.get("/conta")
async def ver_conta(
    _: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    return _conta_publica(await NotificacaoService.conta(db))


class ContaIn(BaseModel):
    # Vazio = manter o token atual. Trocar de conta é mandar um novo.
    bot_token: str = Field(default="", max_length=200)
    ativo: bool = True


@router.put("/conta")
async def salvar_conta(
    dados: ContaIn,
    request: Request,
    autor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    """
    Cria ou troca o bot de envio.

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

    conta.ativo = dados.ativo
    conta.created_by = autor.username

    if conta.ativo and not conta.bot_token_enc:
        raise HTTPException(status_code=400, detail="informe o token do bot antes de habilitar o envio")

    await db.flush()
    await audit_service.registrar(
        db, usuario=autor.username, action="notificacao.conta", ip=client_ip(request),
        detail={"bot": conta.bot_nome, "ativo": conta.ativo, "token_trocado": bool(token_novo)},
    )
    await db.commit()
    return _conta_publica(conta)


@router.get("/chats")
async def chats(
    _: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    """
    Quais chats já falaram com o bot — para escolher em vez de digitar id.

    Resolve o passo mais chato da configuração. O que **não** dá para
    automatizar: adicionar o bot ao grupo, e a pessoa mandar `/start`.
    Isso é limite do Telegram, não do painel.
    """
    conta = await NotificacaoService.conta(db)
    if conta is None or not conta.bot_token_enc:
        raise HTTPException(status_code=400, detail="configure o token do bot primeiro")
    try:
        achados = await telegram_service.descobrir_chats(decrypt_secret(conta.bot_token_enc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"o Telegram recusou: {exc}") from None

    existentes = {d.chat_id for d in await NotificacaoService.destinos(db)}
    for c in achados:
        c["ja_cadastrado"] = c["chat_id"] in existentes
    return {"chats": achados}


# ── Destinos ───────────────────────────────────────────────────────────


@router.get("/destinos")
async def listar_destinos(
    _: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    return {
        "destinos": [_destino_publico(d) for d in await NotificacaoService.destinos(db)],
        "tipos": list(TIPOS_DESTINO),
    }


class DestinoIn(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    tipo: str = "grupo"
    chat_id: str = Field(min_length=1, max_length=64)
    ativo: bool = True
    observacao: str = Field(default="", max_length=255)


@router.put("/destinos")
async def salvar_destino(
    dados: DestinoIn,
    request: Request,
    autor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Cria ou atualiza um destino. O `chat_id` é a chave natural."""
    if dados.tipo not in TIPOS_DESTINO:
        raise HTTPException(status_code=400, detail=f"tipo inválido: {dados.tipo}")

    chat_id = dados.chat_id.strip()
    r = await db.execute(select(NotificacaoDestino).where(NotificacaoDestino.chat_id == chat_id))
    destino = r.scalars().first()
    if destino is None:
        destino = NotificacaoDestino(chat_id=chat_id)
        db.add(destino)

    destino.nome = dados.nome.strip()
    destino.tipo = dados.tipo
    destino.ativo = dados.ativo
    destino.observacao = dados.observacao.strip()
    destino.created_by = autor.username
    await db.flush()

    await audit_service.registrar(
        db, usuario=autor.username, action="notificacao.destino", ip=client_ip(request),
        detail={"nome": destino.nome, "tipo": destino.tipo, "ativo": destino.ativo},
    )
    await db.commit()
    return _destino_publico(destino)


@router.delete("/destinos/{destino_id}")
async def remover_destino(
    destino_id: int,
    request: Request,
    autor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    """
    Apaga o destino. As regras que apontavam só para ele vão junto
    (`ON DELETE CASCADE`) — regra órfã que não manda para lugar nenhum
    seria configuração fantasma.
    """
    r = await db.execute(delete(NotificacaoDestino).where(NotificacaoDestino.id == destino_id))
    if not r.rowcount:
        raise HTTPException(status_code=404, detail="destino não encontrado")

    await audit_service.registrar(
        db, usuario=autor.username, action="notificacao.destino",
        target=str(destino_id), ip=client_ip(request), detail={"acao": "remover"},
    )
    await db.commit()
    return {"ok": True}


@router.post("/testar")
async def testar(
    request: Request,
    destino_id: int | None = Query(default=None),
    autor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    """
    Manda uma mensagem agora — para um destino, ou para todos.

    É o que prova o caminho inteiro: token válido, chat existente e bot com
    permissão de escrever ali.
    """
    try:
        resultado = await request.app.state.notificacoes.enviar_teste(
            db, autor.username, destino_id=destino_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"o Telegram recusou: {exc}") from None
    await db.commit()
    return resultado


# ── Regras ─────────────────────────────────────────────────────────────


@router.get("/regras")
async def listar_regras(
    _: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    """
    As regras, o catálogo de tipos de evento e o de servidores/serviços
    para montar a tela.

    A lista de serviços vem do que o coletor já viu
    (`hosts.servicos_conhecidos`) — sem SSH: esta é uma tela de
    configuração, e ela não pode custar uma ida a quatro servidores de
    produção só para desenhar caixas de seleção.
    """
    r = await db.execute(select(NotificacaoRegra).order_by(NotificacaoRegra.id))
    regras = [
        {
            "id": x.id, "destino_id": x.destino_id, "host_id": x.host_id,
            "servico": x.servico, "tipos": list(x.tipos or []),
            "nivel_minimo": x.nivel_minimo, "atraso_s": x.atraso_s,
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
    return {
        "regras": regras,
        "hosts": hosts,
        "niveis": list(NIVEIS),
        "tipos_evento": TIPOS,
        "destinos": [_destino_publico(d) for d in await NotificacaoService.destinos(db)],
    }


class RegraIn(BaseModel):
    id: int | None = None
    # Nulo = todos os destinos ativos.
    destino_id: int | None = None
    host_id: int | None = None
    servico: str = Field(default="", max_length=160)
    tipos: list[str] = Field(default_factory=list)
    nivel_minimo: str = "critico"
    atraso_s: int = Field(default=0, ge=0, le=86400)
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

    desconhecidos = [t for t in dados.tipos if t not in TIPOS_VALIDOS]
    if desconhecidos:
        raise HTTPException(status_code=400, detail=f"tipo de evento desconhecido: {desconhecidos}")

    if dados.host_id is not None and await db.get(Host, dados.host_id) is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")
    if dados.destino_id is not None and await db.get(NotificacaoDestino, dados.destino_id) is None:
        raise HTTPException(status_code=404, detail="destino não encontrado")

    regra = None
    if dados.id is not None:
        regra = await db.get(NotificacaoRegra, dados.id)
        if regra is None:
            raise HTTPException(status_code=404, detail="regra não encontrada")
    if regra is None:
        regra = NotificacaoRegra()
        db.add(regra)

    regra.destino_id = dados.destino_id
    regra.host_id = dados.host_id
    regra.servico = (dados.servico or "").strip()
    regra.tipos = list(dict.fromkeys(dados.tipos))
    regra.nivel_minimo = dados.nivel_minimo
    regra.atraso_s = dados.atraso_s
    regra.ativo = dados.ativo
    regra.created_by = autor.username
    await db.flush()

    await audit_service.registrar(
        db, usuario=autor.username, action="notificacao.regra", ip=client_ip(request),
        detail={
            "destino_id": regra.destino_id, "host_id": regra.host_id,
            "servico": regra.servico, "tipos": regra.tipos,
            "nivel": regra.nivel_minimo, "atraso_s": regra.atraso_s,
            "ativo": regra.ativo,
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
    """O que já foi mandado, e para qual destino — é a resposta para 'não recebi'."""
    return {"envios": await NotificacaoService.ultimos(db, limite=limite)}
