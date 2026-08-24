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
    SECRET_KEY: str = "dev-only-trocar"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
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
