from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Crescimento(Base):
    """
    Uma **vigilância**: um recurso deste servidor que começou a subir e não
    parou.

    O `Incidente` responde "isto caiu e voltou". Aqui a pergunta é a
    anterior, e é a que dá tempo de agir: *isto está subindo, quando vai
    estourar, e quem está empurrando?*

    Abre e fecha sozinha pelo ciclo do monitor, como o incidente — e pelo
    mesmo motivo de custo: a detecção sai das amostras que o ciclo **já**
    gravou, sem nenhuma ida nova ao servidor. O que custa SSH é o rastreio
    do culpado, e ele só roda quando a vigilância abre ou quando o
    intervalo de re-rastreio vence (ver `crescimento_service`).

    `fim=None` é vigilância aberta. Fechar é carimbar `fim` e `desfecho` —
    não existe editar depois.

    Uma linha guarda a série medida (`medicoes`) e o último rastreio
    (`diagnostico`). Isso é deliberadamente mais gordo que uma amostra: são
    poucas linhas por mês, e o que interessa depois é justamente a
    evidência que sustentou a conclusão.
    """

    __tablename__ = "crescimentos"
    __table_args__ = (
        # As duas perguntas: "o que está aberto agora" e "o que este host
        # já teve". O índice de `inicio` NÃO entra aqui — a coluna já é
        # declarada com index=True e o SQLAlchemy gera o nome sozinho;
        # declarar os dois emite dois CREATE INDEX iguais e mata o startup
        # (o defeito de 01/09/2026, ver `Incidente`).
        Index("ix_crescimentos_host_fim", "host_id", "fim"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), index=True
    )

    # memoria | disco | swap — o que está subindo.
    recurso: Mapped[str] = mapped_column(String(16), default="memoria")

    # linear | acelerando | serrote — como está subindo. "acelerando" é o
    # que o pedido chama de exponencial: a taxa da segunda metade da
    # janela é muito maior que a da primeira.
    regime: Mapped[str] = mapped_column(String(16), default="linear")

    nivel: Mapped[str] = mapped_column(String(16), default="atencao")
    # alta | media | baixa — quanto a série sustenta a conclusão (R² e
    # número de pontos). Nunca "nenhuma": sem confiança não se abre
    # vigilância nenhuma.
    confianca: Mapped[str] = mapped_column(String(16), default="media")

    inicio: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )
    fim: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # estabilizou | recuou | estourou | host_sem_contato — por que fechou.
    # Vazio enquanto aberta.
    desfecho: Mapped[str] = mapped_column(String(32), default="")

    # Percentual de ocupação (0–100) no início e no último ciclo. É a
    # unidade comum às três famílias, e a que a projeção usa.
    valor_inicial: Mapped[float] = mapped_column(Float, default=0.0)
    valor_atual: Mapped[float] = mapped_column(Float, default=0.0)
    # Pontos percentuais por hora. Positivo por definição — vigilância que
    # deixa de subir fecha.
    taxa_por_h: Mapped[float] = mapped_column(Float, default=0.0)
    # Só para regime `acelerando`: em quantas horas o consumo dobra, pelo
    # ajuste exponencial. Nulo quando o ajuste linear explica melhor —
    # anunciar "dobra" sem isso seria dar precisão que não existe.
    dobra_h: Mapped[float | None] = mapped_column(Float, nullable=True)

    # A previsão: quando encosta no teto, e qual teto. Nulo quando a
    # projeção não é honesta (série curta demais, taxa instável).
    estouro_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    teto_pct: Mapped[float] = mapped_column(Float, default=95.0)

    # Quem está empurrando, pelo rastreio: nome de container, caminho no
    # disco, ou vazio enquanto não se sabe. Vazio é resposta legítima.
    culpado: Mapped[str] = mapped_column(String(200), default="")
    # Chave do `catalogo_crescimento`. Vazia = nenhuma causa conhecida
    # casou, e a tela diz isso em vez de escolher a mais parecida.
    causa: Mapped[str] = mapped_column(String(48), default="")

    # A série que sustenta tudo acima: uma entrada por ciclo confirmado,
    # com o valor e a taxa daquele instante. É o que permite a alguém
    # conferir a conclusão em vez de acreditar nela.
    #
    # JSONB no Postgres, JSON simples em outro banco — mesma razão do
    # `Incidente.apuracao`: o tipo do dialeto Postgres não compila no
    # SQLite dos testes, e a decisão precisa ser testável.
    medicoes: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    # Último rastreio seletivo: containers, caminhos, arquivos recentes e
    # os achados. Substituído a cada re-rastreio — guardar o histórico de
    # todos seria guardar o servidor inteiro.
    diagnostico: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    rastreado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Quantos ciclos do monitor confirmaram a subida. Serve para a tela
    # separar "vi uma vez" de "está aí há duas horas".
    ciclos: Mapped[int] = mapped_column(Integer, default=1)
