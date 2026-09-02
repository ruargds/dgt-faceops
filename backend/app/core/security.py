"""
Hash de senha e emissão/validação de JWT.

**Por que PyJWT e não python-jose.** O projeto usava `python-jose` 3.3.0,
que carrega duas falhas conhecidas e sem correção publicada:

* **CVE-2024-33663** — confusão de algoritmo com chave ECDSA no formato
  OpenSSH. Aqui já era barrada, porque `decode` sempre recebeu a lista
  fixa `algorithms=[...]`, mas depender de um cuidado do chamador para
  neutralizar falha da biblioteca é frágil: basta uma chamada nova
  esquecer a lista.
* **CVE-2024-33664** — negação de serviço por token JWE inflado. Esta
  **não** tinha mitigação do nosso lado: o painel decodifica token vindo
  de quem chama, em toda requisição autenticada, e é exatamente esse o
  caminho do ataque.

`PyJWT` é mantido, tem o mesmo formato de token na saída (HS256), e a
troca não invalida nenhuma sessão em aberto — o que já foi emitido
continua válido até expirar.
"""
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Algoritmos aceitos na verificação. Lista fechada, e não
# `settings.ALGORITHM`, para que configuração errada não consiga abrir a
# porta: "none" ou um algoritmo assimétrico aqui viraria token forjável.
ALGORITMOS_ACEITOS = ("HS256", "HS384", "HS512")


def _algoritmo() -> str:
    escolhido = (settings.ALGORITHM or "").upper()
    return escolhido if escolhido in ALGORITMOS_ACEITOS else "HS256"


def hash_password(plain: str) -> str:
    # bcrypt trunca em 72 bytes e ERRA acima disso em versões novas.
    # Cortar aqui mantém o comportamento previsível para senha longa —
    # sem isso, o cadastro falharia com erro de biblioteca.
    return pwd_context.hash(plain[:72])


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain[:72], hashed)
    except Exception:
        # Hash corrompido ou de esquema desconhecido não pode virar 500 na
        # tela de login: é falha de autenticação, e nada mais.
        return False


def create_access_token(subject: str, extra: dict | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {"sub": subject, "exp": expire, **(extra or {})}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_algoritmo())


def decode_access_token(token: str) -> dict | None:
    """Retorna o payload, ou None se o token for inválido/expirado."""
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=list(ALGORITMOS_ACEITOS),
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None
