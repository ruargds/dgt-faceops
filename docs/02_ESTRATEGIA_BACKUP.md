# Estratégia de backup do Face Detect

> Documento central do projeto. Se você só for ler um, leia este.

## O problema com o backup oficial

A documentação da NtechLab para 2.4.1 manda fazer assim:

```bash
sudo docker-compose stop
sudo tar -cvzf ~/configs.tar.gz -C /opt/findface-multi/ configs
sudo tar -cvzf ~/data.tar.gz   -C /opt/findface-multi/ data
sudo cp /opt/findface-multi/docker-compose.yaml ~/
```

Três problemas, na ordem em que doem:

**1. Para o sistema.** `docker-compose stop` derruba todos os containers.
Enquanto o `tar` roda, não há reconhecimento facial, não há gravação de
evento, as câmeras ficam sem consumidor. O que passou na frente da câmera
nesse período não existe.

**2. É enorme.** `data/` contém, junto:

| Dentro de `data/` | O que é | Ordem de grandeza |
|---|---|---|
| `findface-upload/` | fotos originais de evento, thumbnails, imagens normalizadas | dezenas a centenas de GB |
| `postgresql/` | cadastros, usuários, câmeras, dossiês, configuração | centenas de MB a poucos GB |
| `findface-tarantool-server/` | vetores faciais e eventos de identificação | GB |
| `mongodb/` | metadados dos trechos de vídeo (Video Recorder) | variável |
| `redis/` | fila temporária de vetores | irrelevante |

As fotos de evento dominam. E são JPEG — já comprimidas, então o `gzip`
não ajuda quase nada.

**3. Não dá para agendar.** Um procedimento que leva horas e para a
produção não roda todo dia. É por isso que não existe botão de backup na
interface da NtechLab: o único procedimento que eles documentam não cabe
num botão.

## A saída: separar o que é caro do que é valioso

A observação que resolve o problema: **o que você mais precisa recuperar é
a parte pequena**. Cadastros, dossiês, usuários, configuração de câmera e os
vetores faciais somam alguns GB. As fotos de evento — que são quase todo o
volume — têm valor probatório, mas o sistema volta a funcionar sem elas.

Daí os três perfis:

| Perfil | Conteúdo | Tamanho | Downtime | Recorrência sugerida |
|---|---|---|---|---|
| **Config** | `configs/` + `docker-compose.yaml` + licença | MB | zero | a cada 6h |
> **Antes de disparar, o painel diz quanto vai ocupar.** Ao escolher o
> servidor, o modal mede lá dentro — `configs/`, tamanho dos bancos
> (perguntado ao próprio PostgreSQL), Tarantool e diretório de dados — e
> mostra o artefato estimado por perfil, o espaço livre no disco do painel
> e no staging do servidor. O artefato é montado no servidor e copiado
> para cá: precisa caber nos dois lados.
>
> Onde já houve execução daquele perfil naquele servidor, o número exibido
> é o **tamanho real** dela, não uma conta de compressão. E onde o `du` do
> diretório de dados não termina no prazo — normal com milhões de fotos de
> evento — a tela diz **"não medido"**, em vez de mostrar um número pequeno
> que faria alguém disparar achando que cabe.

| **Essencial** | Config + `pg_dump` de todos os bancos + snapshot do Tarantool | GB | zero | diário, 02:00 |
| **Completo** | Procedimento oficial: `configs/` + `data/` inteiro | centenas de GB | **sim** | mensal, em janela |

## Perfil `config`

**O que leva:** `configs/` tarado, o `docker-compose.yaml` em uso, o `.env`
do Face Detect e qualquer arquivo de licença encontrado nos três primeiros
níveis do diretório de instalação.

**O que recupera:** a configuração do sistema. Limiares de reconhecimento,
serviços habilitados, parâmetros de rede, licença. Depois de reinstalar o
Face Detect, aplicar este backup devolve o sistema ao ajuste que estava.

