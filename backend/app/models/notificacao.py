from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text,
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


class NotificacaoDestino(Base):
    """
    Para onde vai o aviso. Um grupo, ou uma pessoa.

    Desenho emprestado de quem já resolveu isso: Zabbix separa *media type*
    de *action*, Grafana separa *contact point* de *notification policy*,
    Alertmanager separa *receiver* de *route*. Aqui é a mesma divisão —
    **destino** é para onde, **regra** é o que mandar para lá. Sem essa
    separação, cada destino novo exigiria duplicar todas as regras.

    `tipo`:

    * `grupo` — grupo/canal do Telegram, id negativo (`-100…`). Alguém
      precisa adicionar o bot ao grupo; o Telegram não deixa o bot entrar
      sozinho.
    * `individual` — conversa direta com uma pessoa. O `chat_id` é o id
      numérico dela, e ela precisa ter falado com o bot pelo menos uma vez
      (mandar `/start`) — antes disso o Telegram recusa a mensagem com
      "bot can't initiate conversation with a user". É limite da
      plataforma, não do painel.
    """

    __tablename__ = "notificacao_destinos"
    __table_args__ = (
        Index("ix_notif_destino_chat", "chat_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Como aparece na tela: "Plantão NOC", "João (celular)".
    nome: Mapped[str] = mapped_column(String(120), default="")
    # grupo | individual
    tipo: Mapped[str] = mapped_column(String(16), default="grupo")
    chat_id: Mapped[str] = mapped_column(String(64))
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    observacao: Mapped[str] = mapped_column(String(255), default="")

    created_by: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class NotificacaoRegra(Base):
    """
    O que mandar, de onde, com que gravidade — e para qual destino.

    Mesma ideia dos limiares: `host_id` nulo vale para todos os
    servidores, `servico` vazio vale para todos os serviços daquele
    servidor. `destino_id` nulo vale para **todos os destinos ativos** —
    é o que mantém simples o caso simples ("avisar todo mundo de tudo").
    """

    __tablename__ = "notificacao_regras"
    __table_args__ = (
        Index("ix_notif_regra_escopo", "destino_id", "host_id", "servico"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nulo = todos os destinos ativos.
    destino_id: Mapped[int | None] = mapped_column(
        ForeignKey("notificacao_destinos.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    servico: Mapped[str] = mapped_column(String(160), default="")

    # Quais tipos de evento esta regra deixa passar. Ver TIPOS em
    # `notificacao_service` — vazio significa "nenhum", não "todos": regra
    # sem tipo marcado não manda nada, e isso é explícito na tela.
    tipos: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # atencao = manda tudo; critico = só o que parou de fato.
    nivel_minimo: Mapped[str] = mapped_column(String(16), default="critico")

    # Só avisa se o problema PERSISTIR por estes segundos — o `for:` do
    # Prometheus. Zero manda na hora. Serve para não acordar ninguém por
    # uma piscada de 20 segundos que já se resolveu.
    atraso_s: Mapped[int] = mapped_column(Integer, default=0)

    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Legado da primeira versão, quando o retorno era um booleano em vez de
    # um tipo de evento. Mantido para não exigir migração destrutiva; quem
    # decide hoje é `tipos` conter "retorno".
    avisar_retorno: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

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
    # Nome do destino, para "não recebi" ter resposta por destino e não só
    # por evento.
    destino: Mapped[str] = mapped_column(String(120), default="")
    texto: Mapped[str] = mapped_column(String(1000), default="")
    # enviado | falha
    status: Mapped[str] = mapped_column(String(16), default="enviado")
    erro: Mapped[str] = mapped_column(String(300), default="")
    tentativas: Mapped[int] = mapped_column(Integer, default=1)
