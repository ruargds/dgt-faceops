"""
Consumo de licença ao longo do tempo.

O limite que aperta neste ambiente não é câmera — são **objetos**. A
instalação levantada tem 453 dispositivos, todos entrando como detector
externo, e o recurso consumido é o `Objects TNT API`: 2,7 milhões de 8,4
milhões. Câmera licenciada em uso: zero de cinco.

Com isso, a pergunta que decide compra e capacidade deixa de ser "cabem
quantas câmeras?" e passa a ser **"em que ritmo consumimos objetos, e
quando acaba?"**. A licença responde o instante; só o histórico responde o
ritmo.

Deliberadamente estreita, como a tabela de amostras do monitor: uma linha
por recurso por dia, só número. Quatro servidores com meia dúzia de
recursos por 365 dias dão alguns milhares de linhas — nada.
"""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LicencaAmostra(Base):
    """Um retrato do consumo de um recurso licenciado."""

    __tablename__ = "licenca_amostras"
    __table_args__ = (
        # A consulta é sempre "este recurso, neste host, nos últimos N dias"
        Index("ix_licenca_host_recurso_ts", "host_id", "recurso", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    # Chave interna do recurso (`objects_tntapi`, `cameras`, `extapi`…). A
    # chave e não o rótulo: rótulo muda com tradução, chave não.
    recurso: Mapped[str] = mapped_column(String(64), index=True)

    # BigInteger porque `Objects TNT API` já está em milhões — int de 32
    # bits estouraria em instalação grande.
    usado: Mapped[int] = mapped_column(BigInteger, default=0)
    limite: Mapped[int] = mapped_column(BigInteger, default=0)
