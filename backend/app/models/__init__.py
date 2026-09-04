"""Registro de modelos — importar todos aqui para o create_all enxergar."""
from app.models.audit import AuditLog, TerminalSession
from app.models.backup import (
    PROFILE_COMPLETO,
    PROFILE_CONFIG,
    PROFILE_ESSENCIAL,
    PROFILES,
    BackupRun,
    Schedule,
)
from app.models.amostra import Amostra
from app.models.amostra_container import AmostraContainer
from app.models.amostra_disco import AmostraDisco
from app.models.licenca_amostra import LicencaAmostra
from app.models.configuracao import Configuracao
from app.models.crescimento import Crescimento
from app.models.destino import TIPOS as TIPOS_DESTINO, Destino
from app.models.host import Host
from app.models.incidente import Incidente
from app.models.limiar_override import LimiarOverride
from app.models.log_padrao import LogPadrao
from app.models.notificacao import (
    NotificacaoConta, NotificacaoDestino, NotificacaoEnvio, NotificacaoRegra,
)
from app.models.user import User
from app.models.visao_log import VisaoLog

__all__ = [
    "AuditLog",
    "TerminalSession",
    "BackupRun",
    "Schedule",
    "Host",
    "User",
    "Destino",
    "Configuracao",
    "Amostra",
    "AmostraContainer",
    "AmostraDisco",
    "LicencaAmostra",
    "Incidente",
    "Crescimento",
    "LimiarOverride",
    "LogPadrao",
    "NotificacaoConta",
    "NotificacaoDestino",
    "NotificacaoRegra",
    "NotificacaoEnvio",
    "VisaoLog",
    "TIPOS_DESTINO",
    "PROFILES",
    "PROFILE_CONFIG",
    "PROFILE_ESSENCIAL",
    "PROFILE_COMPLETO",
]