**O que não recupera:** nada de dado. Zero cadastro, zero evento.

**Por que vale mesmo assim:** é barato (segundos, megabytes) e cobre o erro
mais comum de todos — alguém ajustou uma configuração, o sistema piorou, e
ninguém lembra o que era antes.

## Perfil `essencial` — o backup do dia a dia

**O que leva, na ordem:**

1. Tudo do perfil `config`
2. `pg_dumpall --globals-only` — papéis e permissões do PostgreSQL. O
   `pg_dump` por banco **não** leva isso, e sem os papéis o restore falha
   com erro de permissão que parece corrupção.
3. `pg_dump -Fc` de **cada** banco existente. Os nomes são enumerados via
   `pg_database`, não chutados: o Face Detect cria `ffsecurity`,
   `ffsecurity_identity_provider`, `multi_audit` e outros conforme os
   módulos habilitados, e a lista muda entre instalações.
4. `box.snapshot()` em cada container Tarantool, seguido do arquivamento de
   `data/findface-tarantool-server/`.

**Por que `-Fc`:** formato custom, comprimido internamente, restaurável por
objeto individual. Dá para restaurar uma tabela só, sem tocar no resto.

**Por que `box.snapshot()` antes de copiar:** o Tarantool grava em `.xlog`
(log de escrita) e consolida em `.snap` (snapshot). Copiar o diretório sem
forçar o snapshot pega xlogs no meio da escrita. O Tarantool reaplica os
xlogs no restore e normalmente sobrevive, mas "normalmente" não é palavra
que se aceite num plano de backup.

> **Ponto que precisa de validação em campo:** o disparo do `box.snapshot()`
> tenta duas rotas (`tarantoolctl` via socket de console, e `tarantool` via
> stdin). Se nenhuma funcionar na sua instalação, o script **avisa no log**,
> copia os arquivos de qualquer forma e registra o método usado como
> `copia-direta`. Confira o campo `tarantool_metodo` no log da primeira
> execução. Enquanto ele não sair como `tarantoolctl`, trate o Tarantool
> deste backup como "provavelmente bom", não "garantido".

**O que recupera:** cadastros, dossiês, listas de vigilância, usuários e
permissões, câmeras e suas configurações, contadores, e — o mais
importante — **os vetores faciais**. Restaurado isto, o sistema volta a
reconhecer as pessoas cadastradas.

**O que NÃO recupera:** as fotos originais dos eventos. O histórico de
"quem passou onde e quando" fica no PostgreSQL, mas as imagens em si somem.

**Isso é aceitável?** Depende do que a operação precisa. Se as imagens de
evento têm valor probatório e precisam ser preservadas, o perfil
`essencial` **não basta** — é preciso ou o `completo` periódico, ou uma
sincronização incremental separada das fotos (ver *Fora deste ciclo* em
[SCOPE.md](../SCOPE.md)).

## Perfil `completo`

Implementa o procedimento oficial da NtechLab, com três coisas que o
manual não tem:

**Verificação de espaço antes de começar.** O script mede `data/` com
`du -sb`, mede o espaço livre no destino e **aborta** se o livre for menor
que 60% do bruto. Compressão de JPEG rende pouco; 60% é a margem que evita
encher o disco do servidor de produção às 3 da manhã.

**Rede de segurança para subir o stack.** Um `trap` em `EXIT INT TERM`
garante que, se o script morrer no meio — sinal, disco cheio, sessão SSH
caindo — o stack sobe de volta. Reconhecimento facial fora do ar por causa
de um backup que falhou é pior que não ter backup.

**Medição do downtime.** O tempo real de parada é registrado e aparece na
tela, junto da execução. Serve para dimensionar a janela da próxima vez.

**Trava de agendamento:** um agendamento com perfil `completo` só executa
se tiver o campo de aceite de janela marcado. Sem o aceite, ele registra
`bloqueado: perfil completo sem aceite de janela` e não roda. Melhor falhar
visível do que parar a produção sem ninguém ter autorizado.

