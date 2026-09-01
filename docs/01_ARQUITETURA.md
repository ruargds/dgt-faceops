# Arquitetura

## Visão geral

```
┌─────────────────────────────────────────────────┐
│  Máquina do painel (Windows + WSL2, ou VM Linux)│
│  FORA do ambiente facial                        │
│                                                 │
│  ┌───────────┐   ┌──────────┐   ┌────────────┐  │
│  │ frontend  │──▶│ backend  │──▶│ postgres   │  │
│  │ nginx     │   │ FastAPI  │   │ (do painel)│  │
│  │ React SPA │   │ APSched. │   └────────────┘  │
│  └───────────┘   └────┬─────┘                   │
│                       │  asyncssh (agentless)   │
└───────────────────────┼─────────────────────────┘
                        │  porta 22
       ┌────────────────┼────────────────┬──────────────┐
       ▼                ▼                ▼              ▼
  vm-appserver     vm-dbserver     vm-extraction   vm-ftpserver
  FindFace Multi 2.4.1 — /opt/findface-multi (docker compose)
```

Três containers no painel. Nada instalado nos servidores do FindFace.

## Decisões que valem explicação

### Por que agentless

Instalar agente em servidor de reconhecimento facial de produção significa
mais um processo para atualizar, mais uma porta, mais uma coisa que pode
quebrar. Tudo aqui é `ssh`, que já está lá e já é auditado pelo próprio
servidor.

A pergunta voltou em campo — *"não seria mais preciso com um agente leve em
cada máquina?"* — e a resposta continua não, por três razões concretas:

1. **Os componentes do FindFace já falam HTTP.** O manual do fabricante
   documenta a porta de cada um: `findface-extraction-api` 18666,
   `findface-sf-api` 18411, `findface-video-manager` 18810/18811,
   `findface-video-worker` 18999, `findface-ntls` 3133/3185,
   `findface-upload` 3333, `findface-facerouter` 18820,
   `findface-deduplicator` 18310, `findface-liveness-api` 18301,
   `findface-video-storage` 18611, `findface-video-streamer` 9000,
   `findface-tarantool-server` 32001. Todos atendem em `localhost` da
   máquina onde rodam.
2. **O painel já está dentro.** SSH com sudo é exatamente o alcance que um
   agente teria. O agente não abriria porta nova de informação — abriria
   porta nova de rede.
3. **Agente é dívida.** Mais um binário para versionar, atualizar em quatro
   VMs e explicar numa auditoria de um ambiente de reconhecimento facial.

É isso que a seção **Componentes internos do FindFace**, na aba Descoberta,
faz: abre uma sessão SSH e, de dentro dela, pergunta a cada componente na
porta que o fabricante documentou. Precisão de agente, sem agente. Só
leitura — nenhuma consulta ali muda estado.

Custo: cada leitura abre (ou reaproveita) uma conexão SSH. Mitigado por
pool com TTL de 120s e por concentrar toda a coleta de métrica numa única
execução remota — quatro hosts × seis comandos daria 24 handshakes; assim é
um por host.

### Por que a chave do host é fixada antes da credencial

`asyncssh.get_server_host_key()` lê a chave pública do servidor **sem
autenticar**. Essa chave é guardada e toda conexão posterior é fixada nela.

A alternativa comum — conectar com `known_hosts=None` e confiar na primeira
conexão — tem um furo real: na autenticação por senha, a senha é enviada
durante o handshake. Um atacante no caminho da rede recebe a senha de sudo
antes de você ter chance de comparar fingerprint nenhum.

Se a chave mudar, a conexão é **abortada antes de enviar credencial**, com
mensagem explícita orientando a tratar como incidente (ou refazer a
varredura, se o servidor foi reinstalado de propósito).

### Por que o jobstore do APScheduler é em memória

O jobstore persistente do APScheduler serializa a referência da função
agendada. Qualquer renomeação de módulo ou função quebra os jobs gravados,
e o erro aparece dias depois, na hora que o backup devia rodar.

Aqui a tabela `schedules` é a única fonte de verdade. Na subida, e a cada
mudança, o agendador é remontado a partir dela. Custa milissegundos e nunca
diverge.

