"""Dependências de autenticação e autorização."""
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import PERMISSION_CATALOG, permissions_for
from app.core.security import decode_access_token
from app.db.database import get_db
from app.models.user import User

bearer = HTTPBearer(auto_error=False)


async def get_current_user(
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
