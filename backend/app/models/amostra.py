from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Amostra(Base):
    """
    Um instante de um servidor. Alimenta os gráficos.

    Deliberadamente **estreita**: só número, nada de texto nem JSON. Uma
    linha ocupa ~80 bytes. Quatro servidores a cada 60 s por 30 dias dão
    ~170 mil linhas — alguns MB. Guardar o retrato completo (containers,
    processos de GPU, lista de discos) multiplicaria isso por cem sem
    acrescentar nada a um gráfico.

    O detalhe continua vindo da coleta sob demanda, que lê a máquina no
    momento do clique. Aqui fica só o suficiente para desenhar a linha do
    tempo e disparar alerta.
    """

    __tablename__ = "amostras"
    __table_args__ = (
        # A consulta é sempre "as últimas N horas deste host"
        Index("ix_amostras_host_ts", "host_id", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    # Percentuais, 0–100. Float ocupa o mesmo que int aqui e evita
    # arredondar cedo demais.
    # `cpu_pct` é a CARGA por núcleo em percentual (fila), mantida pelo
    # histórico já gravado. `cpu_uso_pct` é a OCUPAÇÃO real da CPU — as
    # duas coisas divergem, e confundi-las é o clássico "a máquina está a
    # 80%" quando 80% era fila de espera de disco.
    cpu_pct: Mapped[float] = mapped_column(Float, default=0.0)
    cpu_uso_pct: Mapped[float] = mapped_column(Float, default=0.0)
    carga_por_nucleo: Mapped[float] = mapped_column(Float, default=0.0)
    mem_pct: Mapped[float] = mapped_column(Float, default=0.0)
    swap_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # Valor ABSOLUTO ao lado do percentual. "78,8%" não diz se sobra 1 GB
    # ou 40 GB, e é a sobra que decide se dá para esperar até amanhã.
    # Cinco números a mais por amostra (~40 bytes) — 30 dias de 4
    # servidores continuam na casa de poucos MB.
    mem_total_mb: Mapped[float] = mapped_column(Float, default=0.0)
    mem_usado_mb: Mapped[float] = mapped_column(Float, default=0.0)

    # Do disco guardamos só o pior: é o que dispara alerta. Qual disco é
    # fica no campo ao lado, para a tela poder dizer "/ em 94%".
    disco_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # Saturação de disco — o que faltava para ver a queda chegando.
    #
    # Ocupação em GB e `iowait` da CPU NÃO enxergam isto: dá para estourar
    # o teto de IOPS do provedor com o disco quase vazio. Ao encostar no
    # teto, o disco enfileira, a latência explode e tudo que toca disco
    # trava junto — inclusive o systemd e o sshd, e a máquina parece ter
    # caído quando está só esperando o disco.
    disco_iops: Mapped[float] = mapped_column(Float, default=0.0)
    disco_util_pct: Mapped[float] = mapped_column(Float, default=0.0)
    disco_ponto: Mapped[str] = mapped_column(String(64), default="")
    disco_livre_gb: Mapped[float] = mapped_column(Float, default=0.0)
    disco_total_gb: Mapped[float] = mapped_column(Float, default=0.0)

    # Zero quando o host não tem GPU
    gpu_pct: Mapped[float] = mapped_column(Float, default=0.0)
    gpu_mem_pct: Mapped[float] = mapped_column(Float, default=0.0)
    gpu_mem_total_mb: Mapped[float] = mapped_column(Float, default=0.0)
    gpu_mem_usado_mb: Mapped[float] = mapped_column(Float, default=0.0)
    gpu_temp: Mapped[float] = mapped_column(Float, default=0.0)

    containers_total: Mapped[int] = mapped_column(Integer, default=0)
    containers_rodando: Mapped[int] = mapped_column(Integer, default=0)
    containers_problema: Mapped[int] = mapped_column(Integer, default=0)

    # Quanto a coleta custou. Serve para provar que o monitoramento não
    # está pesando — e para perceber quando um servidor começa a demorar.
    coleta_ms: Mapped[int] = mapped_column(Integer, default=0)

    # Vazio quando deu certo. Falha vira amostra também: buraco no
    # gráfico é informação, e some se a gente não gravar nada.
    erro: Mapped[str] = mapped_column(String(255), default="")
