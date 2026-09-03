"""
Catálogo do que cresce sem parar — e o que fazer com cada caso.

Irmão do `catalogo_erros`, para uma pergunta diferente. Lá o gatilho é uma
linha de log; aqui é um **caminho no disco** ou um **container comendo
memória** que não para de subir.

Cada entrada responde as quatro coisas que o operador precisa, na ordem
em que ele precisa:

* **por_que** — por que aquilo cresce. Sem isto o painel estaria só
  apontando o dedo;
* **dano** — o que quebra quando encostar no teto, em termos de operação;
* **contorno** — o que fazer AGORA, com a tela do painel que faz;
* **fabricante** — o que a NtechLab manda fazer, quando ela manda. Onde o
  manual é silencioso, o campo diz isso com todas as letras, em vez de
  vestir prática nossa de recomendação oficial.

E a `fonte`, com a mesma régua do catálogo de erros:

* **campo** — aconteceu neste ambiente e está em `10_ERROS_CONHECIDOS.md`;
* **manual** — documentado pela NtechLab, com a página citada;
* **campo+manual** — visto aqui e confirmado pelo fabricante.

Páginas do manual usadas (FindFace Multi 2.4.1): `logs.html`,
`event-cleaner.html`, `architecture.html`, `backup-restore.html`,
`configuration.html`.

Acrescentar um caso é uma entrada nesta lista. O casamento é por **sinal
explícito** — regex de caminho, regex de nome de container — nunca por
adivinhação sobre texto livre: um palpite errado aqui manda alguém apagar
evento de reconhecimento facial, que é irreversível.
"""
import re

