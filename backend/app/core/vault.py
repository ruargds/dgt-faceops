"""
Cofre criptográfico para segredos de acesso aos hosts.

Guarda chave PEM e senha de sudo com Fernet (AES-128-CBC + HMAC-SHA256)
derivado da SECRET_KEY — mesmo esquema já validado no DGT InfraCore.

Regra dura: o valor em claro só existe em memória durante a conexão SSH.
Nunca é logado, nunca é serializado numa resposta de API, nunca vai para
o cofre de auditoria.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _get_fernet() -> Fernet:
    """Deriva uma chave Fernet determinística a partir da SECRET_KEY."""
    raw = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt_secret(plain: str) -> str:
    """Criptografa um segredo. Retorna string base64 (vazio vira vazio)."""
    if not plain:
        return ""
    return _get_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(encrypted: str) -> str:
    """
    Descriptografa um segredo.

    Levanta ValueError se a SECRET_KEY mudou desde a gravação — falha
    explícita é melhor do que tentar conectar com lixo e culpar a rede.
    """
    if not encrypted:
        return ""
    try:
        return _get_fernet().decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "Segredo ilegível: a SECRET_KEY mudou desde que foi gravado. "
            "Recadastre a credencial do host."
        ) from exc


def fingerprint(plain: str) -> str:
    """
    Impressão digital curta de um segredo, para a UI confirmar QUAL chave
    está cadastrada sem jamais exibir a chave.
    """
    if not plain:
        return ""
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()[:16]
