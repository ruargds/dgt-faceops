"""
Registro de auditoria.

Toda ação que muda estado num servidor de produção passa por aqui. O
registro é gravado ANTES de o resultado ser devolvido ao cliente — se o
painel cair no meio, fica o rastro de que a ação foi tentada.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import DESTRUCTIVE_PERMISSIONS
from app.models.audit import AuditLog

# Chaves que nunca podem ser gravadas no detalhe da auditoria, venham de
# onde vierem. Auditoria é lida por mais gente do que o cofre.
CHAVES_PROIBIDAS = frozenset({
    "password", "senha", "ssh_key", "ssh_password", "sudo_password",
    "ssh_key_passphrase", "secret", "token", "access_token", "pem",
    "ff_api_pass",
})


def _limpar(detalhe: dict | None) -> dict:
    """Remove segredo do detalhe, inclusive aninhado."""
    if not detalhe:
        return {}
    saida: dict = {}
    for chave, valor in detalhe.items():
        if any(proibida in chave.lower() for proibida in CHAVES_PROIBIDAS):
            saida[chave] = "<omitido>"
        elif isinstance(valor, dict):
            saida[chave] = _limpar(valor)
        elif isinstance(valor, str) and len(valor) > 2000:
            saida[chave] = valor[:2000] + "…(truncado)"
        else:
            saida[chave] = valor
    return saida


async def registrar(
    db: AsyncSession,
    *,
    usuario: str,
    action: str,
    target: str = "",
    ip: str = "",
    success: bool = True,
    level: str | None = None,
    detail: dict | None = None,
) -> AuditLog:
    if level is None:
        if not success:
            level = "warning"
        elif action in DESTRUCTIVE_PERMISSIONS:
            level = "critical"
        else:
            level = "info"

    registro = AuditLog(
        usuario=usuario[:120],
        action=action[:64],
        target=target[:255],
        ip=ip[:64],
        success=success,
        level=level,
        detail=_limpar(detail),
    )
    db.add(registro)
    await db.commit()
    return registro