## Recorrência sugerida

Para as quatro VMs, um ponto de partida razoável:

| Agendamento | Perfil | Cron | Por quê |
|---|---|---|---|
| Config frequente | `config` | `0 */6 * * *` | barato; pega mudança de ajuste no mesmo dia |
| Essencial diário | `essencial` | `0 2 * * *` | janela de menor movimento; recupera o que importa |
| Completo mensal | `completo` | `0 3 1 * *` | dia 1º, 03:00, com aceite de janela |

Ajuste o horário do `essencial` para não coincidir nas quatro VMs se elas
dividirem link de saída — quatro uploads simultâneos para o Azure saturam
a banda.

## Retenção

Aplicada **só no disco do painel**. Azure e Google Drive ficam com política
própria de ciclo de vida — apagar de lá por conta própria arriscaria remover
o único arquivo que sobrou de um incidente.

Padrões (configuráveis por agendamento):

| Perfil | Dias | Racional |
|---|---|---|
| `config` | 90 | pequeno; vale guardar histórico longo |
| `essencial` | 30 | um mês de pontos de retorno diários |
| `completo` | 180 | grande, mas é o único com as imagens |

`0` desliga a retenção — nada é apagado automaticamente.

## Integridade

O `sha256sum` é calculado **no servidor**, depois de empacotar. O painel
recalcula depois de transferir e compara. Divergência aborta a execução com
`checksum nao confere apos a transferencia`.

Além disso, o tamanho informado pelo servidor é comparado com o tamanho
recebido — pega transferência truncada antes mesmo do checksum.

Um artefato marcado `sucesso` no histórico passou pelas duas conferências.

## Onde os arquivos ficam

```
No servidor do Face Detect (temporário)
  /var/backups/faceops/faceops_<perfil>_<data>.tar.gz
  -> apagado assim que o painel confirma a transferência

No painel
  /data/backups/<nome-do-servidor>/faceops_<perfil>_<data>.tar.gz

No Azure Blob
  <container>/<nome-do-servidor>/faceops_<perfil>_<data>.tar.gz   (tier Cool)

No Google Drive
  <remote>:<caminho>/<nome-do-servidor>/faceops_<perfil>_<data>.tar.gz
```

Dentro do `.tar.gz`:

```
<data>/
├── MANIFESTO.txt              # inventário + procedimento de restore
├── config/
│   ├── configs.tar.gz
│   ├── docker-compose.yaml
│   ├── env.bak
│   └── licenca/
├── postgres/                  # perfis essencial
│   ├── globals.sql
│   ├── ffsecurity.dump
│   └── <outros bancos>.dump
├── tarantool/                 # perfil essencial
│   └── tarantool-data.tar.gz
└── completo/                  # perfil completo
    └── data.tar.gz
```

O `MANIFESTO.txt` viaja dentro do artefato justamente para o caso ruim: o
painel indisponível e alguém precisando restaurar com o que tem na mão.

## Primeira execução — o que conferir

Antes de programar recorrência, rode uma vez manualmente e verifique:

1. **Perfil `config`** — termina em segundos? O artefato tem alguns MB?
2. **Perfil `essencial`** — o log lista os bancos encontrados? Cada
   `.dump` tem tamanho plausível (não 0 bytes)? O campo
   `tarantool_metodo` saiu como `tarantoolctl`?
3. **Espaço** — quanto o `essencial` ocupou de verdade? Multiplique por 30
   dias e confirme que o disco do painel aguenta.
4. **Análise de armazenamento** (tela Recursos) — quanto `data/` ocupa em
   cada VM? Esse número decide se o perfil `completo` é viável e com qual
   frequência.
5. **Restore de teste** — o passo que quase ninguém faz e é o único que
   prova que o backup funciona. Ver [03_RESTORE](03_RESTORE.md).

> Backup nunca testado não é backup. É esperança com nome técnico.
