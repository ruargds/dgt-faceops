"""
Ações rápidas no servidor — catálogo FIXO, nunca comando livre.

A pergunta legítima é: por que não uma caixa de texto no cartão do
servidor, onde se digita o comando? Porque isso já existe, e melhor
feito: o **InTerminal** abre sessão SSH real, com sudo, e **grava tudo**
em asciicast para auditoria. Uma caixa de comando no cartão seria um
shell remoto sem gravação, sem cerca e sem revisão — o mesmo poder do
terminal com menos rastro.

Então aqui só entra o que:

1. **não existe em outro lugar do painel** — reiniciar o stack está em
   Serviços, diagnóstico de disco está em Manutenção, e duplicar seria
   criar dois lugares que divergem;
2. **cabe num clique** — algo que não justifica abrir uma sessão inteira;
3. **tem risco declarado**, permissão própria e, quando destrutivo,
   confirmação digitada.

Cada entrada diz o que roda, com que permissão e por quê. Comando que
não couber nessas três linhas não pertence a esta lista: pertence ao
terminal.

**O que ficou deliberadamente de fora:** `shutdown`. Uma VM desligada não
volta pelo painel — sem SSH, o painel não alcança nada. Voltar exigiria o
portal do Azure, e oferecer um botão cuja consequência o painel não
consegue desfazer é armadilha, não recurso.
"""
import logging

log = logging.getLogger("faceops.comandos")


# Cada ação: o que faz, o comando, quem pode e quanto pesa.
#
# `confirmar` marca as que exigem digitar o nome do servidor. `derruba`
# avisa que a conexão cai como PARTE do sucesso — sem isso, a rota
# trataria o fim da sessão como falha e diria que não funcionou algo que
# funcionou.
COMANDOS: dict[str, dict] = {
    "uptime": {
        "rotulo": "Há quanto tempo está de pé",
        "ajuda": "Desde quando o sistema subiu e a carga atual. É o primeiro "
                 "dado depois de uma queda: diz se a máquina reiniciou.",
        "comando": "uptime -p; echo; uptime; echo; who -b",
        "sudo": False,
        "permissao": "hosts.view",
        "destrutivo": False,
        "confirmar": False,
        "derruba": False,
        "timeout": 20,
    },
    "disco": {
        "rotulo": "Espaço em disco",
        "ajuda": "Ocupação por ponto de montagem e os maiores diretórios do "
                 "sistema. Leitura pura, não apaga nada.",
        "comando": "df -h -x tmpfs -x devtmpfs; echo; "
                   "ionice -c3 du -xh --max-depth=1 /var 2>/dev/null | sort -rh | head -8",
        "sudo": False,
        "permissao": "maintenance.view",
        "destrutivo": False,
        "confirmar": False,
        "derruba": False,
        "timeout": 60,
    },
    "quem": {
        "rotulo": "Quem está conectado",
        "ajuda": "Sessões abertas agora e os últimos acessos. Útil antes de "
                 "reiniciar: mostra se há alguém trabalhando ali.",
        "comando": "who; echo; last -n 8 -w 2>/dev/null | head -10",
        "sudo": False,
        "permissao": "hosts.view",
        "destrutivo": False,
        "confirmar": False,
        "derruba": False,
        "timeout": 20,
    },
    "relogio": {
        "rotulo": "Relógio e sincronismo",
        "ajuda": "Hora, fuso e se o NTP está sincronizado. Relógio fora do "
                 "lugar bagunça correlação de log entre servidores.",
        "comando": "timedatectl 2>/dev/null || date",
        "sudo": False,
        "permissao": "hosts.view",
        "destrutivo": False,
        "confirmar": False,
        "derruba": False,
        "timeout": 20,
    },
    "memoria": {
        "rotulo": "Memória e processos pesados",
        "ajuda": "Memória livre e os cinco processos que mais consomem. "
                 "Responde 'quem comeu a RAM' sem abrir terminal.",
        "comando": "free -h; echo; "
                   "ps -eo pid,comm,%mem,%cpu --sort=-%mem 2>/dev/null | head -6",
        "sudo": False,
        "permissao": "metrics.view",
        "destrutivo": False,
        "confirmar": False,
        "derruba": False,
        "timeout": 30,
    },
    "reiniciar_docker": {
        "rotulo": "Reiniciar o Docker",
        "ajuda": "Reinicia o serviço do Docker — e com ele TODOS os containers "
                 "do servidor. O reconhecimento fica fora do ar por alguns "
                 "minutos. Antes disto, tente reiniciar só o container em "
                 "Serviços: quase sempre resolve.",
        "comando": "systemctl restart docker",
        "sudo": True,
        # Mesmo alcance de parar o stack inteiro — logo, a mesma permissão.
        "permissao": "services.stack",
        "destrutivo": True,
        "confirmar": True,
        "derruba": False,
        "timeout": 180,
    },
    "reiniciar": {
        "rotulo": "Reiniciar o servidor",
        "ajuda": "Reinicia a máquina inteira. O reconhecimento facial deste "
                 "servidor fica fora do ar até ela voltar, e o painel vai "
                 "registrar isso como queda — inclusive avisando no Telegram, "
                 "porque para quem está de plantão uma parada planejada é "
                 "indistinguível de uma real.",
        # Agendado para 3 s à frente, e não `reboot` direto, porque o
        # `reboot` mata a sessão SSH antes de responder: a rota veria erro
        # de conexão e diria que falhou algo que funcionou. Com o
        # `systemd-run`, o comando retorna limpo e a máquina cai depois.
        "comando": (
            "systemd-run --on-active=3 --timer-property=AccuracySec=100ms "
            "systemctl reboot 2>/dev/null "
            "|| (nohup sh -c 'sleep 3; reboot' >/dev/null 2>&1 & echo agendado)"
        ),
        "sudo": True,
        "permissao": "hosts.reboot",
        "destrutivo": True,
        "confirmar": True,
        "derruba": True,
        "timeout": 30,
    },
}


def catalogo(permissoes: set[str]) -> list[dict]:
    """
    As ações que ESTE usuário pode ver, sem o comando.

    O comando não sai na resposta de propósito: a tela não precisa dele
    para desenhar um botão, e publicá-lo só ensinaria o que colar num
    terminal. Quem decide o que roda é o servidor.
    """
    return [
        {
            "chave": chave,
            "rotulo": info["rotulo"],
            "ajuda": info["ajuda"],
            "destrutivo": info["destrutivo"],
            "confirmar": info["confirmar"],
            "derruba": info["derruba"],
        }
        for chave, info in COMANDOS.items()
        if info["permissao"] in permissoes
    ]
