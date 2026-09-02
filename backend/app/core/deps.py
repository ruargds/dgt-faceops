"""Dependências de autenticação e autorização."""
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import PERMISSION_CATALOG, permissions_for
from app.core.security import decode_access_token, sessao_expirada
from app.db.database import get_db
from app.models.user import User

bearer = HTTPBearer(auto_error=False)

# Padrões da sessão. Ficam aqui como fallback porque `deps` é carregado
# antes da configuração existir; o valor real vem de `ConfigService`.
SESSAO_INATIVIDADE_MIN = 20
SESSAO_MAXIMA_H = 24


def _cfg(request: Request, chave: str, padrao):
    """
    Valor do catálogo, pelo `app.state`. Sem singleton global: a
    configuração vive na aplicação, e `deps` recebe a requisição de
    graça — inventar um acessor de módulo só criaria um segundo lugar
    onde ela existe.
    """
    try:
        return request.app.state.config.get(chave)
    except Exception:
        return padrao


async def get_current_user(
    request: Request,
    credenciais: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credenciais is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticação necessária",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(credenciais.credentials)
    if payload is None or not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão expirada ou token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Teto absoluto de sessão. Conferido AQUI, e não só na renovação:
    # a renovação é um pedido do navegador, e regra de segurança que
    # depende do cliente pedir não é regra.
    if sessao_expirada(payload, float(_cfg(request, "sessao.maxima_h", SESSAO_MAXIMA_H))):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão atingiu o tempo máximo. Entre novamente.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    resultado = await db.execute(select(User).where(User.username == payload["sub"]))
    usuario = resultado.scalars().first()
    if usuario is None or not usuario.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário inativo ou removido",
        )

    # Token emitido antes de sair, trocar senha ou ser desativado deixa de
    # valer. Sem esta checagem, "sair" seria só apagar o token do
    # navegador — quem tivesse copiado antes continuaria dentro.
    # Sinal de que há alguém usando o painel. Fica aqui porque TODA rota
    # autenticada passa por este ponto — não precisa de middleware novo
    # nem de chamada espalhada. É o que decide a velocidade do coletor:
    # sem ninguém olhando, ele desacelera (ver `MonitorService.modo`).
    try:
        request.app.state.monitor.registrar_atividade()
    except Exception:
        pass

    if int(payload.get("tv", 0)) != usuario.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão encerrada. Entre novamente.",
        )
    return usuario


def require_permission(codigo: str):
    """
    Fábrica de dependência que exige uma permissão.

    Uso:  @router.post(..., dependencies=[Depends(require_permission("backups.run"))])
    """
    if codigo not in PERMISSION_CATALOG:
        raise ValueError(f"permissão fora do catálogo: {codigo}")

    async def _verificar(usuario: User = Depends(get_current_user)) -> User:
        if codigo not in permissions_for(usuario.role, usuario.is_super_admin):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Seu perfil ({usuario.role}) não tem a permissão "
                    f"'{codigo}' — {PERMISSION_CATALOG[codigo]}"
                ),
            )
        return usuario

    return _verificar


def client_ip(request: Request) -> str:
    """
    IP real do cliente. O painel roda atrás do nginx, então o socket
    sempre mostraria o IP do proxy — o log de auditoria e o freio de força
    bruta ficariam inúteis (todos os acessos com o mesmo IP).

    Com o painel exposto pela Cloudflare, a ordem de confiança importa:

    1. **CF-Connecting-IP** — a Cloudflare grava o IP real do visitante e
       SOBRESCREVE qualquer valor que o cliente tente injetar. É a fonte
       mais confiável quando o tráfego entra pela Cloudflare.
    2. **X-Forwarded-For** — só como reserva (acesso interno direto ao
       nginx). O primeiro item é o cliente por convenção, mas é forjável
       por quem alcança a origem sem passar pela Cloudflare.
    3. Socket — último recurso.

    IMPORTANTE (infra, não código): expor a origem direto na internet
    torna 1 e 2 forjáveis — basta bater no IP da máquina sem passar pela
    Cloudflare e mandar o cabeçalho na mão. A origem PRECISA aceitar
    conexão só da Cloudflare (Cloudflare Tunnel/cloudflared, ou firewall
    liberando apenas as faixas da Cloudflare). Sem isso, o freio de força
    bruta por IP é contornável.
    """
    cf = request.headers.get("cf-connecting-ip", "")
    if cf:
        return cf.strip()[:64]
    encaminhado = request.headers.get("x-forwarded-for", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]
