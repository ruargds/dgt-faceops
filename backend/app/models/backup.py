from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Perfis de backup — ver docs/02_ESTRATEGIA_BACKUP.md
PROFILE_CONFIG = "config"        # configs/ + docker-compose.yaml + licença
PROFILE_ESSENCIAL = "essencial"  # + pg_dump + snapshot Tarantool (quente)
PROFILE_COMPLETO = "completo"    # procedimento oficial NtechLab (frio)

PROFILES = (PROFILE_CONFIG, PROFILE_ESSENCIAL, PROFILE_COMPLETO)


class BackupRun(Base):
    """Uma execução de backup — sob demanda ou disparada por agendamento."""

    __tablename__ = "backup_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True, index=True
    )

    profile: Mapped[str] = mapped_column(String(16), index=True)

    # pendente | executando | sucesso | falha | cancelado
    status: Mapped[str] = mapped_column(String(16), default="pendente", index=True)
    # Etapa corrente, para a barra de progresso da UI
    stage: Mapped[str] = mapped_column(String(64), default="")
    progress: Mapped[int] = mapped_column(Integer, default=0)

    artifact_name: Mapped[str] = mapped_column(String(255), default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    checksum_sha256: Mapped[str] = mapped_column(String(64), default="")

    # [{"type":"local|azure|gdrive","uri":"...","status":"ok|erro","error":"..."}]
    destinations: Mapped[list] = mapped_column(JSONB, default=list)

    # Se o stack precisou ser parado (perfil completo)
    caused_downtime: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    downtime_seconds: Mapped[int] = mapped_column(Integer, default=0)

    triggered_by: Mapped[str] = mapped_column(String(255), default="")
    log: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Marcado quando a retenção já apagou os artefatos
    expired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Schedule(Base):
    """Recorrência programada — o que falta na plataforma da NtechLab."""

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    profile: Mapped[str] = mapped_column(String(16))

    # Expressão cron de 5 campos (min hora dia mês diadasemana), fuso do painel
    cron: Mapped[str] = mapped_column(String(64))

    # ["local","azure","gdrive"]
    destinations: Mapped[list] = mapped_column(JSONB, default=list)
    retention_days: Mapped[int] = mapped_column(Integer, default=30)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Perfil completo para o stack — exige aceite explícito da janela
    allow_downtime: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str] = mapped_column(String(16), default="")
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
