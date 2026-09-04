"""
Catálogo de erros conhecidos — sintoma reconhecido vira causa e ação.

É a versão executável do `docs/10_ERROS_CONHECIDOS.md`: cada entrada aqui
saiu de um caso que aconteceu de verdade neste ambiente. O painel casa a
linha de log com estes padrões e mostra, junto do erro, o que já se sabe
sobre ele.

**Por que catálogo e não modelo de linguagem.** Um modelo pequeno o
bastante para rodar na VM do painel não conhece `findface-video-worker`,
sharding de Tarantool nem o procedimento de limpeza da NtechLab — ele
produziria comando com cara de certo. Aqui, cada sugestão é escrita por
quem operou a máquina, aponta a tela do painel que resolve, e pode ser
auditada linha a linha. Num painel que reinicia container de
reconhecimento facial em produção, isso vale mais que fluência.

Acrescentar um padrão é uma entrada nesta lista. Quando algo custar tempo
para achar, registre no doc **e** aqui.

Cada entrada declara a `fonte`:

* **campo** — aconteceu neste ambiente e está em `10_ERROS_CONHECIDOS.md`;
* **manual** — documentado pela NtechLab, com a página citada;
* **campo+manual** — visto aqui e confirmado pelo fabricante.

Páginas do manual usadas (Face Detect 2.4.1):
`logs.html`, `status.html`, `ntls_status.html`, `licensing.html`,
`event-cleaner.html`.
"""
import re

