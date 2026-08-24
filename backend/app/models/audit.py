from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AuditLog(Base):
    """
    Registro de auditoria. Toda ação que muda estado em servidor de
    produção entra aqui — inclusive quem abriu terminal e o que rodou.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    usuario: Mapped[str] = mapped_column(String(120), default="", index=True)
    ip: Mapped[str] = mapped_column(String(64), default="")

    # ex: "services.restart", "backups.restore", "terminal.open"
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(255), default="")

    # info | warning | critical
    level: Mapped[str] = mapped_column(String(16), default="info", index=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    detail: Mapped[dict] = mapped_column(JSONB, default=dict)


class TerminalSession(Base):
    """Sessão do InTerminal, com gravação em asciicast v2 para auditoria."""

    __tablename__ = "terminal_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    usuario: Mapped[str] = mapped_column(String(120), index=True)
    ip: Mapped[str] = mapped_column(String(64), default="")

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    recording_path: Mapped[str] = mapped_column(String(512), default="")
    bytes_in: Mapped[int] = mapped_column(Integer, default=0)
    bytes_out: Mapped[int] = mapped_column(Integer, default=0)

    sudo_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    end_reason: Mapped[str] = mapped_column(String(64), default="")
    error: Mapped[str] = mapped_column(Text, default="")
