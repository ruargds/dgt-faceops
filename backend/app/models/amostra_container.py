from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AmostraContainer(Base):
    """
    Memória e CPU de UM container num instante.

    A `Amostra` responde "quanto a máquina está usando"; esta responde
    **quem** está usando. Sem ela, o painel só sabia dizer que a memória
    subiu de 60% para 90% — e a pergunta seguinte, que é a única que leva
    a uma ação, ficava para alguém abrir Recursos no momento certo (isto
    é: nunca, porque o momento certo já passou).

    ## Por que isto não custa nada ao servidor

    O ciclo do monitor **já** lê `docker stats` a cada passada, para
    calcular os cartões e a memória por container da tela de Recursos. O
    resultado era descartado. Aqui ele passa a ser gravado — não há
    comando novo, nem SSH novo, nem um segundo por ciclo a mais.

    ## O que custa: linhas no banco

    Uma instalação do Face Detect tem ~30 containers. Gravar todos a
    cada 60 s, em quatro servidores, daria 172 mil linhas por dia — muito
    para o valor que entrega, porque memória de container não muda de
    forma interessante a cada minuto.

    Por isso a gravação tem **cadência própria** (`containers.intervalo_min`,
    padrão 5 min) e **retenção própria** (`containers.retencao_dias`,
    padrão 7 dias), independentes das da `Amostra`. Com os padrões: ~34
    mil linhas por dia, ~240 mil no total, na casa de dezenas de MB.

    Sete dias é curto de propósito. Esta série existe para responder "quem
    está comendo a memória AGORA e desde quando" — e não para ser o
    histórico de capacidade do ano, que continua sendo o da `Amostra`.
    """

    __tablename__ = "amostras_container"
    __table_args__ = (
        # "os últimos N minutos deste host" — o desenho do gráfico.
        Index("ix_amostras_ct_host_ts", "host_id", "ts"),
        # "a série DESTE container" — a linha que o filtro da tela seleciona
        # e a conta de quanto ele cresceu por hora.
        Index("ix_amostras_ct_host_nome_ts", "host_id", "nome", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Nome do container como o Docker reporta
    # ("findface-multi-findface-video-worker-1"). Repetido em cada linha,
    # sim: uma tabela de nomes economizaria uns MB e cobraria um JOIN em
    # toda leitura de gráfico — troca ruim nesta escala.
    nome: Mapped[str] = mapped_column(String(120), default="")

    # MB em vez de bytes: o gráfico lê em MB, e Float aqui ocupa o mesmo
    # que um inteiro grande.
    mem_mb: Mapped[float] = mapped_column(Float, default=0.0)
    # Percentual da memória da MÁQUINA (é o que o docker stats devolve),
    # para a linha do container ser comparável com a linha do host.
    mem_pct: Mapped[float] = mapped_column(Float, default=0.0)
    cpu_pct: Mapped[float] = mapped_column(Float, default=0.0)
