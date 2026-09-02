"""Login, sessão e gestão de usuários."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import bearer, client_ip, get_current_user, require_permission
from app.core.permissions import (
    PERMISSION_CATALOG,
    ROLE_LABELS,
    matriz_perfis,
    permissions_for,
)
from app.core.rate_limit import freio
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    sessao_expirada,
    verify_password,
)
from app.db.database import get_db
from app.models.user import User
from app.schemas import (
    LoginIn,
    MeOut,
    TokenOut,
    TrocarSenhaIn,
    UsuarioIn,
    UsuarioOut,
    UsuarioUpdate,
)
from app.services import audit_service

router = APIRouter(prefix="/api/auth", tags=["auth"])



def _inatividade_min(request: Request) -> int:
    """Janela de inatividade, do catálogo."""
    try:
        return int(request.app.state.config.get("sessao.inatividade_min"))
    except Exception:
        return 20


def _maxima_h(request: Request) -> float:
    try:
        return float(request.app.state.config.get("sessao.maxima_h"))
    except Exception:
        return 24.0

@router.post("/login", response_model=TokenOut)
async def login(
    dados: LoginIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip = client_ip(request)
    tentado = dados.username.strip().lower()

    # Freio antes de tocar no banco: negar cedo custa menos e nao entrega
    # tempo de resposta diferente para usuario que existe.
    barrado, faltam = freio.bloqueado(ip, tentado)
    if barrado:
        await audit_service.registrar(
            db, usuario=tentado[:120], action="auth.login", success=False,
            ip=ip, level="warning",
            detail={"motivo": "bloqueado por excesso de tentativas"},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Muitas tentativas. Tente de novo em {max(1, faltam // 60)} "
                "minuto(s)."
            ),
        )

    resultado = await db.execute(select(User).where(User.username == tentado))
    usuario = resultado.scalars().first()

    # Mensagem única para usuário inexistente e senha errada — dizer qual
    # dos dois falhou entrega a lista de usuários válidos a quem tenta.
    if usuario is None or not verify_password(dados.password, usuario.hashed_password):
        restantes = freio.registrar_falha(ip, tentado)
        await audit_service.registrar(
            db,
            usuario=tentado[:120],
            action="auth.login",
            success=False,
            ip=ip,
            detail={"motivo": "credenciais inválidas", "restantes": restantes},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Usuário ou senha inválidos"
                + (f" — restam {restantes} tentativa(s)" if restantes <= 2 else "")
            ),
        )

    if not usuario.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Usuário desativado"
        )

    freio.registrar_sucesso(ip, tentado)
    usuario.last_login_at = datetime.now(timezone.utc)
    await db.commit()

    await audit_service.registrar(
        db,
        usuario=usuario.username,
        action="auth.login",
        ip=ip,
        detail={"perfil": usuario.role},
    )

    return TokenOut(
        access_token=create_access_token(
            usuario.username,
            {"role": usuario.role, "tv": usuario.token_version},
            minutos=_inatividade_min(request),
        ),
        usuario=UsuarioOut.model_validate(usuario),
    )


@router.post("/sair")
async def sair(
    request: Request,
    usuario: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Encerra a sessão do lado do SERVIDOR.

    Incrementa a versão do token, o que invalida imediatamente qualquer
    token emitido antes — inclusive um que tenha sido copiado do
    localStorage por outra pessoa. Sem isto, "sair" seria apenas apagar
    o token do próprio navegador.

    Derruba todas as sessões do usuário, em todos os dispositivos. É o
    comportamento certo para um painel que dá acesso a servidor de
    produção: na dúvida, encerra tudo.
    """
    usuario.token_version += 1
    await db.commit()

    await audit_service.registrar(
        db, usuario=usuario.username, action="auth.sair", ip=client_ip(request),
    )
    return {"ok": True, "mensagem": "Sessão encerrada em todos os dispositivos."}


