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

# ── Como isto se organiza na tela ──────────────────────────────────────
# O catálogo acima é a fonte da verdade do que existe. O que vem abaixo é
# o que faz esse catálogo ser LEGÍVEL: em que área cada permissão vive, e
# uma frase dizendo o que ela permite de fato — porque "backups.restore"
# não diz a ninguém que aquilo sobrescreve o servidor de produção.
#
# Hardcoded como o resto, e pela mesma razão: permissão que só existe no
# banco vira caixa marcada que ninguém sabe para que serve.

AREAS: list[tuple[str, str, str]] = [
    ("servidores", "Servidores e acesso",
     "Quem existe, como o painel entra neles e quem pode mexer nisso."),
    ("monitoramento", "Monitoramento",
     "Ver estado, métrica, incidente e diagnóstico. Nada aqui altera nada."),
    ("servicos", "Serviços do FindFace",
     "Agir sobre os containers: reiniciar, parar, subir."),
    ("manutencao", "Manutenção e limpeza",
     "Conter o crescimento de log e liberar disco no servidor."),
    ("backup", "Backup e restauração",
     "Copiar, baixar, restaurar e apagar — onde mora o risco de perda."),
    ("terminal", "Terminal remoto",
     "Acesso a linha de comando nos servidores, e o registro dele."),
    ("administracao", "Administração do painel",
     "Usuários, perfis e a trilha de auditoria."),
]

# permissão -> (área, o que ela permite na prática)
PERMISSION_INFO: dict[str, tuple[str, str]] = {
    "hosts.view": ("servidores",
                   "Ver a lista de servidores, papel e endereço. Não vê credencial."),
    "hosts.manage": ("servidores",
                     "Cadastrar, editar e remover servidor — inclusive trocar a chave "
                     "SSH e a senha de sudo. Na prática é acesso root aos servidores."),
    "metrics.view": ("monitoramento",
                     "Ver CPU, memória, disco, GPU, incidentes e diagnóstico."),
    "services.view": ("monitoramento",
                      "Ver quais containers do FindFace estão de pé e o log deles."),
    "services.restart": ("servicos",
                         "Reiniciar UM container. Ele volta sozinho — é a ação de "
                         "destravar, e a de menor risco."),
    "services.power": ("servicos",
                       "Parar ou subir UM container. Parado FICA parado: um serviço "
                       "de vídeo desligado é reconhecimento fora do ar sem gerar erro."),
    "services.stack": ("servicos",
                       "Parar ou subir o stack INTEIRO. Derruba o reconhecimento "
                       "facial do servidor enquanto estiver parado."),
    "maintenance.view": ("manutencao",
                         "Ver o que ocupa disco e a velocidade de crescimento do log."),
    "maintenance.apply": ("manutencao",
                          "Escrever configuração de log no servidor e arquivar log "
                          "antigo. Reinicia rsyslog e journald; o FindFace não é afetado."),
    "cleanup.run": ("manutencao",
                    "Apagar eventos antigos do FindFace para liberar disco. "
                    "IRREVERSÍVEL — o evento apagado não volta."),
    "backups.view": ("backup", "Ver histórico de execuções e os artefatos."),
    "backups.run": ("backup", "Disparar backup agora, fora do agendamento."),
    "backups.download": ("backup",
                         "Baixar o artefato. Ele contém dado do FindFace — tratar "
                         "o arquivo baixado com o mesmo cuidado do servidor."),
    "backups.restore": ("backup",
                        "Restaurar sobre o servidor. SOBRESCREVE o banco atual: o que "
                        "existir e não estiver no backup é perdido."),
    "backups.delete": ("backup",
                       "Apagar artefato em todos os destinos. Irreversível."),
    "destinations.manage": ("backup",
                            "Cadastrar destino de backup e suas credenciais "
                            "(Azure, rclone). Define para ONDE o dado sai."),
    "schedules.view": ("backup",
                       "Ver quais backups estão agendados, com que frequência e "
                       "quando rodaram pela última vez."),
    "schedules.manage": ("backup",
                         "Criar, editar, pausar e remover agendamento."),
    "terminal.use": ("terminal",
                     "Abrir terminal SSH no servidor pelo painel. Tudo é gravado."),
    "terminal.sudo": ("terminal",
                      "Rodar comando como root no terminal. Sem limite de escopo — "
                      "é a permissão mais ampla do painel."),
    "terminal.sessions.view": ("terminal",
                               "Assistir às gravações de sessões de terminal."),
    "audit.view": ("administracao",
                   "Ver quem fez o quê nos servidores, com busca e filtro, e as "
                   "gravações das sessões de terminal."),
    "users.manage": ("administracao",
                     "Criar usuário, trocar perfil e senha de outro, desativar. "
                     "Permite conceder a si mesmo qualquer permissão."),
}

