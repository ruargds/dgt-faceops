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
    "services.power": "Parar ou subir um container individual",
    "services.stack": "Parar/subir o stack inteiro do FindFace Multi",
    # Manutenção de disco e log
    "maintenance.view": "Ver diagnóstico de disco e crescimento de log",
    "maintenance.apply": "Aplicar contenção de log e arquivar log antigo",
    "cleanup.run": "Apagar eventos antigos do FindFace (libera disco, irreversível)",
    # Backups
    "backups.view": "Ver histórico e artefatos de backup",
    "backups.run": "Disparar backup sob demanda",
    "backups.download": "Baixar artefato de backup",
    "backups.restore": "Restaurar backup sobre o servidor",
    "backups.delete": "Apagar artefato de backup",
    "destinations.manage": "Cadastrar e editar destinos de backup (local, Azure, rclone)",
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
    # Parar um serviço é diferente de reiniciar: reiniciar volta sozinho,
    # parar FICA parado. Um `findface-video-worker` parado por descuido é
    # reconhecimento facial fora do ar até alguém perceber — e ninguém
    # percebe, porque não há erro, só ausência.
    "services.power",
    # Escreve configuração de sistema em servidor de produção e reinicia
    # rsyslog/journald. Não derruba o FindFace, mas merece rastro forte.
    "maintenance.apply",
    "backups.restore",
    "backups.delete",
    "hosts.manage",
    "destinations.manage",
})

VIEW_ONLY: list[str] = [
    "hosts.view",
    "metrics.view",
    "services.view",
    "backups.view",
    "schedules.view",
    "maintenance.view",
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
        "maintenance.apply",
        "services.restart",
        # Deliberadamente FORA do perfil "operador": o plantão precisa
        # destravar serviço travado (reiniciar), não deixar serviço
        # parado. São riscos de ordem diferente.
        "services.power",
        "backups.run",
        "backups.download",
        "destinations.manage",
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
