from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Host(Base):
    """
    Um servidor alvo (as VMs do Azure rodando FindFace Multi).

    Os segredos ficam em colunas *_enc, cifrados com Fernet pelo core.vault.
    Nenhum schema de saída expõe essas colunas — só o fingerprint.
    """

    __tablename__ = "hosts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(255), default="")

    # Papel na topologia: appserver | dbserver | extraction | ftpserver | outro
    role: Mapped[str] = mapped_column(String(32), default="outro")

    address: Mapped[str] = mapped_column(String(255))  # IP ou FQDN
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_user: Mapped[str] = mapped_column(String(120))

    # key | password
    auth_method: Mapped[str] = mapped_column(String(16), default="key")

    ssh_key_enc: Mapped[str] = mapped_column(Text, default="")
    ssh_key_passphrase_enc: Mapped[str] = mapped_column(Text, default="")
    ssh_password_enc: Mapped[str] = mapped_column(Text, default="")
    sudo_password_enc: Mapped[str] = mapped_column(Text, default="")

    # Identidade do host, fixada por varredura explícita ANTES de qualquer
    # autenticação. Sem isto, um MITM na rede se passa pelo servidor e
    # captura a senha de sudo no handshake.
    host_key_pub: Mapped[str] = mapped_column(Text, default="")
    host_key_fingerprint: Mapped[str] = mapped_column(String(120), default="")
    # Impressão digital da chave PEM cadastrada — confirma QUAL chave está
    # no cofre sem nunca exibi-la.
    key_fingerprint: Mapped[str] = mapped_column(String(32), default="")

    # Caminhos do FindFace Multi neste host (default vem do .env)
    ffmulti_dir: Mapped[str] = mapped_column(String(255), default="")
    compose_file: Mapped[str] = mapped_column(String(255), default="")

    has_gpu: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str] = mapped_column(String(32), default="desconhecido")
    last_error: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
