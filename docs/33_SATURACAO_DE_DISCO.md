# Saturação de disco — o pico de IOPS que derruba o servidor

Um servidor caiu com pico de **~5.000 IOPS**. O painel não tinha como
ver: ele media ocupação em GB e `iowait` da CPU, e **nenhum dos dois
enxerga saturação**.

---

## Por que o disco derruba a máquina inteira

Em disco gerenciado de nuvem existe um **teto de IOPS contratado**, por
tipo e tamanho de disco. 5.000 é exatamente o teto de um Premium SSD P30
(1 TiB) no Azure.

Ao encostar nesse teto o provedor não devolve erro: ele **enfileira**. A
latência de cada operação sai de ~1 ms para centenas de ms, e então
**tudo que toca disco trava junto** — o Docker, o Postgres, o journald, o
systemd e o `sshd`.

O resultado, visto do painel, é este:

| Sintoma | O que parece | O que é |
|---|---|---|
| SSH `Connection refused` | servidor caiu | o `sshd` não consegue nem ler o disco para autenticar |
| `0 de 0 containers` | stack removido | o Docker não consegue ler o próprio estado |
| terminal não abre | rede | disco |

E o mais traiçoeiro: **isso acontece com o disco quase vazio.** Ocupação
em GB não tem relação nenhuma com vazão.

## O que o painel passou a medir

Do `/proc/diskstats`, na **mesma janela** das duas leituras de
`/proc/stat` que a coleta já fazia — sem custo novo:

| Métrica | O que responde |
|---|---|
| `disco_iops` | operações por segundo (leitura + escrita) |
| `disco_util_pct` | quanto do tempo o disco esteve com E/S em andamento |

O que vale para alarme é a **utilização**, não o IOPS: o teto de IOPS
varia por tipo de disco, e um padrão errado geraria alarme falso todo
dia. Por isso:

* `alerta.disco_util_pct` — padrão **85%**. Acima disso o disco está no
  limite, seja qual for o teto contratado.
* `alerta.disco_iops` — padrão **0 (desligado)**. Ponha uns 80% do teto
  do seu disco para o aviso chegar antes da saturação.

O painel reporta o **disco mais castigado**, não a média: a média entre
um disco parado e um saturado esconde exatamente o que se procura.

---

## "Não pode ser a nossa aplicação?"

Pergunta certa, e a resposta honesta era: **sim, plausivelmente.**

### Auditoria da pegada de disco do painel

| O que o painel faz | Frequência | Peso em E/S |
|---|---|---|
| Coleta de métricas (`/proc/*`, `df`, `docker stats`) | a cada 60 s | desprezível |
| Leitura de log de container com incidente aberto | máx. 3/ciclo, 1×/5min por serviço | baixo |
| Apuração no fechamento do incidente (`journalctl` com `-n`) | máx. 2/ciclo | baixo |
| `du` da árvore do Face Detect (Recursos, Manutenção) | **sob demanda** | alto |
| **Backup** (`pg_dump` + `mongodump` + 32 snapshots + `tar`) | **agendado** | **muito alto** |

O monitoramento periódico está descartado. O `du` é pesado, mas é
disparado por clique e tem `timeout`.

**O backup é o suspeito real** — e ele rodava em **prioridade normal de
E/S**, disputando disco de igual para igual com o Face Detect em produção.
Num disco com teto de IOPS, o backup era candidato legítimo a derrubar o
servidor que existe para proteger.

### O que mudou

| Mudança | Efeito |
|---|---|
| `ionice -c3` (classe *idle*) + `nice -n19` | o backup só recebe disco quando ninguém mais quer |
| o mesmo **dentro do container** (`IO_BAIXO_SH`) | `ionice` no cliente do `docker exec` não afeta o processo que roda lá dentro, que é quem lê o banco |
| pausa entre os 32 snapshots do Tarantool | 16 shards + 16 réplicas seguidos viravam um bloco único de escrita pesada |
| `ionice` nos `du` sob demanda | diagnóstico deixa de competir com produção |

Na prática: **o backup fica mais lento sob carga, e para de ser candidato
a derrubar o servidor.** É a troca certa — backup atrasado se resolve
sozinho; servidor caído, não.

Ausência de `ionice` não quebra nada: cai para `nice`, e sem `nice` roda
direto.

### O painel agora acusa a si mesmo

A apuração de incidente passou a cruzar a janela da queda com as
execuções de backup **daquele servidor**. Quando coincide, o primeiro
achado é:

> havia um backup 'essencial' deste servidor em andamento (02/09 03:00 →
> 02/09 03:41, status sucesso). O backup lê o banco inteiro e é a maior
> carga de disco que o painel provoca — considere-o suspeito antes de
> procurar fora.

Um painel que monitora e também escreve tem de conseguir dizer quando o
problema foi ele. Esperar que alguém desconfie e cruze horários à mão é
contar com sorte.

---

## O que ainda depende de decisão sua

O `ionice` reduz o risco; **não aumenta o teto do disco.** Se o Face Detect
sozinho já encosta nos 5.000 IOPS em horário de pico, a saída é
infraestrutura:

| Caminho | Observação |
|---|---|
| Disco com teto maior | P30 → P40/P50, ou **Premium SSD v2**, onde IOPS é configurável sem trocar o tamanho |
| Cache de host `ReadOnly` no disco de dados | ajuda leitura; o Azure não recomenda `ReadWrite` para banco |
| Separar os dados em discos diferentes | Tarantool, Mongo e Postgres competindo no mesmo disco somam no mesmo teto |
| Reduzir a escrita | retenção de eventos do Face Detect (ver [18_LIMPEZA_DE_EVENTOS](18_LIMPEZA_DE_EVENTOS.md)) e do log (ver [14_MANUTENCAO](14_MANUTENCAO.md)) |

Com a medição no ar, dá para responder isso com número em vez de
palpite: veja em **Recursos** o IOPS ao longo do dia e compare com o teto
do disco contratado.

---

## Verificação

| Cenário | Trava |
|---|---|
| `saturacao de disco e medida` | cálculo do IOPS e da utilização; contador reiniciado descartado; disco virtual ignorado; devolve o mais castigado, não a média; limite de IOPS vem desligado |
| `backup do painel nao disputa disco` | `ionice` em cada linha que executa `pg_dump`/`pg_dumpall`/`mongodump`; plano B onde falta `ionice`; pausa entre snapshots; e a apuração usando a correlação de verdade |

A primeira versão da segunda trava conferia a primeira aparição do nome
`pg_dump` no script — que é um **comentário**. Ela passaria com o backup
em prioridade normal. Corrigida para olhar cada linha que de fato executa,
e verificada por injeção: removendo o `ionice`, o teste falha citando a
linha exata.
