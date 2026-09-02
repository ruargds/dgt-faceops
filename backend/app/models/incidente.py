from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Incidente(Base):
    """
    Uma janela de indisponibilidade — de um host inteiro ou de um serviço
    (container) dentro dele.

    Aberto e fechado pelo próprio ciclo do monitor contínuo
    (`MonitorService._amostrar`), sem nenhuma consulta extra: o ciclo já lê
    o estado dos containers a cada passada para calcular os cartões, aqui só
    aproveitamos esse resultado para saber quando algo entrou e saiu de
    problema.

    `fim=None` é o incidente aberto. Fechar é só carimbar `fim` — não existe
    "editar" um incidente depois de criado.
    """

    __tablename__ = "incidentes"
    __table_args__ = (
        # A pergunta mais comum é "o que está aberto agora" e "o que
        # aconteceu com este host nos últimos dias" — os dois cabem neste
        # índice composto.
        #
        # O índice de `inicio` NÃO entra aqui: a coluna já é declarada com
        # `index=True`, e o SQLAlchemy gera para ela o nome
        # `ix_incidentes_inicio`. Declarar os dois emitia dois CREATE INDEX
        # com o mesmo nome na subida — o segundo falhava, o startup do
        # FastAPI morria junto e o painel nunca respondia (deploy de
        # 01/09/2026, revertido automaticamente pelo atualizar.sh).
        Index("ix_incidentes_host_fim", "host_id", "fim"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), index=True
    )

    # host | servico — "host" é quando a máquina inteira ficou sem
    # contato (a.erro); "servico" é um container específico com problema.
    tipo: Mapped[str] = mapped_column(String(16), default="servico")
    # Nome do container/serviço; vazio para incidente de host inteiro.
    servico: Mapped[str] = mapped_column(String(160), default="")

    nivel: Mapped[str] = mapped_column(String(16), default="atencao")
    texto: Mapped[str] = mapped_column(String(255), default="")
    # Hipótese heurística — nunca afirmativa, sempre "provável": ver
    # `incidente_service._causa_provavel`.
    causa_provavel: Mapped[str] = mapped_column(String(255), default="")

    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    fim: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Carimbada no fechamento, para não recalcular em toda listagem.
    duracao_s: Mapped[float] = mapped_column(Float, default=0.0)

    # Apuração da causa, feita UMA vez quando o incidente fecha (ver
    # `apuracao_service`). Mora aqui, e não em tabela própria, por dois
    # motivos: é um-para-um com o incidente, e assim a retenção de
    # incidentes já a recicla — nenhuma faxina nova.
    #
    # JSONB no Postgres, JSON simples em outro banco: o tipo do dialeto
    # Postgres não compila no SQLite, e a decisão que interpreta esses
    # dados precisa ser testável.
    apuracao: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    # Nulo = nunca apurado. Diferente de apurado sem achar nada, que
    # grava `apuracao` com veredito "não encontrei evidência" — a
    # distinção existe para a tela não oferecer "apurar" de novo à toa.
    apurado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
