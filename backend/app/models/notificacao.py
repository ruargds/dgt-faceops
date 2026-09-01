from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NotificacaoConta(Base):
    """
    A conta de envio — um bot do Telegram e o grupo que recebe.

    Um bot por cliente, como já se faz no Zabbix: o token é do bot, o
    `chat_id` é do grupo onde estão as pessoas que precisam receber.

    O token é segredo de verdade — quem o tem manda mensagem como o bot e
    lê o que chega nele. Fica cifrado com Fernet, na mesma caixa das
    chaves SSH (`core.vault`), e **nunca** volta numa resposta da API: a
    tela mostra o nome do bot e a impressão digital, nada mais.
    """

    __tablename__ = "notificacao_contas"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nome do bot conforme o próprio Telegram responde (getMe), para a
    # tela mostrar em qual conta está enviando sem expor o token.
    bot_nome: Mapped[str] = mapped_column(String(120), default="")
    bot_token_enc: Mapped[str] = mapped_column(Text, default="")
    # Confirma QUAL token está guardado sem nunca exibi-lo.
    token_fingerprint: Mapped[str] = mapped_column(String(32), default="")
    # Grupo/canal de destino. Grupo do Telegram tem id negativo.
    chat_id: Mapped[str] = mapped_column(String(64), default="")
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_by: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class NotificacaoRegra(Base):
    """
    O que se quer receber: de qual servidor, de qual serviço, a partir de
    qual gravidade.

    Mesma ideia dos limiares: `host_id` nulo vale para todos os
    servidores, `servico` vazio vale para todos os serviços daquele
    servidor. "Permitir todos" é uma regra só, com os dois em branco.
    """

    __tablename__ = "notificacao_regras"
    __table_args__ = (
        Index("ix_notif_regra_unica", "host_id", "servico", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    servico: Mapped[str] = mapped_column(String(160), default="")

    # atencao = manda tudo; critico = só o que parou de fato.
    nivel_minimo: Mapped[str] = mapped_column(String(16), default="critico")
    # Avisar também quando volta. Ligado por padrão: saber que normalizou
    # evita alguém sair de casa às 3h por um problema que já passou.
    avisar_retorno: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_by: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class NotificacaoEnvio(Base):
    """
    O que já foi mandado. Serve a três coisas, nesta ordem:

    1. **Não repetir.** `chave` identifica o evento; o mesmo evento não é
       mandado duas vezes, mesmo que o ciclo o veja de novo.
    2. **Diagnóstico.** Quando alguém diz "não recebi", a resposta está
       aqui: mandou e falhou, ou nem chegou a mandar por causa de regra.
    3. **Retentativa.** Falha de rede não perde o aviso.

    Tem retenção própria (`notificacao.retencao_dias`, padrão 14). Isto é
    log operacional, não histórico de valor — passado o prazo, sai.
    """

    __tablename__ = "notificacao_envios"
    __table_args__ = (
        Index("ix_notif_envio_chave", "chave"),
        Index("ix_notif_envio_ts", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    chave: Mapped[str] = mapped_column(String(200), default="")
    texto: Mapped[str] = mapped_column(String(1000), default="")
    # enviado | falha
    status: Mapped[str] = mapped_column(String(16), default="enviado")
    erro: Mapped[str] = mapped_column(String(300), default="")
    tentativas: Mapped[int] = mapped_column(Integer, default=1)
