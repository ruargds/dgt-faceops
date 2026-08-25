from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Configuracao(Base):
    """
    Uma opção alterada pela web.

    Só existe linha aqui para o que foi mudado — quem está no padrão não
    ocupa espaço. Isso mantém a tabela pequena e deixa óbvio, olhando o
    banco, o que a equipe realmente ajustou.

    O tipo e a validação moram no catálogo (`services/config_service.py`),
    não aqui: a tabela guarda texto, o catálogo dá sentido.
    """

    __tablename__ = "configuracoes"

    chave: Mapped[str] = mapped_column(String(120), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")

    atualizado_por: Mapped[str] = mapped_column(String(120), default="")
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
