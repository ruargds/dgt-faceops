from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LogPadrao(Base):
    """
    Um *tipo* de linha de log, não a linha.

    A diferença é o ponto inteiro desta tabela. Guardar log dá um segundo
    syslog — e o problema neste ambiente já é log demais (8 GB/dia de
    syslog num servidor real). Aqui guardamos o **molde**: a linha com id,
    timestamp, IP e número trocados por marcador, reduzida a uma
    impressão digital. Mil ocorrências do mesmo erro viram uma linha com
    `ocorrencias = 1000`.

    É isso que responde "o que mais repete neste servidor" sem custar
    espaço, e é o que o `catalogo_erros` usa para casar o erro com a causa
    já conhecida.
    """

    __tablename__ = "log_padroes"
    __table_args__ = (
        # Um molde por (host, serviço) — é a chave natural do upsert que o
        # analisador faz a cada leitura.
        Index("ix_log_padroes_unico", "host_id", "servico", "fingerprint", unique=True),
        Index("ix_log_padroes_visto", "host_id", "ultima_vez"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), index=True
    )
    servico: Mapped[str] = mapped_column(String(160), default="")

    # sha1 truncado do molde normalizado.
    fingerprint: Mapped[str] = mapped_column(String(32))
    # erro | aviso
    nivel: Mapped[str] = mapped_column(String(16), default="erro")
    # Chave do catálogo de erros conhecidos, quando algum casou.
    padrao_conhecido: Mapped[str] = mapped_column(String(64), default="")

    # O molde (com marcadores) e uma linha real de exemplo. O molde agrupa;
    # o exemplo é o que a pessoa precisa ler para entender.
    molde: Mapped[str] = mapped_column(String(400), default="")
    exemplo: Mapped[str] = mapped_column(String(600), default="")

    ocorrencias: Mapped[int] = mapped_column(Integer, default=0)
    primeira_vez: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ultima_vez: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
