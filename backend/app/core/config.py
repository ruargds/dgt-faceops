"""Configuração central — lida do ambiente (.env)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Banco do painel
    POSTGRES_USER: str = "faceops"
    POSTGRES_PASSWORD: str = "faceops"
    POSTGRES_DB: str = "faceops"
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432

    # Segurança
    # Placeholders conhecidos. Estão no repositório e no `.env.example`,
    # portanto são chave pública: quem os tiver forja token de admin e,
    # como o cofre Fernet deriva daqui, decifra toda credencial SSH
    # guardada. Ver `verificar_chave()`.
    SECRET_KEY: str = "dev-only-trocar"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # Só liga a documentação interativa (/api/docs) e o CORS de dev quando
    # ligado explicitamente. Em produção — e sobretudo com o painel exposto
    # pela Cloudflare — fica DESLIGADO: /api/docs abriria toda a superfície
    # da API para qualquer visitante, e o CORS de localhost não tem uso.
    MODO_DEV: bool = False
    ADMIN_USER: str = "admin"
    ADMIN_PASSWORD: str = "admin123"

    # Padrões do FindFace Multi nos hosts alvo
    FFMULTI_DIR: str = "/opt/findface-multi"
    FFMULTI_COMPOSE: str = "/opt/findface-multi/docker-compose.yaml"

    # Backups
    REMOTE_STAGING_DIR: str = "/var/backups/faceops"
    LOCAL_BACKUP_DIR: str = "/data/backups"
    RETENTION_CONFIG_DAYS: int = 90
    RETENTION_ESSENCIAL_DAYS: int = 30
    RETENTION_COMPLETO_DAYS: int = 180

    # Destinos remotos
    AZURE_STORAGE_CONNECTION_STRING: str = ""
    AZURE_BLOB_CONTAINER: str = "faceops-backups"
    RCLONE_REMOTE: str = "gdrive"
    RCLONE_PATH: str = "FaceOps/backups"

    # InTerminal
    TERMINAL_RECORD: bool = True
    TERMINAL_SESSION_DIR: str = "/data/sessions"
    TERMINAL_IDLE_TIMEOUT_MIN: int = 30

    TZ: str = "America/Sao_Paulo"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def database_url_sync(self) -> str:
        """APScheduler exige driver síncrono para o jobstore."""
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()


# Valores que NÃO podem chegar em produção: estão versionados no
# repositório e no `.env.example`.
CHAVES_PROIBIDAS = frozenset({
    "dev-only-trocar",
    "troque-esta-chave",
    "changeme",
    "secret",
    "",
})

TAMANHO_MINIMO_CHAVE = 32


def verificar_chave() -> str:
    """
    Motivo da recusa, ou string vazia se a chave serve.

    Falhar FECHADO aqui é a decisão certa, mesmo custando uma subida:
    com a chave de exemplo, qualquer pessoa que leia o repositório assina
    um token de administrador e, de quebra, decifra as chaves SSH dos
    quatro servidores de produção — o cofre Fernet deriva desta mesma
    chave. Um painel que sobe assim é pior que um painel que não sobe,
    porque parece funcionar.

    Em `MODO_DEV` só avisa: o ambiente de desenvolvimento precisa subir
    com o `.env.example` sem cerimônia.
    """
    chave = (settings.SECRET_KEY or "").strip()
    if chave.lower() in CHAVES_PROIBIDAS:
        return (
            "a SECRET_KEY ainda é o valor de exemplo. Ele está no "
            "repositório, então qualquer um assina um token de "
            "administrador e decifra as credenciais SSH guardadas."
        )
    if len(chave) < TAMANHO_MINIMO_CHAVE:
        return (
            f"a SECRET_KEY tem {len(chave)} caracteres; o mínimo é "
            f"{TAMANHO_MINIMO_CHAVE}. Chave curta é chave adivinhável."
        )
    return ""


def como_gerar_chave() -> str:
    """A instrução, para a mensagem de erro não deixar ninguém parado."""
    return (
        "Gere uma e coloque em SECRET_KEY no .env:\n"
        "    python -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
        "Guarde-a: trocar a SECRET_KEY torna ilegível toda credencial já "
        "gravada no cofre."
    )
