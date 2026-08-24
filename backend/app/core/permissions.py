"""
Catálogo de permissões — HARDCODED, não existe tabela SQL de permissões.
(Mesma regra do InfraCore: nunca fazer INSERT em tabela `permissions`.)

O perfil "observador" é o que atende o pedido de modo somente-leitura:
enxerga tudo, não executa nada.
"""

PERMISSION_CATALOG: dict[str, str] = {
    # Hosts
    "hosts.view": "Ver servidores cadastrados",
    "hosts.manage": "Cadastrar, editar e remover servidores (inclui credenciais)",
    # Métricas
    "metrics.view": "Ver RAM, GPU, disco e carga",
    # Serviços do FindFace
    "services.view": "Ver status dos containers do FindFace Multi",
    "services.restart": "Reiniciar um container individual",
    "services.stack": "Parar/subir o stack inteiro do FindFace Multi",
    # Backups
    "backups.view": "Ver histórico e artefatos de backup",
    "backups.run": "Disparar backup sob demanda",
    "backups.download": "Baixar artefato de backup",
    "backups.restore": "Restaurar backup sobre o servidor",
    "backups.delete": "Apagar artefato de backup",
    # Agendamentos
    "schedules.view": "Ver agendamentos",
    "schedules.manage": "Criar, editar, pausar e remover agendamentos",
    # InTerminal
    "terminal.use": "Abrir sessão de terminal SSH",
    "terminal.sudo": "Executar comandos com sudo no terminal",
    "terminal.sessions.view": "Ver gravações de sessões de terminal",
    # Auditoria e usuários
    "audit.view": "Ver log de auditoria",
    "users.manage": "Gerenciar usuários e perfis",
}

# Ações destrutivas — sempre exigem dupla confirmação na UI e viram
# registro de auditoria com nível "critical".
DESTRUCTIVE_PERMISSIONS: frozenset[str] = frozenset({
    "services.stack",
    "backups.restore",
    "backups.delete",
    "hosts.manage",
})

VIEW_ONLY: list[str] = [
    "hosts.view",
    "metrics.view",
    "services.view",
    "backups.view",
    "schedules.view",
]

ROLE_PERMISSIONS: dict[str, list[str]] = {
    # Somente leitura — vê o painel inteiro, não mexe em nada.
    "observador": VIEW_ONLY,
    # Plantão: diagnostica e destrava serviço travado, sem poder destrutivo.
    "operador": VIEW_ONLY + [
        "backups.run",
        "services.restart",
        "terminal.use",
    ],
    # Técnico: opera backups e terminal com sudo, sem restore nem stack.
    "tecnico": VIEW_ONLY + [
        "services.restart",
        "backups.run",
        "backups.download",
        "schedules.manage",
        "terminal.use",
        "terminal.sudo",
        "terminal.sessions.view",
        "audit.view",
    ],
    # Admin: tudo, inclusive restore e parada do stack.
    "admin": list(PERMISSION_CATALOG.keys()),
}

ROLE_LABELS: dict[str, str] = {
    "observador": "Observador (somente leitura)",
    "operador": "Operador de plantão",
    "tecnico": "Técnico",
    "admin": "Administrador",
}


def permissions_for(role: str, is_super_admin: bool = False) -> set[str]:
    """Resolve o conjunto efetivo de permissões de um usuário."""
    if is_super_admin:
        return set(PERMISSION_CATALOG.keys())
    return set(ROLE_PERMISSIONS.get(role, VIEW_ONLY))
