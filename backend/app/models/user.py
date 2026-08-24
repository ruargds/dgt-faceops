from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    hashed_password: Mapped[str] = mapped_column(String(255))

    # Marcado no seed inicial (admin/admin123). A UI mostra faixa de
    # aviso enquanto for True — sem bloquear o uso.
    senha_padrao: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # observador | operador | tecnico | admin  (ver core/permissions.py)
    role: Mapped[str] = mapped_column(String(32), default="observador")

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_super_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
