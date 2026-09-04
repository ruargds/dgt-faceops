from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AmostraDisco(Base):
    """
    IOPS e utilização de UM dispositivo de disco num instante.

    A `Amostra` guarda só o dispositivo mais castigado do host (ver
    `metrics_service.calcular_io`) — é o que decide o alerta. Esta tabela
    guarda TODOS os dispositivos lidos na mesma passada, para responder
    "qual disco, neste servidor, está saturado" quando há mais de um
    (comum em VM com disco de sistema e disco de dados separados).

    ## Por que isto não custa nada ao servidor

    O `/proc/diskstats` já é lido duas vezes por ciclo — antes e depois —
    para calcular IOPS (ver `calcular_io`). Hoje o resultado por
    dispositivo é jogado fora depois de escolher o pior; aqui ele passa a
    ser gravado. Nenhum comando novo, nenhuma leitura a mais.

    ## O que custa: linhas no banco

    Poucos dispositivos por servidor (1 a 5, tipicamente) — bem mais barato
    que a série de containers. Ainda assim a cadência e a retenção são
    próprias (`discos.intervalo_min`, `discos.retencao_dias`), pelo mesmo
    motivo da `AmostraContainer`: é uma série do presente ("qual disco está
    sofrendo agora"), não o histórico de capacidade do ano.
    """

    __tablename__ = "amostras_disco"
    __table_args__ = (
        Index("ix_amostras_disco_host_ts", "host_id", "ts"),
        Index("ix_amostras_disco_host_dispositivo_ts", "host_id", "dispositivo", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Nome do dispositivo como o kernel reporta ("sda", "nvme0n1") — já
    # sem partição, ver `_diskstats`.
    dispositivo: Mapped[str] = mapped_column(String(64), default="")

    iops: Mapped[float] = mapped_column(Float, default=0.0)
    leitura_ps: Mapped[float] = mapped_column(Float, default=0.0)
    escrita_ps: Mapped[float] = mapped_column(Float, default=0.0)
    util_pct: Mapped[float] = mapped_column(Float, default=0.0)
