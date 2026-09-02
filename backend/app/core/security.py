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


def create_access_token(
    subject: str,
    extra: dict | None = None,
    minutos: int | None = None,
    inicio_sessao: int | None = None,
) -> str:
    """
    Token com **dois relógios**, e é a separação que faz a regra existir:

    * `exp` — a janela de inatividade. Curta (20 min por padrão). Cada
      interação de gente renova este relógio.
    * `ini` — o instante em que a sessão COMEÇOU, carregado de renovação
      em renovação sem nunca ser recalculado. É ele que impõe o teto
      absoluto: passadas 24 h, nem interação contínua segura a sessão.

    Guardar só o `exp` daria uma sessão eterna para quem ficasse mexendo;
    guardar só o `ini` daria uma sessão que morre no meio do trabalho.
    """
    agora = datetime.now(timezone.utc)
    janela = minutos if minutos is not None else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    payload = {
        "sub": subject,
        "exp": agora + timedelta(minutes=max(1, int(janela))),
        "ini": int(inicio_sessao if inicio_sessao is not None else agora.timestamp()),
        **(extra or {}),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_algoritmo())


def sessao_expirada(payload: dict, maxima_h: float) -> bool:
    """
    A sessão passou do teto absoluto?

    Vale mesmo com `exp` válido: é exatamente o caso que o teto existe
    para cobrir — alguém trabalhando sem parar por mais de 24 h, ou um
    token renovado indefinidamente por um script.
    """
    if maxima_h <= 0:
        return False
    inicio = payload.get("ini")
    if not inicio:
        # Token da versão anterior, sem o carimbo. Não derruba quem já
        # estava dentro: a próxima renovação passa a carregar o `ini`.
        return False
    idade_h = (datetime.now(timezone.utc).timestamp() - float(inicio)) / 3600
    return idade_h >= maxima_h


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
