from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


# local  — disco do próprio painel
# azure  — Azure Blob Storage
# rclone — qualquer backend do rclone: Drive, S3, B2, OneDrive, SFTP,
#          WebDAV, Dropbox… é o que dá alcance externo sem escrever um
#          conector por provedor.
TIPOS = ("local", "azure", "rclone")


class Destino(Base):
    """
    Um destino de backup, configurado pela web em vez do .env.

    Trocar destino não pode exigir editar arquivo e reiniciar container —
    é operação de rotina (credencial de nuvem vence, cliente muda de
    provedor, um bucket enche).

    Segredos ficam em colunas *_enc, cifrados com Fernet. Nenhum schema de
    saída os expõe; a tela confirma o que está guardado pelo fingerprint.
    """

    __tablename__ = "destinos"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    descricao: Mapped[str] = mapped_column(String(255), default="")
    tipo: Mapped[str] = mapped_column(String(16), index=True)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Pré-selecionado ao criar backup ou agendamento
    padrao: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── local ──────────────────────────────────────────────────────────
    caminho: Mapped[str] = mapped_column(String(512), default="")

    # ── azure ──────────────────────────────────────────────────────────
    azure_container: Mapped[str] = mapped_column(String(255), default="")
    azure_conn_enc: Mapped[str] = mapped_column(Text, default="")
    # Hot | Cool | Archive — Cool é o padrão por custo
    azure_tier: Mapped[str] = mapped_column(String(16), default="Cool")

    # ── rclone ─────────────────────────────────────────────────────────
    # Nome do remote dentro do bloco de configuração
    rclone_remote: Mapped[str] = mapped_column(String(120), default="")
    rclone_caminho: Mapped[str] = mapped_column(String(512), default="")
    # O bloco [remote] inteiro do rclone.conf. Contém token e chave, então
    # vai cifrado e é materializado em arquivo temporário só na hora do uso.
    rclone_conf_enc: Mapped[str] = mapped_column(Text, default="")
    # Ajustes de transferência — arquivo de dezenas de GB pede tuning
    rclone_flags: Mapped[str] = mapped_column(String(512), default="")

    # ── retenção ───────────────────────────────────────────────────────
    # 0 = não aplicar retenção neste destino (padrão para nuvem, que
    # costuma ter política própria de ciclo de vida)
    retencao_dias: Mapped[int] = mapped_column(Integer, default=0)

    # ── último teste ───────────────────────────────────────────────────
    last_test_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_test_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_test_error: Mapped[str] = mapped_column(Text, default="")
    # Confirma QUAL credencial está guardada, sem revelá-la
    cred_fingerprint: Mapped[str] = mapped_column(String(32), default="")

    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
