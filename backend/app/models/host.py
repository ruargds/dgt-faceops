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
    # Modelo da placa, como o nvidia-smi reporta ("NVIDIA A10-12Q").
    # Preenchido pelo coletor: fica no host, e não na amostra, porque não
    # muda de minuto em minuto — guardá-lo por amostra seria texto
    # repetido milhares de vezes.
    gpu_nome: Mapped[str] = mapped_column(String(120), default="")
    # Entra no coletor continuo. Ligado por padrao: cadastrar um
    # servidor e querer acompanha-lo sao a mesma coisa na pratica.
    monitorar: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # API HTTP do FindFace (opcional). Quando preenchida, a consulta de
    # cameras usa a API oficial em vez de ler o Postgres via SSH — mais
    # limpo e sem depender de acesso ao banco. O token vai cifrado.
    ff_api_url: Mapped[str] = mapped_column(String(255), default="")
    # A instalação real do FindFace entra com USUÁRIO E SENHA; o token é o
    # caminho alternativo, para quem gerou um. Os dois ficam no cofre.
    ff_api_user: Mapped[str] = mapped_column(String(120), default="")
    ff_api_pass_enc: Mapped[str] = mapped_column(Text, default="")
    ff_api_token_enc: Mapped[str] = mapped_column(Text, default="")
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