Consequência: **o backend precisa rodar com 1 worker.** Com dois, cada um
teria sua cópia do agendador e todo backup rodaria em dobro. Está fixado no
`CMD` do Dockerfile, com o motivo comentado ali.

### Por que ticket de uso único no WebSocket

O navegador não permite mandar cabeçalho `Authorization` ao abrir um
WebSocket. O caminho fácil é `?token=<jwt>` na URL — e aí o JWT vai para o
log de acesso do nginx, para o histórico do navegador e para qualquer proxy
no caminho.

O painel emite um ticket (`POST /api/terminal/ticket/{host_id}`) que vale 30
segundos, serve **uma vez** e só abre aquele host. Vazar o ticket depois do
uso não dá nada.

### Por que as ações são cercadas ao projeto compose

Antes de reiniciar qualquer container, o painel confere o rótulo
`com.docker.compose.project` e recusa se não for o projeto do FindFace
daquele host. Sem essa cerca, `POST /services/{id}/restart` com um nome
arbitrário derruba qualquer container do servidor — inclusive o agente
Zabbix, inclusive o próprio painel se ele estivesse lá.

O nome do projeto é perguntado ao Docker, não deduzido do caminho:
`COMPOSE_PROJECT_NAME` no `.env` muda o padrão, e adivinhar erraria.

### Por que o script de backup vai por stdin

`ffmulti-backup.sh` é enviado pela entrada padrão de um `bash -s` remoto,
não copiado para o servidor. Assim o servidor de produção não acumula
script nosso, uma versão nova do painel já roda a versão nova sem
sincronizar arquivo, e o conteúdo não aparece no `ps` de quem estiver logado.

A senha de sudo segue o mesmo princípio: `sudo -S` lê da entrada padrão, e
nunca aparece na linha de comando.

## Fluxo de um backup, ponta a ponta

```
1.  POST /api/backups/{host_id}          → cria BackupRun, responde 202
2.  asyncio.create_task(...)             → segue em segundo plano
3.  ssh.run_script_stream(sudo=True)     → envia o script, lê linha a linha
4.  on_line() por linha                  → atualiza etapa/progresso
                                           (commit no máximo a cada 2s)
5.  script emite FACEOPS:chave=valor     → artefato, tamanho, checksum, downtime
6.  ssh.download() via SFTP              → traz para /data/backups/_staging
7.  compara tamanho e SHA-256            → divergência aborta
8.  rm no servidor                       → libera o disco de produção
9.  storage.distribuir()                 → Azure, Drive, depois local
10. aplicar_retencao()                   → limpa artefato velho local
11. status = sucesso                     → progresso 100%
```

O passo 8 vem **antes** do 9 de propósito: o servidor de produção não pode
segurar dezenas de GB enquanto o upload roda.

O passo 9 termina com o local porque `enviar_local` **move** o arquivo do
staging — precisa ser o último a tocá-lo.

Se nenhum destino aceitar, a execução falha. Se um falhar e outro der certo,
fica `sucesso` com ressalva registrada em `error`.

## Modelo de dados

| Tabela | Guarda |
|---|---|
| `users` | login, hash de senha, perfil, flag de senha de fábrica |
| `hosts` | endereço, usuário SSH, segredos cifrados, chave de host fixada, caminhos do FindFace |
| `backup_runs` | uma execução: perfil, situação, etapa, progresso, tamanho, checksum, destinos, log |
| `schedules` | recorrência: cron, perfil, destinos, retenção, aceite de janela |
| `audit_logs` | toda ação que muda estado, com detalhe higienizado |
| `terminal_sessions` | sessões do InTerminal, com caminho da gravação |
| `amostras` | série do monitor contínuo: percentuais e absolutos, ~120 bytes por linha |
| `incidentes` | janela de indisponibilidade por host/serviço, com causa provável ([25](25_INCIDENTES_E_LIMIARES.md)) |
| `limiar_overrides` | exceção de limite por host e/ou serviço, sobre o catálogo global |
| `log_padroes` | **molde** de linha de log com contador — não a linha ([27](27_DIAGNOSTICO.md)) |
| `notificacao_contas` | bot do Telegram e grupo de destino; token cifrado ([28](28_AVISOS_TELEGRAM.md)) |
| `notificacao_regras` | de quais servidores/serviços avisar, e a partir de qual gravidade |
| `notificacao_envios` | o que já foi mandado — deduplicação, diagnóstico e retentativa |
| `licenca_amostras` | consumo de licença ao longo do tempo, para a projeção de "quando acaba" |
| `visoes_log` | visões salvas de log ao vivo, compartilhadas |
| `configuracoes` | catálogo chave/valor editável pela web |
| `destinos` | destinos de backup, com credencial cifrada |

