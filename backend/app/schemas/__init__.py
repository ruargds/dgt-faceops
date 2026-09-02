"""
Schemas de entrada e saída.

Regra dura deste arquivo: **nenhum schema de saída expõe coluna `*_enc`**.
A chave PEM e as senhas entram por aqui e nunca mais saem — a UI confirma
o que está cadastrado pelo fingerprint, não pelo valor.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.backup import PROFILES
from app.models.destino import TIPOS as TIPOS_DESTINO

# ── Autenticação ───────────────────────────────────────────────────────


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    usuario: "UsuarioOut"


class TrocarSenhaIn(BaseModel):
    senha_atual: str = Field(min_length=1, max_length=256)
    senha_nova: str = Field(min_length=6, max_length=256)


class UsuarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str
    role: str
    is_active: bool
    is_super_admin: bool
    senha_padrao: bool
    last_login_at: datetime | None = None


class UsuarioIn(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    full_name: str = ""
    password: str = Field(min_length=6, max_length=256)
    role: str = "observador"


class UsuarioUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=6, max_length=256)


class MeOut(BaseModel):
    usuario: UsuarioOut
    permissoes: list[str]


# ── Hosts ──────────────────────────────────────────────────────────────


class HostIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # Vazio é o normal: sem apelido, o rótulo é o próprio nome.
    alias: str = Field(default="", max_length=120)
    description: str = ""
    role: str = "outro"
    address: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(min_length=1, max_length=120)
    auth_method: str = "key"

    # Segredos — só entram, nunca saem
    ssh_key: str | None = None
    ssh_key_passphrase: str | None = None
    ssh_password: str | None = None
    sudo_password: str | None = None

    ffmulti_dir: str = ""
    compose_file: str = ""
    has_gpu: bool = False
    enabled: bool = True
    monitorar: bool = True
    ff_api_url: str = ""
    # A instalação entra na API do FindFace com usuário e senha; o token é
    # alternativa para quem gerou um. Senha e token são segredo: só entram.
    ff_api_user: str = ""
    ff_api_pass: str | None = None
    ff_api_token: str | None = None

    @field_validator("auth_method")
    @classmethod
    def _validar_auth(cls, v: str) -> str:
        if v not in ("key", "password"):
            raise ValueError("auth_method deve ser 'key' ou 'password'")
        return v


class HostUpdate(BaseModel):
    name: str | None = None
    alias: str | None = None
    description: str | None = None
    role: str | None = None
    address: str | None = None
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    ssh_user: str | None = None
    auth_method: str | None = None
    ssh_key: str | None = None
    ssh_key_passphrase: str | None = None
    ssh_password: str | None = None
    sudo_password: str | None = None
    ffmulti_dir: str | None = None
    compose_file: str | None = None
    has_gpu: bool | None = None
    enabled: bool | None = None
    monitorar: bool | None = None
    ff_api_url: str | None = None
    ff_api_user: str | None = None
    ff_api_pass: str | None = None
    ff_api_token: str | None = None


class HostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    alias: str
    # Calculado no modelo (`Host.rotulo`) e servido pronto: a tela não
    # deve reimplementar a regra de qual nome mostrar.
    rotulo: str
    description: str
    role: str
    address: str
    ssh_port: int
    ssh_user: str
    auth_method: str
    ffmulti_dir: str
    compose_file: str
    has_gpu: bool
    enabled: bool
    monitorar: bool
    ff_api_url: str
    ff_api_user: str
    last_seen_at: datetime | None
    last_status: str
    last_error: str

    # Confirmam O QUE está cadastrado, sem revelar nada
    host_key_fingerprint: str
    key_fingerprint: str
    tem_credencial: bool = False
    tem_sudo: bool = False
    tem_api: bool = False


class ComandoRapidoIn(BaseModel):
    """`confirmar` é o nome do servidor, exigido nas ações destrutivas."""

    confirmar: str = ""


class PowerContainerIn(BaseModel):
    """Parar ou subir um container. `confirmar` só é exigido em `stop`."""

    container: str = Field(min_length=1, max_length=255)
    acao: str = "stop"
    confirmar: str = ""

    @field_validator("acao")
    @classmethod
    def _validar_acao(cls, v: str) -> str:
        if v not in ("stop", "start"):
            raise ValueError("acao deve ser 'stop' ou 'start'")
        return v


class ScanChaveIn(BaseModel):
    address: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)


class ScanChaveOut(BaseModel):
    host_key_pub: str
    fingerprint: str


# ── Serviços ───────────────────────────────────────────────────────────


class AcaoContainerIn(BaseModel):
    container: str = Field(min_length=1, max_length=128)


class AcaoStackIn(BaseModel):
    acao: str
    # Dupla confirmação: o operador digita o nome do host para seguir
    confirmar_host: str = ""

    @field_validator("acao")
    @classmethod
    def _validar(cls, v: str) -> str:
        if v not in ("stop", "up", "restart"):
            raise ValueError("acao deve ser 'stop', 'up' ou 'restart'")
        return v


# ── Backups ────────────────────────────────────────────────────────────


class BackupIn(BaseModel):
    perfil: str
    # IDs dos destinos cadastrados. Vazio = usa os marcados como padrão.
    destinos: list[int] = []
    retencao_dias: int | None = Field(default=None, ge=0, le=3650)
    # Perfil completo derruba o reconhecimento — exige aceite explícito
    aceito_downtime: bool = False

    @field_validator("perfil")
    @classmethod
    def _validar_perfil(cls, v: str) -> str:
        if v not in PROFILES:
            raise ValueError(f"perfil deve ser um de {PROFILES}")
        return v


class BackupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    host_id: int | None
    schedule_id: int | None
    profile: str
    status: str
    stage: str
    progress: int
    artifact_name: str
    size_bytes: int
    checksum_sha256: str
    destinations: list
    caused_downtime: bool
    downtime_seconds: int
    triggered_by: str
    error: str
    started_at: datetime
    finished_at: datetime | None
    expired: bool

    host_nome: str = ""


class BackupDetalheOut(BackupOut):
    log: str = ""


# ── Agendamentos ───────────────────────────────────────────────────────


class ItemLimpezaAgendada(BaseModel):
    opcao: str = Field(min_length=4, max_length=64)
    dias: int = Field(ge=0, le=3650)


class ScheduleIn(BaseModel):
    """
    Agendamento. Dois tipos convivem na mesma tabela.

    `backup` usa perfil e destinos. `limpeza` usa `como_configurado` — o
    `--as-configured` do manual da NtechLab, que aplica as idades já
    configuradas na plataforma — ou uma lista explícita de tipos e prazos.
    Preferir `como_configurado` evita o agendamento carregar uma segunda
    verdade sobre quanto tempo cada coisa fica.
    """

    name: str = Field(min_length=1, max_length=120)
    host_id: int
    perfil: str = "essencial"
    cron: str = Field(min_length=1, max_length=64)
    destinos: list[int] = []
    retencao_dias: int = Field(default=30, ge=0, le=3650)
    enabled: bool = True
    allow_downtime: bool = False

    tipo: str = "backup"
    como_configurado: bool = True
    itens: list[ItemLimpezaAgendada] = Field(default_factory=list)

    @field_validator("tipo")
    @classmethod
    def _validar_tipo(cls, v: str) -> str:
        if v not in ("backup", "limpeza"):
            raise ValueError("tipo deve ser 'backup' ou 'limpeza'")
        return v

    @field_validator("perfil")
    @classmethod
    def _validar_perfil(cls, v: str) -> str:
        if v not in PROFILES:
            raise ValueError(f"perfil deve ser um de {PROFILES}")
        return v


class ScheduleUpdate(BaseModel):
    name: str | None = None
    perfil: str | None = None
    cron: str | None = None
    destinos: list[int] | None = None
    retencao_dias: int | None = Field(default=None, ge=0, le=3650)
    enabled: bool | None = None
    allow_downtime: bool | None = None


class ScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    host_id: int | None
    profile: str
    tipo: str = "backup"
    parametros: dict = {}
    cron: str
    destinations: list
    retention_days: int
    enabled: bool
    allow_downtime: bool
    last_run_at: datetime | None
    last_status: str
    next_run_at: datetime | None
    created_by: str

    host_nome: str = ""
    cron_legivel: str = ""


# ── Destinos de backup ─────────────────────────────────────────────────


class DestinoIn(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    descricao: str = ""
    tipo: str
    enabled: bool = True
    padrao: bool = False
    retencao_dias: int = Field(default=0, ge=0, le=3650)

    # local
    caminho: str = ""

    # azure
    azure_container: str = ""
    azure_tier: str = "Cool"
    azure_conn: str | None = None       # segredo — só entra

    # rclone
    rclone_remote: str = ""
    rclone_caminho: str = ""
    rclone_conf: str | None = None      # segredo — só entra
    rclone_flags: str = ""

    @field_validator("tipo")
    @classmethod
    def _validar_tipo(cls, v: str) -> str:
        if v not in TIPOS_DESTINO:
            raise ValueError(f"tipo deve ser um de {TIPOS_DESTINO}")
        return v


class DestinoUpdate(BaseModel):
    nome: str | None = None
    descricao: str | None = None
    enabled: bool | None = None
    padrao: bool | None = None
    retencao_dias: int | None = Field(default=None, ge=0, le=3650)
    caminho: str | None = None
    azure_container: str | None = None
    azure_tier: str | None = None
    azure_conn: str | None = None
    rclone_remote: str | None = None
    rclone_caminho: str | None = None
    rclone_conf: str | None = None
    rclone_flags: str | None = None


class DestinoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    descricao: str
    tipo: str
    enabled: bool
    padrao: bool
    retencao_dias: int
    caminho: str
    azure_container: str
    azure_tier: str
    rclone_remote: str
    rclone_caminho: str
    rclone_flags: str

    last_test_at: datetime | None
    last_test_ok: bool
    last_test_error: str
    # Confirma QUAL credencial está guardada, sem revelá-la
    cred_fingerprint: str
    tem_credencial: bool = False


# ── Auditoria ──────────────────────────────────────────────────────────


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    usuario: str
    ip: str
    action: str
    target: str
    level: str
    success: bool
    detail: dict


class CredencialTerminalIn(BaseModel):
    """
    Login e senha válidos APENAS para uma sessão de terminal.

    Vem por corpo JSON, nunca por query param (regra 2 — query string vai
    para o log de acesso do nginx). Nada disto é gravado: vive no ticket em
    memória por 30 segundos e some da sessão assim que o SSH autentica.

    Senha vazia significa "usar a credencial do cofre" — é o caminho dos
    servidores cadastrados com chave PEM, que não têm senha para digitar.
    """

    usuario: str = Field(default="", max_length=120)
    senha: str = Field(default="", max_length=256)


class SessaoTerminalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    host_id: int
    usuario: str
    ip: str
    started_at: datetime
    ended_at: datetime | None
    bytes_in: int
    bytes_out: int
    sudo_used: bool
    end_reason: str

    host_nome: str = ""


TokenOut.model_rebuild()