# `onde` é o id da aba no frontend, para o achado virar atalho clicável
# em vez de instrução escrita ("vá em Manutenção" não leva ninguém a
# lugar nenhum às três da manhã).
CAUSAS: list[dict] = [
    # ── Disco ──────────────────────────────────────────────────────────
    {
        "chave": "log_do_sistema",
        "recurso": "disco",
        "caminho": re.compile(r"^/var/log(/|$)|journal", re.I),
        "titulo": "Log do sistema crescendo",
        "por_que": "O log de acesso HTTP do próprio FindFace grava toda "
                   "requisição bem-sucedida. Em operação normal, sem erro "
                   "nenhum, isso deu 8 GB por dia num servidor deste "
                   "ambiente — e 99 GB de /var/log num disco de 123 GB.",
        "dano": "Quando a raiz enche, o banco para de gravar e as passagens "
                "deixam de ser registradas. O servidor também para de "
                "conseguir escrever qualquer coisa — um caso ficou 17 dias "
                "com 0 byte livre, e o sintoma foi falha de login.",
        "contorno": "Em Manutenção: 'Conter crescimento' aplica o filtro na "
                    "chegada ao rsyslog (nada do FindFace reinicia) e "
                    "'Arquivar log antigo' move o que já rotacionou para um "
                    "disco com folga.",
        "fabricante": "A NtechLab recomenda teto no journal "
                      "(`SystemMaxUse=3G` em journald.conf) e o driver "
                      "`journald` no daemon.json — manual 2.4.1, logs.html. "
                      "É o que a contenção do painel aplica.",
        "onde": "manutencao",
        "fonte": "campo+manual",
    },
    {
        "chave": "log_de_container",
        "recurso": "disco",
        "caminho": re.compile(r"/var/lib/docker/containers", re.I),
        "titulo": "Log de container sem rotação",
        "por_que": "Com o driver `json-file` e sem `max-size`, o Docker "
                   "guarda a saída do container em arquivo que só cresce. "
                   "Um container verboso enche o disco sozinho, e "
                   "`docker logs` continua respondendo — então nada parece "
                   "errado até o `df`.",
        "dano": "Disco raiz cheio, com o mesmo efeito de qualquer disco "
                "cheio: banco sem gravar e reconhecimento parado.",
        "contorno": "Em Logs ao vivo, veja qual container despeja mais "
                    "linha. A contenção de Manutenção não alcança este "
                    "arquivo — ele é do Docker, não do rsyslog.",
        "fabricante": "A NtechLab recomenda o driver `journald` no "
                      "daemon.json (logs.html), que passa a saída para o "
                      "journal — onde o teto de 3 GB dela vale. Trocar "
                      "driver exige reiniciar o Docker, e isso derruba o "
                      "FindFace: é janela de manutenção, não clique de "
                      "plantão.",
        "onde": "logs",
        "fonte": "manual",
    },
    {
        "chave": "eventos_e_fotos",
        "recurso": "disco",
        "caminho": re.compile(
            r"/data(/|$)|uploads|/ffupload|findface-multi-legacy", re.I
        ),
        "titulo": "Eventos e fotos de reconhecimento acumulando",
        "por_que": "Cada passagem grava a foto do rosto, a miniatura e o "
                   "quadro completo. O volume é proporcional ao movimento "
                   "diante das câmeras, então cresce todo dia, sem defeito "
                   "nenhum. É o crescimento ESPERADO da operação.",
        "dano": "Disco cheio no servidor que hospeda os dados. Antes disso, "
                "o backup do perfil `completo` deixa de caber no destino.",
        "contorno": "Em Manutenção → Limpeza de eventos, apagar evento "
                    "antigo é a única ação que realmente devolve disco. É a "
                    "mais destrutiva do painel: irreversível, exige "
                    "confirmação digitada e não roda com backup em curso.",
        "fabricante": "Procedimento oficial: `manage.py cleanup` com prazo "
                      "em segundos por tipo de evento, e a recorrência em "
                      "`CLEANUP_SCHEDULE` / `vms_cleanup` — manual 2.4.1, "
                      "event-cleaner.html. O manual também proíbe reiniciar "
                      "container do FindFace enquanto a purga roda.",
        "onde": "manutencao",
        "fonte": "campo+manual",
    },
    {
        "chave": "base_biometrica",
        "recurso": "disco",
        "caminho": re.compile(r"tarantool", re.I),
        "titulo": "Base biométrica do Tarantool crescendo",
        "por_que": "É a base de vetores faciais — cresce com cadastro e com "
                   "os snapshots de checkpoint. A instalação deste ambiente "
                   "tem 16 shards e 16 réplicas, então o mesmo dado ocupa "
                   "espaço em duas cópias.",
        "dano": "Sem espaço, o Tarantool para de gravar e a BUSCA facial "
                "para — mesmo com todo o resto do stack de pé. E a limpeza "
                "de eventos NÃO libera este espaço: evento e face cadastrada "
                "são coisas diferentes.",
        "contorno": "Não há purga a fazer aqui. Confira se o crescimento "
                    "acompanha cadastro novo (esperado) ou se são snapshots "
                    "acumulando; o caminho de longo prazo é disco maior ou "
                    "separar os dados em disco próprio.",
        "fabricante": "O manual documenta o sharding, não retenção de base "
                      "biométrica — não existe recomendação de limpeza da "
                      "NtechLab para este diretório. E a base não é "
                      "compatível entre versões maiores do produto: migrar "
                      "exige o backup/restore do fabricante.",
        "onde": "recursos",
        "fonte": "campo+manual",
    },
    {
        "chave": "banco_do_findface",
        "recurso": "disco",
        "caminho": re.compile(r"postgres|timescale|mongo|clickhouse|etcd", re.I),
        "titulo": "Banco de dados do FindFace crescendo",
        "por_que": "Os eventos vivem no banco antes de virar arquivo. WAL do "
                   "Postgres e coleção do Mongo crescem com a carga de "
                   "gravação; o que apaga isso é a purga de eventos, não a "
                   "limpeza de arquivo.",
        "dano": "Banco sem espaço para de aceitar escrita. O reconhecimento "
                "segue rodando e nenhuma passagem é registrada — a pior "
                "combinação, porque não há erro visível na operação.",
        "contorno": "Em Manutenção → Limpeza de eventos: é ela que reduz o "
                    "banco. Arquivar log não ajuda em nada aqui.",
        "fabricante": "A retenção de evento é configurada no próprio "
                      "FindFace (`manage.py cleanup`, event-cleaner.html). "
                      "O manual não documenta manutenção manual dos bancos "
                      "— mexer neles por fora é risco sem respaldo.",
        "onde": "manutencao",
        "fonte": "manual",
    },
    {
        "chave": "staging_do_painel",
        "recurso": "disco",
        "caminho": re.compile(r"faceops|staging|/tmp/ffmulti", re.I),
        "titulo": "Sobra de backup do próprio painel",
        "por_que": "O backup monta o artefato num diretório de staging DENTRO "
                   "do servidor antes de copiar para o destino. Execução "
                   "interrompida deixa esse arquivo lá.",
        "dano": "O painel que existe para proteger o servidor passa a ser "
                "quem enche o disco dele.",
        "contorno": "Em Backups, confira execução com falha; em Manutenção → "
                    "faxina pontual, a categoria 'Sobras de staging'. É "
                    "problema nosso, e o primeiro a descartar antes de "
                    "procurar culpado fora.",
        "fabricante": "Não se aplica — é código deste painel, não do "
                      "FindFace.",
        "onde": "backups",
        "fonte": "campo",
    },
    {
        "chave": "arquivo_apagado_aberto",
        "recurso": "disco",
        "caminho": re.compile(r"\(deleted\)"),
        "titulo": "Arquivo apagado que ainda ocupa espaço",
        "por_que": "Alguém apagou um log com `rm` enquanto o processo o "
                   "mantinha aberto. O nome sai do diretório, o inode só é "
                   "devolvido quando o processo fecha — então o `du` não "
                   "acha nada e o `df` continua cheio.",
        "dano": "O espaço nunca volta sozinho, e a investigação vai para o "
                "lugar errado: procura-se arquivo grande que já não existe.",
        "contorno": "Reinicie quem segura o arquivo (costuma ser o rsyslog). "
                    "E no arquivo de log ATIVO use `truncate -s 0`, nunca "
                    "`rm` — é o que a tela de Manutenção faz.",
        "fabricante": "Comportamento do Linux, não do FindFace.",
        "onde": "manutencao",
        "fonte": "campo",
    },
    {
        "chave": "inodes",
        "recurso": "disco",
        "caminho": re.compile(r"^inodes$"),
        "titulo": "Inodes esgotados com disco livre",
        "por_que": "Milhões de fotos pequenas gastam a tabela de inodes "
                   "antes de gastar os bytes. É o formato do dado deste "
                   "produto: muitos arquivos minúsculos.",
        "dano": "'No space left on device' com o `df -h` mostrando espaço "
                "sobrando — e ninguém entende por quê.",
        "contorno": "A saída é a mesma da ocupação: purgar evento antigo "
                    "(Limpeza de eventos). Formatar com mais inodes é "
                    "decisão de infraestrutura, não de plantão.",
        "fabricante": "Não documentado pelo fabricante.",
        "onde": "manutencao",
        "fonte": "campo",
    },

    # ── Memória ────────────────────────────────────────────────────────
    {
        "chave": "video_worker",
        "recurso": "memoria",
        "container": re.compile(r"video-worker|video-manager", re.I),
        "titulo": "Serviço de vídeo comendo memória",
        "por_que": "Cada stream de câmera custa memória, e câmera "
                   "problemática (reconectando, resolução alta, timeout) "
                   "custa mais. O consumo sobe com o número de câmeras "
                   "ligadas, não com o número de reconhecimentos.",
        "dano": "O kernel mata o container por falta de memória. O "
                "reconhecimento das câmeras daquele worker para SEM erro na "
                "operação — só falta de evento, que ninguém nota na hora.",
        "contorno": "Reiniciar o worker devolve a memória e é seguro (as "
                    "câmeras reconectam). Se voltar a subir na mesma "
                    "velocidade, o problema é quantidade/qualidade de "
                    "câmera, não o container. Em Serviços, confira também a "
                    "contagem de reinícios.",
        "fabricante": "O manual dimensiona o `video-worker` por stream "
                      "(architecture.html) e não documenta vazamento — "
                      "tratar como dimensionamento antes de tratar como bug.",
        "onde": "servicos",
        "fonte": "campo+manual",
    },
    {
        "chave": "extraction",
        "recurso": "memoria",
        "container": re.compile(r"extraction-api|liveness", re.I),
        "titulo": "Extração de características ocupando memória",
        "por_que": "O serviço carrega modelo e cache na subida. Consumo alto "
                   "e ESTÁVEL é normal; o que interessa é ele subir sem "
                   "parar depois de já estar de pé há horas.",
        "dano": "Sem extração não há reconhecimento — a passagem é gravada "
                "sem identificação.",
        "contorno": "Confira se o crescimento coincide com pico de câmeras. "
                    "Na PRIMEIRA subida, o manual avisa que os serviços de "
                    "GPU levam até 45 minutos por causa do cache: consumo "
                    "subindo aí é esperado, não vazamento.",
        "fabricante": "Aquecimento de até 45 min na primeira subida está no "
                      "manual da NtechLab. Vazamento de memória, não.",
        "onde": "servicos",
        "fonte": "campo+manual",
    },
    {
        "chave": "container_sem_teto",
        "recurso": "memoria",
        "container": re.compile(r".*"),
        "titulo": "Container sem limite de memória",
        "por_que": "Sem `mem_limit`, o container pode crescer até acabar a "
                   "RAM da máquina inteira. Aí o kernel escolhe a vítima — e "
                   "pode não ser o culpado.",
        "dano": "Um serviço vazando derruba OUTRO serviço, e a investigação "
                "começa pelo inocente.",
        "contorno": "Reiniciar o container que cresce devolve a memória "
                    "agora. Limite no compose é mudança no arquivo do "
                    "fabricante: vale decidir com calma, fora do plantão.",
        "fabricante": "O compose distribuído pela NtechLab não define teto "
                      "de memória por serviço. Alterá-lo é escolha nossa, "
                      "sem respaldo no manual.",
        "onde": "servicos",
        "fonte": "campo",
    },
    {
        "chave": "swap",
        "recurso": "swap",
        "container": re.compile(r"^$"),
        "titulo": "Swap subindo — a máquina está usando disco como memória",
        "por_que": "Falta RAM para a carga. O kernel empurra página para o "
                   "disco em vez de matar processo, então nada quebra: fica "
                   "lento.",
        "dano": "Latência de disco no caminho do reconhecimento. Em servidor "
                "com teto de IOPS, swap ainda concorre com o banco pelo "
                "mesmo teto e piora tudo junto.",
        "contorno": "Veja em Processos quem ocupa memória. Swap em uso "
                    "constante é sinal de VM pequena para a carga — é "
                    "dimensionamento, não defeito.",
        "fabricante": "Não documentado pelo fabricante.",
        "onde": "processos",
        "fonte": "campo",
    },
]