As cinco últimas tabelas do monitor (`amostras` em diante) têm retenção
própria e são apagadas pela faxina — ver
[20_PERSISTENCIA](20_PERSISTENCIA.md).

Colunas `*_enc` guardam segredo cifrado com Fernet. **Nenhum schema de
saída as expõe** — a UI confirma o que está guardado pelo fingerprint.

## Serviços do backend

| Módulo | Responsabilidade |
|---|---|
| `ssh_service` | conexão, pool, execução, streaming, SFTP, pinagem de host |
| `metrics_service` | script de coleta e parsers de `/proc`, `df`, `nvidia-smi`, `docker stats` |
| `stack_service` | status dos containers, reinício, ações de stack, cerca do projeto |
| `backup_service` | orquestração da execução, progresso, integridade |
| `storage_service` | destinos (local/Azure/Drive), retenção, caminho de download |
| `scheduler_service` | APScheduler, validação e tradução de cron |
| `terminal_service` | ponte PTY, gravação asciicast, sessões vivas |
| `audit_service` | registro com higienização de segredo |
| `monitor_service` | coletor contínuo, série, alertas — uma execução SSH por host por ciclo |
| `incidente_service` | abre/fecha incidente a partir do que o ciclo já leu; laço de reinício por janela |
| `limiar_service` | resolve limite em cascata: host+serviço > host > serviço > catálogo |
| `log_analise_service` | molde de log (fingerprint), agrupamento e contagem |
| `catalogo_erros` | base de erros conhecidos: sintoma → causa → ação → tela |
| `notificacao_service` | casa evento com regra, monta a mensagem, deduplica |
| `telegram_service` | só envio, sem laço de escuta; sem dependência nova |
| `faxina_service` | retenção diária de tudo que cresce |

Instanciados uma vez na subida e guardados em `app.state`.

## Parsers de métrica — por que assim

**`/proc/meminfo` em vez de `free -h`.** O `free` já vem arredondado e muda
de formato entre versões do `procps`. E o cálculo de "usado" aqui é
`total - MemAvailable`, não `total - free`: contar buffers e cache como uso
é o erro clássico que faz alguém achar que a máquina está estourando quando
o kernel só está usando RAM ociosa como cache.

**`df -P`.** O `-P` garante uma linha por sistema de arquivos, sem quebra
quando o nome do dispositivo é longo.

**`nvidia-smi --format=csv,noheader,nounits`.** Em GPU virtualizada do Azure
(NV-series com GRID) vários campos vêm `[N/A]`. Nenhum parser aqui assume
número — `_to_float` devolve `None` e a UI mostra "n/d".

**Seções delimitadas em vez de JSON gerado no bash.** Montar JSON com `echo`
quebra no primeiro valor com aspas ou acento. O script emite
`###FACEOPS:SECAO` e o Python separa.

## O que roda em segundo plano

| Tarefa | Frequência | Onde |
|---|---|---|
| Agendamentos de backup | conforme cron | APScheduler |
| Varredura de sessões ociosas | 60s | `_varredor_de_ociosas` |
| Backup disparado por HTTP | sob demanda | `asyncio.create_task` |

**Não há polling de métrica.** A coleta acontece só quando alguém clica em
Atualizar. Decisão consciente: o painel bate SSH em servidor de produção a
cada leitura, e fazer isso de minuto em minuto rouba CPU de quem está
reconhecendo rosto — sem ganho, porque o Zabbix já cobre histórico e alerta.