# `onde` usa o id da aba (o mesmo de `nav()` no frontend), para o achado
# virar atalho clicável em vez de instrução escrita.
CATALOGO: list[dict] = [
    {
        "chave": "oom",
        "regex": re.compile(r"out of memory|oom[-_ ]?kill|killed process|cannot allocate memory", re.I),
        "titulo": "Processo morto por falta de memória",
        "causa": "O sistema matou o container por falta de RAM. Num servidor de "
                 "reconhecimento facial isso costuma vir junto de pico de câmeras.",
        "acao": "Confira a memória em Recursos antes de reiniciar — subir o container "
                "de novo sem liberar memória repete o ciclo em minutos.",
        "onde": "recursos",
        "fonte": "campo",
    },
    {
        "chave": "vram",
        "regex": re.compile(r"cuda (?:error|out of memory)|cudnn|nvml|no CUDA-capable|"
                            r"insufficient .*driver|failed to allocate .*gpu", re.I),
        "titulo": "Falha de GPU ou memória de vídeo",
        "causa": "A placa não atendeu a alocação. Depois de trocar o perfil de vGPU "
                 "(ex.: A10-12Q) é comum o driver não reengatar no reboot, e os "
                 "containers de CUDA subirem sem GPU.",
        "acao": "No servidor, confira `nvidia-smi` e a licença (`nvidia-smi -q | grep -i lic`). "
                "Só reinicie findface-extraction-api / video-worker / liveness-api depois "
                "que a GPU aparecer saudável. ATENÇÃO: o manual da NtechLab avisa que, na "
                "PRIMEIRA subida, os serviços de GPU levam até 45 minutos por causa do "
                "cache — GPU parada logo após um restart pode ser normal, não falha.",
        "onde": "recursos",
        "fonte": "campo+manual",
    },
    {
        "chave": "disco_cheio",
        "regex": re.compile(r"no space left on device|disk (?:is )?full|write error.*space", re.I),
        "titulo": "Disco cheio",
        "causa": "Sem espaço, o banco para de gravar e o reconhecimento para junto. "
                 "Neste ambiente a origem clássica é o /var/log — o log de acesso HTTP "
                 "do Face Detect chegou a 8 GB/dia num servidor real.",
        "acao": "Em Manutenção, use Diagnosticar para ver o que ocupa e Arquivar log antigo. "
                "Nunca apague o arquivo de log ativo com `rm`: o rsyslog o mantém aberto "
                "e o espaço não volta. A própria NtechLab recomenda desligar o rsyslog "
                "(rotação ruim) e limitar o journald com SystemMaxUse.",
        "onde": "manutencao",
        "fonte": "campo+manual",
    },
    {
        "chave": "rtsp",
        "regex": re.compile(r"rtsp|stream (?:closed|error|timeout)|failed to (?:open|read) (?:stream|source)|"
                            r"connection to camera", re.I),
        "titulo": "Problema no stream de câmera",
        "causa": "Stream instável faz o video-worker morrer e voltar. É a causa mais "
                 "comum de contagem de reinício subindo — não é container quebrado.",
        "acao": "O log aponta qual câmera. Confira a rede até ela e o estado do "
                "dispositivo em Licenciamento e dispositivos.",
        "onde": "dispositivos",
        "fonte": "campo",
    },
    {
        "chave": "banco_recusa",
        "regex": re.compile(r"connection refused|could not connect to server|"
                            r"too many clients|FATAL:\s+password|remaining connection slots", re.I),
        "titulo": "Banco de dados recusando conexão",
        "causa": "PostgreSQL/pgbouncer fora do ar, sem slot livre, ou credencial errada. "
                 "Em topologia distribuída o banco pode estar em outra VM que não a "
                 "que está reclamando.",
        "acao": "Veja o estado do postgresql e do pgbouncer em Serviços. Se o banco fica "
                "noutro servidor, confira lá — a Topologia mostra quem é quem.",
        "onde": "servicos",
        "fonte": "campo",
    },
    {
        "chave": "tarantool",
        "regex": re.compile(r"tarantool|box\.snapshot|xlog|vinyl", re.I),
        "titulo": "Aviso do Tarantool",
        "causa": "O Tarantool guarda os vetores faciais. Mensagem dele costuma envolver "
                 "snapshot, xlog ou shard fora do ar.",
        "acao": "Não reinicie durante limpeza de eventos — o manual da NtechLab é "
                "explícito sobre corromper o banco. Confira o container em Serviços.",
        "onde": "servicos",
        "fonte": "campo+manual",
    },
    {
        "chave": "docker_permissao",
        "regex": re.compile(r"permission denied.*docker\.sock|got permission denied while trying to connect", re.I),
        "titulo": "Usuário sem acesso ao Docker",
        "causa": "O usuário SSH não está no grupo `docker` — padrão da instalação do "
                 "Face Detect. A leitura de serviços volta vazia, sem erro claro.",
        "acao": "O painel contorna usando sudo por host. Para resolver na origem: "
                "`sudo usermod -aG docker <usuario>` e relogar.",
        "onde": "servidores",
        "fonte": "campo",
    },
    {
        "chave": "crlf",
        "regex": re.compile(r"\$'\\r'|\\r': command not found|bad interpreter", re.I),
        "titulo": "Script com fim de linha do Windows (CRLF)",
        "causa": "O arquivo .sh veio de ZIP ou o Git converteu a quebra de linha.",
        "acao": "No repositório: `find scripts -name '*.sh' -exec sed -i 's/\\r$//' {} \\;` "
                "e reconstrua. O .gitattributes previne em clone novo.",
        "onde": "",
        "fonte": "campo",
    },
    {
        "chave": "healthcheck",
        "regex": re.compile(r"health.?check (?:failed|error)|unhealthy", re.I),
        "titulo": "Healthcheck do container falhando",
        "causa": "O container está de pé mas não responde ao próprio teste de saúde — "
                 "costuma ser dependência (banco, fila) ainda indisponível.",
        "acao": "Veja o log completo do container em Serviços; o erro do healthcheck "
                "quase sempre aponta quem ele não conseguiu alcançar.",
        "onde": "servicos",
        "fonte": "campo",
    },
    {
        "chave": "certificado",
        "regex": re.compile(r"certificate (?:verify )?fail|x509|ssl.*handshake|expired certificate", re.I),
        "titulo": "Problema de certificado TLS",
        "causa": "Certificado vencido, autoassinado não confiado, ou relógio da máquina "
                 "desalinhado (que faz certificado válido parecer vencido).",
        "acao": "Confira a data com `timedatectl` antes de mexer no certificado — "
                "relógio errado é a causa mais barata e a mais esquecida.",
        "onde": "",
        "fonte": "campo",
    },
    {
        "chave": "licenca_ff",
        "regex": re.compile(r"license (?:expired|invalid|limit|not found)|ntls|"
                            r"licen[cç]a|license server", re.I),
        "titulo": "Licença do Face Detect / findface-ntls",
        "causa": "Licença vencida, sem comunicação com o NTLS, ou limite de câmeras/faces "
                 "atingido — o reconhecimento para sem o container cair, que é o que "
                 "torna esse caso difícil de perceber.",
        "acao": "No servidor do NTLS: `curl http://localhost:3185/v1/licenses.json -s | jq`. "
                "O manual da NtechLab lê o campo `.last_updated` assim: até 5s é normal; "
                "de 5 a 30s indica problema de rede ou de disco; de 30 a 120s 'algo ruim "
                "aconteceu'; acima de 120s a fonte de licença deu timeout. Se "
                "`.licenses[].valid.valid` for false, a conexão nunca foi estabelecida — "
                "o motivo está em `.valid.description`, e o licenciamento online precisa "
                "alcançar license.ntechlab.com na 443. No painel, validade e consumo "
                "ficam em Licenciamento e dispositivos.",
        "onde": "dispositivos",
        "fonte": "manual",
    },
    {
        "chave": "limpeza_eventos",
        "regex": re.compile(r"event.?cleaner|purge|cleanup.*(?:event|record)|vms_cleanup", re.I),
        "titulo": "Limpeza de eventos em andamento ou falhando",
        "causa": "É o procedimento que libera disco de verdade (as fotos de evento "
                 "chegaram a 242 GB de 268 GB num servidor real), e também o mais "
                 "destrutivo.",
        "acao": "O manual da NtechLab é explícito: NÃO reinicie container do Face Detect "
                "nem o Docker enquanto a purga roda — causa erro no banco. O painel já "
                "recusa reiniciar durante a limpeza; espere terminar.",
        "onde": "manutencao",
        "fonte": "campo+manual",
    },
]

# Sinais de gravidade na própria linha, quando nenhum padrão do catálogo
# casa. Serve para separar "erro" de ruído — sem isso, guardar padrão de
# log viraria guardar o log inteiro.
RE_ERRO = re.compile(
    r"\b(error|erro|exception|traceback|fatal|panic|critical|failed|failure|"
    r"refused|timeout|denied|unable to|cannot|could not)\b", re.I
)
RE_AVISO = re.compile(r"\b(warn|warning|aviso|deprecated|retry|retrying)\b", re.I)


def classificar(linha: str) -> tuple[str, dict | None]:
    """
    Devolve (nivel, padrão conhecido ou None) para uma linha de log.

    `nivel` é "erro", "aviso" ou "info" — só os dois primeiros são
    guardados, senão o painel viraria um segundo syslog.
    """
    for padrao in CATALOGO:
        if padrao["regex"].search(linha):
            # Um padrão conhecido é sempre pelo menos aviso: ele existe
            # justamente porque já custou tempo de alguém.
            nivel = "erro" if RE_ERRO.search(linha) else "aviso"
            return nivel, padrao

    if RE_ERRO.search(linha):
        return "erro", None
    if RE_AVISO.search(linha):
        return "aviso", None
    return "info", None


POR_CHAVE = {p["chave"]: p for p in CATALOGO}
