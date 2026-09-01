from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LimiarOverride(Base):
    """
    Um limite de alerta fora do padrão global, para um host e/ou serviço
    específico.

    O catálogo em `config_service.py` continua sendo o padrão de fábrica —
    esta tabela só guarda EXCEÇÕES. `host_id=None` vale para todos os
    hosts; `servico=""` vale para o host inteiro (métrica de máquina, não
    de container). "Restaurar padrão" é simplesmente apagar a linha: sem
    override, `LimiarService.resolver` cai de volta no catálogo.
    """

    __tablename__ = "limiar_overrides"
    __table_args__ = (
        Index("ix_limiar_host_servico_chave", "host_id", "servico", "chave", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Vazio = limite do host inteiro (disco, memória, cpu, gpu). Preenchido
    # = limite específico de um serviço (reinícios, minutos indisponível).
    servico: Mapped[str] = mapped_column(String(160), default="")
    # Mesma chave usada no catálogo global, sem o prefixo "alerta.":
    # disco_pct | mem_pct | swap_pct | cpu_pct | gpu_mem_pct | gpu_temp |
    # servico_reinicios | servico_indisponivel_min
    chave: Mapped[str] = mapped_column(String(64))
    valor: Mapped[float] = mapped_column(Float)

    created_by: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