# Por que os perfis são FIXOS e não editáveis pela tela.
#
# Perfil editável parece flexibilidade e é, na prática, uma porta: quem
# pudesse editar perfil se concederia `terminal.sudo` sem passar por
# ninguém, e a trilha de auditoria registraria só "perfil alterado". Com
# perfil fixo, conceder poder é trocar o perfil de alguém — uma ação, um
# registro, um responsável.
#
# Quatro perfis cobrem os papéis reais desta operação. Se aparecer um
# quinto papel de verdade, ele entra AQUI, com revisão de código, e não
# numa tela às três da manhã.
ROLE_INFO: dict[str, dict] = {
    "observador": {
        "resumo": "Enxerga tudo, não muda nada.",
        "para_quem": "Gestor, auditor, cliente acompanhando. Também serve como "
                     "perfil seguro para uma tela em parede.",
        "nao_pode": "Nenhuma ação: não reinicia, não baixa backup, não abre terminal.",
    },
    "operador": {
        "resumo": "Plantão: destrava o que travou.",
        "para_quem": "Quem atende o chamado de madrugada e precisa pôr o "
                     "reconhecimento de volta no ar.",
        "nao_pode": "Deixar serviço parado, restaurar backup, mexer em cadastro "
                    "ou usar sudo. Reiniciar volta sozinho; parar, não.",
    },
    "tecnico": {
        "resumo": "Opera e mantém, sem poder de destruição.",
        "para_quem": "Quem cuida do ambiente no dia a dia: backup, manutenção, "
                     "terminal com sudo.",
        "nao_pode": "Restaurar ou apagar backup, parar o stack inteiro, cadastrar "
                    "servidor ou gerenciar usuário.",
    },
    "admin": {
        "resumo": "Tudo, inclusive o que não tem volta.",
        "para_quem": "Responsável pelo painel. Devem existir poucos.",
        "nao_pode": "Nada é bloqueado — por isso toda ação destrutiva pede "
                    "confirmação digitada e vira auditoria de nível crítico.",
    },
}


def matriz_perfis() -> dict:
    """
    Tudo o que a tela precisa para explicar os perfis: as áreas, as
    permissões de cada uma (com o que fazem e se são destrutivas) e quais
    perfis têm cada uma.

    Montado aqui, e não na tela, para não existirem duas versões da mesma
    verdade. A tela desenha; quem decide o que é verdade é este módulo.
    """
    perfis = [
        {
            "codigo": codigo,
            "rotulo": ROLE_LABELS.get(codigo, codigo),
            **ROLE_INFO.get(codigo, {}),
            "total": len(permissions_for(codigo)),
            "destrutivas": len(permissions_for(codigo) & DESTRUCTIVE_PERMISSIONS),
        }
        for codigo in ROLE_PERMISSIONS
    ]

    areas = []
    for chave, rotulo, ajuda in AREAS:
        itens = []
        for codigo, descricao in PERMISSION_CATALOG.items():
            area, detalhe = PERMISSION_INFO.get(codigo, ("administracao", ""))
            if area != chave:
                continue
            itens.append({
                "codigo": codigo,
                "rotulo": descricao,
                "detalhe": detalhe,
                "destrutiva": codigo in DESTRUCTIVE_PERMISSIONS,
                "perfis": [c for c in ROLE_PERMISSIONS if codigo in permissions_for(c)],
            })
        if itens:
            areas.append({"chave": chave, "rotulo": rotulo, "ajuda": ajuda, "itens": itens})

    return {"perfis": perfis, "areas": areas}
