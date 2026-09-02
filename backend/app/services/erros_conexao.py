"""
Tradução de falha de conexão — do erro técnico para o que fazer.

`[Errno 111] Connection refused` está tecnicamente correto e é inútil
para quem está de plantão às 3h. Pior: os três erros mais comuns têm
causas OPOSTAS e a mesma cara de "não conectou".

| O que o sistema diz | O que realmente aconteceu | Onde procurar |
|---|---|---|
| Connection refused | a máquina respondeu e **recusou**: nada escutando na porta | o SSH do servidor, não a rede |
| Timeout / No route | ninguém respondeu | rede, firewall ou VM desligada |
| Permission denied | conectou e o login falhou | credencial, não conectividade |

Mandar alguém conferir a rede quando o problema é o `sshd` parado custa
o dobro do tempo — e é o que uma mensagem sem tradução faz.

Este módulo existe porque a mesma lição já foi aprendida três vezes neste
painel: em "serviço travado", em "câmera sem evento" e nos alertas de
recurso. O padrão é sempre o mesmo — **informar sem explicar não é
informar.**
"""
import re

# (padrão no texto do erro) -> (o que é, o que costuma ser, o que fazer)
#
# A ordem importa: o primeiro que casar vence, então os específicos vêm
# antes dos genéricos.
PADROES: list[tuple[str, str, str, str]] = [
    (
        r"errno 111|connection refused|conn.*refused",
        "O servidor respondeu e recusou a conexão",
        "Alguma coisa está de pé na máquina — ela respondeu. O que não "
        "está é o SSH: nada escutando na porta. Não é rede, é serviço.",
        "Costuma ser o sshd parado, ou a máquina em modo degradado após "
        "uma queda — quando um disco do /etc/fstab não monta, o systemd "
        "pode subir sem os serviços normais. Pelo portal do Azure, use o "
        "console serial para entrar e rodar: systemctl status sshd, e "
        "depois lsblk e mount -a para conferir os discos.",
    ),
    (
        r"errno 113|no route to host",
        "Não há caminho até o servidor",
        "A rede não sabe como chegar nessa máquina. Ela pode estar "
        "desligada, ou numa rede que o painel não alcança mais.",
        "Confira no portal do Azure se a VM está ligada, e se o grupo de "
        "segurança de rede ainda libera a porta a partir do painel.",
    ),
    (
        r"timed? ?out|errno 110|timeout",
        "O servidor não respondeu no tempo",
        "Ninguém respondeu — nem para aceitar, nem para recusar. Isso é "
        "rede ou máquina fora, não credencial.",
        "Confira se a VM está ligada e se o firewall permite a porta. "
        "Máquina sob carga extrema também demora a responder.",
    ),
    (
        r"errno -2|errno 11001|name or service not known|nodename nor servname",
        "O nome do servidor não resolve",
        "O DNS não sabe traduzir esse nome para um endereço.",
        "Confira o endereço em Servidores → editar. Se for nome, "
        "considere usar o IP: um painel que depende de DNS interno para "
        "diagnosticar rede perde os dois ao mesmo tempo.",
    ),
    (
        r"permission denied|auth.*fail|recusou o login",
        "O servidor aceitou a conexão e recusou o login",
        "A rede está boa e o SSH está de pé. O que falhou foi a "
        "credencial — coisa completamente diferente.",
        "Em Servidores → editar, confira usuário e chave/senha. Se a "
        "chave do servidor foi trocada, cadastre a nova.",
    ),
    (
        r"host key|hostkeynotverifiable|não confere com a cadastrada",
        "A identidade do servidor mudou",
        "A chave que o servidor apresentou não é a que está fixada aqui. "
        "Isso acontece quando a VM é recriada — e é também exatamente o "
        "que um ataque de intermediário pareceria.",
        "Se a máquina foi reinstalada ou recriada, refaça a varredura em "
        "Servidores → editar → verificar identidade. Se NÃO foi, não "
        "prossiga: investigue antes de reconectar.",
    ),
    (
        r"errno 104|reset by peer|connection lost|broken pipe",
        "A conexão caiu no meio",
        "A sessão foi aberta e interrompida. Costuma ser reinício da "
        "máquina, queda de rede ou o próprio SSH sendo reiniciado.",
        "Tente de novo. Se repetir, confira em Monitor se o servidor "
        "está reiniciando sozinho.",
    ),
]


def explicar(erro: str | Exception) -> dict:
    """
    Do texto do erro para causa e ação.

    Devolve sempre as três partes; quando o erro não casa com nada
    conhecido, `significa` e `acao` vêm vazios — e vazio é honesto. A
    alternativa seria inventar uma explicação para um erro que ninguém
    entendeu ainda, que é pior do que não explicar.
    """
    texto = str(erro or "").strip()
    baixo = texto.lower()

    for padrao, resumo, significa, acao in PADROES:
        if re.search(padrao, baixo):
            return {
                "erro": texto[:400],
                "resumo": resumo,
                "significa": significa,
                "acao": acao,
                "conhecido": True,
            }

    return {
        "erro": texto[:400],
        "resumo": "Não consegui falar com o servidor",
        "significa": "",
        "acao": "",
        "conhecido": False,
    }


def mensagem(host_rotulo: str, erro: str | Exception) -> str:
    """
    Uma linha só, para onde não cabe o bloco inteiro (log, aviso).

    Mantém o texto original no fim: quem for investigar precisa do erro
    exato, e resumo que apaga o original obriga a ir ao log do servidor.
    """
    info = explicar(erro)
    if info["conhecido"]:
        return f"{host_rotulo}: {info['resumo'].lower()} — {info['erro']}"
    return f"{host_rotulo}: {info['erro']}"
