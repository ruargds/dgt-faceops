from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VisaoLog(Base):
    """
    Uma visão salva de log ao vivo.

    Substitui o `docker logs -f | jq -r '…'` que cada um guarda no
    histórico do próprio shell: o filtro vira configuração compartilhada,
    fica versionado no banco e qualquer um da equipe abre com um clique.

    Os campos descrevem O QUE mostrar de cada linha JSON. A formatação
    acontece no navegador — nada de expressão do usuário perto de um
    shell remoto.
    """

    __tablename__ = "visoes_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    descricao: Mapped[str] = mapped_column(String(255), default="")

    # Opcional: visão presa a um servidor. Vazio = serve para qualquer um.
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    container: Mapped[str] = mapped_column(String(128), default="")
    tail: Mapped[int] = mapped_column(Integer, default=200)

    # [{"caminho": "context.dgtId", "rotulo": "dgtId",
    #   "corte_inicio": null, "corte_fim": null}]
    # Caminho com ponto navega objeto aninhado, como no jq.
    campos: Mapped[list] = mapped_column(JSONB, default=list)

    # Só mostra a linha se TODOS estes campos existirem — equivale ao
    # `select(.trace_id)` do jq.
    exigir_campos: Mapped[list] = mapped_column(JSONB, default=list)

    # Filtro de texto simples aplicado à linha inteira (case-insensitive)
    filtro: Mapped[str] = mapped_column(String(255), default="")

    # Destaca em vermelho a linha que casar (erro, exception, timeout…)
    destacar: Mapped[str] = mapped_column(String(255), default="")

    # Mostra a linha crua quando não for JSON válido, em vez de escondê-la
    mostrar_nao_json: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
