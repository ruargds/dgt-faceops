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
from app.models.destino import TIPOS as TIPOS_DESTINO, Destino
from app.models.host import Host
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
    "VisaoLog",
    "TIPOS_DESTINO",
    "PROFILES",
    "PROFILE_CONFIG",
    "PROFILE_ESSENCIAL",
    "PROFILE_COMPLETO",
]