POR_CHAVE = {c["chave"]: c for c in CAUSAS}

# Casos que NÃO são causa e aparecem como se fossem. Ficam registrados
# para o serviço poder dizer "isto não é vazamento" com a mesma clareza
# com que diz o contrário — a regra da casa é não afirmar sobre ausência
# de dado, e "cache alto" já fez gente trocar de VM sem precisar.
NAO_E_VAZAMENTO = [
    "Cache e buffer NÃO contam como memória usada aqui: o painel lê "
    "MemAvailable do /proc/meminfo, então cache cheio não vira alarme.",
    "Consumo alto e estável não é vazamento. O que caracteriza vazamento é "
    "a subida que não para depois de horas de operação.",
    "Crescimento de disco proporcional ao movimento nas câmeras é a "
    "operação funcionando, não defeito — o que se decide aí é retenção.",
]


def casar_caminho(caminho: str) -> dict | None:
    """
    Qual causa explica o crescimento DESTE caminho.

    Ordem do catálogo é a ordem de precedência: entrada mais específica
    primeiro. Nada casou = None, e quem chama diz "não sei o que é isto"
    em vez de escolher a entrada mais parecida.
    """
    alvo = (caminho or "").strip()
    if not alvo:
        return None
    for causa in CAUSAS:
        padrao = causa.get("caminho")
        if padrao is not None and padrao.search(alvo):
            return causa
    return None


def casar_container(nome: str, recurso: str = "memoria") -> dict | None:
    """
    Qual causa explica o consumo DESTE container.

    `container_sem_teto` casa com qualquer nome de propósito: é o caso
    genérico, e fica por último no catálogo justamente para só valer
    quando nenhum serviço conhecido casou antes.
    """
    alvo = (nome or "").strip()
    if not alvo:
        return None
    for causa in CAUSAS:
        padrao = causa.get("container")
        if padrao is None or causa.get("recurso") != recurso:
            continue
        if padrao.pattern in ("^$",):
            continue
        if padrao.search(alvo):
            return causa
    return None