@router.get("/me", response_model=MeOut)
async def me(usuario: User = Depends(get_current_user)):
    return MeOut(
        usuario=UsuarioOut.model_validate(usuario),
        permissoes=sorted(permissions_for(usuario.role, usuario.is_super_admin)),
    )


@router.get("/catalogo")
async def catalogo(_: User = Depends(get_current_user)):
    """Catálogo de permissões e perfis — a tela de usuários monta a partir daqui."""
    return {"permissoes": PERMISSION_CATALOG, "perfis": ROLE_LABELS}


@router.post("/trocar-senha")
async def trocar_senha(
    dados: TrocarSenhaIn,
    request: Request,
    usuario: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(dados.senha_atual, usuario.hashed_password):
        raise HTTPException(status_code=400, detail="Senha atual incorreta")

    if dados.senha_nova == dados.senha_atual:
        raise HTTPException(status_code=400, detail="A senha nova é igual à atual")

    usuario.hashed_password = hash_password(dados.senha_nova)
    usuario.senha_padrao = False
    # Trocar senha porque suspeita de vazamento nao adianta se o token
    # antigo continuar valendo.
    usuario.token_version += 1
    await db.commit()

    await audit_service.registrar(
        db,
        usuario=usuario.username,
        action="auth.trocar_senha",
        ip=client_ip(request),
    )
    # Token novo junto: a troca invalidou o anterior, e sem devolver um
    # novo a tela cairia para o login logo apos trocar a senha.
    return {
        "ok": True,
        "mensagem": "Senha alterada. As outras sessões foram encerradas.",
        "access_token": create_access_token(
            usuario.username,
            {"role": usuario.role, "tv": usuario.token_version},
            minutos=_inatividade_min(request),
        ),
    }


# ── Gestão de usuários ─────────────────────────────────────────────────


@router.get("/usuarios", response_model=list[UsuarioOut])
async def listar_usuarios(
    _: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    resultado = await db.execute(select(User).order_by(User.username))
    return [UsuarioOut.model_validate(u) for u in resultado.scalars().all()]


@router.post("/usuarios", response_model=UsuarioOut, status_code=201)
async def criar_usuario(
    dados: UsuarioIn,
    request: Request,
    autor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    login_novo = dados.username.strip().lower()
    if dados.role not in ROLE_LABELS:
        raise HTTPException(
            status_code=400, detail=f"perfil inválido. Use um de: {list(ROLE_LABELS)}"
        )

    existente = await db.execute(select(User).where(User.username == login_novo))
    if existente.scalars().first() is not None:
        raise HTTPException(status_code=409, detail=f"usuário '{login_novo}' já existe")

    usuario = User(
        username=login_novo,
        full_name=dados.full_name,
        hashed_password=hash_password(dados.password),
        role=dados.role,
        is_active=True,
        is_super_admin=False,
        senha_padrao=False,
    )
    db.add(usuario)
    await db.commit()
    await db.refresh(usuario)

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="users.manage",
        target=login_novo,
        ip=client_ip(request),
        detail={"acao": "criar", "perfil": dados.role},
    )
    return UsuarioOut.model_validate(usuario)


@router.patch("/usuarios/{usuario_id}", response_model=UsuarioOut)
async def atualizar_usuario(
    usuario_id: int,
    dados: UsuarioUpdate,
    request: Request,
    autor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    alvo = await db.get(User, usuario_id)
    if alvo is None:
        raise HTTPException(status_code=404, detail="usuário não encontrado")

    if dados.role is not None:
        if dados.role not in ROLE_LABELS:
            raise HTTPException(status_code=400, detail="perfil inválido")
        alvo.role = dados.role
    if dados.full_name is not None:
        alvo.full_name = dados.full_name
    if dados.is_active is not None:
        # Trancar-se para fora do painel é um chamado de suporte garantido
        if alvo.id == autor.id and not dados.is_active:
            raise HTTPException(
                status_code=400, detail="você não pode desativar a própria conta"
            )
        alvo.is_active = dados.is_active
        if not dados.is_active:
            # Desativar tem que derrubar a sessao aberta, nao so impedir
            # o proximo login.
            alvo.token_version += 1
    if dados.password:
        alvo.hashed_password = hash_password(dados.password)
        alvo.senha_padrao = False
        alvo.token_version += 1

    await db.commit()
    await db.refresh(alvo)

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="users.manage",
        target=alvo.username,
        ip=client_ip(request),
        detail={"acao": "atualizar", "campos": [
            c for c in ("role", "full_name", "is_active", "password")
            if getattr(dados, c) is not None
        ]},
    )
    return UsuarioOut.model_validate(alvo)


@router.delete("/usuarios/{usuario_id}")
async def remover_usuario(
    usuario_id: int,
    request: Request,
    autor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    alvo = await db.get(User, usuario_id)
    if alvo is None:
        raise HTTPException(status_code=404, detail="usuário não encontrado")
    if alvo.id == autor.id:
        raise HTTPException(status_code=400, detail="você não pode remover a própria conta")
    if alvo.is_super_admin:
        raise HTTPException(status_code=400, detail="o super admin não pode ser removido")

    nome = alvo.username
    await db.delete(alvo)
    await db.commit()

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="users.manage",
        target=nome,
        ip=client_ip(request),
        level="critical",
        detail={"acao": "remover"},
    )
    return {"ok": True}


@router.post("/renovar")
async def renovar(
    request: Request,
    autor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Estende a sessão por mais uma janela de inatividade.

    Chamada pela TELA, e só quando houve interação de gente desde a
    última renovação. Essa condição é o coração da regra: o painel se
    atualiza sozinho a cada 10 s, e se qualquer requisição renovasse a
    sessão, uma tela esquecida aberta ficaria logada para sempre — o
    tempo de inatividade nunca chegaria ao fim.

    O `ini` do token atual é COPIADO para o novo, nunca recalculado: é
    ele que faz o teto de 24 h ser um teto, e não um horizonte que se
    afasta a cada renovação.
    """
    credenciais = await bearer(request)
    payload = decode_access_token(credenciais.credentials) if credenciais else None
    if payload is None:
        raise HTTPException(status_code=401, detail="Sessão inválida.")

    maxima = _maxima_h(request)
    if sessao_expirada(payload, maxima):
        raise HTTPException(
            status_code=401,
            detail=f"Sessão atingiu o limite de {maxima:.0f}h. Entre novamente.",
        )

    inicio = int(payload.get("ini") or datetime.now(timezone.utc).timestamp())
    restante_h = max(0.0, maxima - (datetime.now(timezone.utc).timestamp() - inicio) / 3600)

    return {
        "access_token": create_access_token(
            autor.username,
            {"role": autor.role, "tv": autor.token_version},
            minutos=_inatividade_min(request),
            inicio_sessao=inicio,
        ),
        "inatividade_min": _inatividade_min(request),
        "maxima_h": maxima,
        # Quanto ainda resta do teto — a tela avisa antes de derrubar, em
        # vez de encerrar no meio de um formulário sem explicação.
        "restante_h": round(restante_h, 2),
    }


@router.get("/sessao")
async def politica_sessao(
    request: Request,
    _: User = Depends(get_current_user),
):
    """
    Os prazos em vigor, para a tela não ter os números repetidos no
    JavaScript. Configuração que existe em dois lugares diverge no
    primeiro ajuste.
    """
    return {
        "inatividade_min": _inatividade_min(request),
        "maxima_h": _maxima_h(request),
    }


@router.get("/perfis")
async def perfis(_: User = Depends(get_current_user)):
    """
    O que cada perfil pode ver e fazer.

    Aberta a qualquer usuário autenticado de propósito: saber o que o
    PRÓPRIO perfil permite não é informação privilegiada, e esconder isso
    só gera a pergunta "por que não aparece o botão?" — que vira chamado.
    """
    return matriz_perfis()
