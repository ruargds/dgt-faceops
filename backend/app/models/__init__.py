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
from app.models.host import Host
from app.models.user import User

__all__ = [
    "AuditLog",
    "TerminalSession",
    "BackupRun",
    "Schedule",
    "Host",
    "User",
    "PROFILES",
    "PROFILE_CONFIG",
    "PROFILE_ESSENCIAL",
    "PROFILE_COMPLETO",
]
