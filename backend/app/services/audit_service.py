"""
Registro de auditoria.

Toda ação que muda estado num servidor de produção passa por aqui. O
registro é gravado ANTES de o resultado ser devolvido ao cliente — se o
painel cair no meio, fica o rastro de que a ação foi tentada.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import Text, cast, or_

from app.core.busca import condicao_de_busca
from app.core.permissions import DESTRUCTIVE_PERMISSIONS
from app.models.audit import AuditLog

# Chaves que nunca podem ser gravadas no detalhe da auditoria, venham de
# onde vierem. Auditoria é lida por mais gente do que o cofre.
CHAVES_PROIBIDAS = frozenset({
    "password", "senha", "ssh_key", "ssh_password", "sudo_password",
    "ssh_key_passphrase", "secret", "token", "access_token", "pem",
    "ff_api_pass",
})


def aplicar_filtros(
    consulta,
    *,
    busca: str | None = None,
    usuario: str | None = None,
    action: str | None = None,
    level: str | None = None,
    desde=None,
    ate=None,
    so_falhas: bool = False,
):
    """
    Os filtros da tela de auditoria, aplicados a uma consulta já montada.

    Vive aqui, e não na rota, porque **duas** rotas filtram: a listagem e
    a exportação. Cada uma com a sua cópia divergiria no primeiro filtro
    novo — e "exportar" que traz coisa diferente do que está na tela é
    pior do que não exportar, porque ninguém desconfia de um CSV.

    A busca livre varre usuário, ação, alvo e o detalhe. O detalhe é
    JSONB e entra por conversão para texto: é varredura, mas a tabela é
    pequena por construção (a faxina apaga além da retenção) e a consulta
    tem teto de linhas — o custo é de milissegundos, e é o único jeito de
    achar "por que o restore falhou" sem saber de cor a ação.
    """
    if busca and busca.strip():
        # Mesma régua da busca das telas (ver `core.busca` e a gêmea em
        # `frontend/src/utils/buscaInteligente.js`): começo de palavra por
        # padrão, `%` para qualquer posição, aspas para a palavra inteira,
        # vírgula separando termos. Digitar do mesmo jeito nos dois lugares
        # é o que evita a pergunta "por que ali achou e aqui não?".
        condicao = condicao_de_busca(
            [AuditLog.usuario, AuditLog.action, AuditLog.target,
             cast(AuditLog.detail, Text)],
            busca,
        )
        if condicao is not None:
            consulta = consulta.where(condicao)
    if usuario:
        consulta = consulta.where(AuditLog.usuario == usuario)
    if action:
        consulta = consulta.where(AuditLog.action == action)
    if level:
        consulta = consulta.where(AuditLog.level == level)
    if desde is not None:
        consulta = consulta.where(AuditLog.ts >= desde)
    if ate is not None:
        consulta = consulta.where(AuditLog.ts <= ate)
    if so_falhas:
        consulta = consulta.where(AuditLog.success.is_(False))
    return consulta


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
