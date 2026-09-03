# Persistência — o que sobrevive a quê

## Regra da casa: nada fica sem prazo

Tudo o que o painel grava tem retenção. Não existe "guardo por garantia":
dado sem pergunta que o justifique é lixo com backup.

| O que o painel grava | Prazo | Onde se ajusta |
|---|---|---|
| Amostras do monitor | 30 dias | Configurações → Monitoramento |
| Histórico de consumo de licença | 365 dias | Configurações → Faxina |
| Gravações do InTerminal (`.cast`) | 90 dias | Configurações → Faxina |
| Registros de auditoria | 365 dias (crítico: o triplo) | Configurações → Faxina |
| Sessões de terminal (a linha) | junto da auditoria | Configurações → Faxina |
| Texto do log das execuções | 60 dias — a linha fica, o texto sai | Configurações → Faxina |
| Linha das execuções de backup | 730 dias, **e só sem artefato** | Configurações → Faxina |
| Incidentes fechados | 30 dias | Configurações → Incidentes |
| Moldes de log | 30 dias | Configurações → Análise |
| Avisos enviados no Telegram | 14 dias | Configurações → Notificação |
| Sobras de staging | 24 horas | fixo |
| Artefatos de backup | por perfil, no disco local | no agendamento ou no disparo |

Duas coisas que **não** ficam para sempre por acidente:

- **Execução travada.** Backup que ficou em `executando` quando o painel
  reiniciou está morto — a tarefa vivia no processo que saiu. A subida
  marca essas execuções como falha, com o motivo. Sem isso elas contavam
  como "ocupado" no `/api/saude` e faziam o `atualizar.sh` adiar
  atualização por causa de um backup que não existe.
- **Artefato apagado pela metade.** Apagar remove o arquivo em **todos** os
  destinos onde ele foi parar — local, Azure e rclone — e a linha sai do
  histórico. Se algum destino recusar, a tela diz onde sobrou. "Apaguei"
  precisa significar apagado em todo lugar; caso contrário é lixo no lugar
  mais caro de guardar, e ninguém sabendo.

O rastro de que aquilo existiu fica na **auditoria**, que é onde esse tipo
de registro pertence — não numa linha fantasma no meio das execuções.


Documento de revisão. Cada linha foi verificada no `docker-compose.yml`,
não deduzida.

## Onde cada coisa mora

| Dado | Onde | Tipo |
|---|---|---|
| Banco do painel | volume `postgres_data` | volume nomeado |
| Artefatos de backup | `${DIR_BACKUPS}` → `/data/backups` | bind mount |
| Gravações do InTerminal | `${DIR_SESSOES}` → `/data/sessions` | bind mount |
| Logotipos enviados | `${DIR_MARCA}` → `/data/marca` | bind mount |
| `SECRET_KEY` e configuração de subida | `.env` no host | arquivo |
| Configuração da aba Configurações | banco (tabela `configuracoes`) | volume |
| Credenciais dos servidores | banco, cifradas (Fernet) | volume |

O banco guarda: usuários, servidores com credenciais cifradas, destinos,
agendamentos, histórico de execuções, auditoria, visões de log e
configuração.

## O que sobrevive a cada operação

| Operação | Banco | Backups | Gravações | Logos | `.env` |
|---|:---:|:---:|:---:|:---:|:---:|
| `docker compose restart` | sim | sim | sim | sim | sim |
| `docker compose stop` / `up` | sim | sim | sim | sim | sim |
| `bash deploy.sh` | sim | sim | sim | sim | sim |
| `bash deploy.sh --build` | sim | sim | sim | sim | sim |
| `bash atualizar.sh` | sim | sim | sim | sim | sim |
| Reinício da máquina | sim | sim | sim | sim | sim |
| `docker compose down` | sim | sim | sim | sim | sim |
| **`docker compose down -v`** | **NÃO** | sim | sim | sim | sim |
| Remover a pasta do projeto | não | depende¹ | depende¹ | depende¹ | não |

¹ Sobrevivem se `DIR_BACKUPS`, `DIR_SESSOES` e `DIR_MARCA` apontarem para
fora da pasta do projeto — que é o recomendado.

**`down -v` é o único comando que apaga o banco.** O `-v` remove volumes
nomeados. Está documentado aqui justamente para que ninguém o rode
achando que é só "parar".

## Migração de esquema

Ao subir, o painel executa:

1. `Base.metadata.create_all` — cria tabela que falta
2. `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` — para colunas acrescentadas
   depois da primeira versão
3. `ALTER TABLE ... DROP NOT NULL` — para restrições afrouxadas

Tudo idempotente: rodar duas vezes não muda nada e não levanta erro.

**Por que não Alembic.** Para o punhado de mudanças que houve, ele
cobraria mais uma dependência, mais um passo no deploy e um diretório de
migrações para manter. As três linhas acima resolvem em milissegundos.
Se o esquema começar a mudar com frequência, a conta se inverte e vale
migrar para Alembic — a decisão está registrada aqui para poder ser
revista.

## O ponto único de falha

**A máquina do painel.** Se ela morrer, o volume `postgres_data` vai
junto.

Duas providências, ambas necessárias:

### 1. Backup do painel

**Backups → Backup do painel**, agendado. Salva o banco inteiro para os
mesmos destinos dos outros backups. Detalhes em
[19_BACKUP_DO_PAINEL](19_BACKUP_DO_PAINEL.md).

### 2. Cópia do `.env` fora da máquina

```bash
sudo cp /opt/.faceops/.env /caminho/seguro/faceops.env
```

O `.env` **não vai** no backup do painel, de propósito: a `SECRET_KEY`
que ele contém é o que decifra as credenciais guardadas no dump. Junto
seria guardar a chave dentro do cofre.

Guarde em lugar controlado — cofre de senhas da equipe. Sem ele, o
backup restaura o cadastro e nenhuma credencial funciona.

## Crescimento controlado

Nada aqui cresce sem teto. Verificado item por item:

| O que | Contenção |
|---|---|
| Log dos containers do painel | `max-size: 10m/20m`, `max-file: 2/3` no compose |
| Artefatos de backup | retenção por destino, aplicada ao fim de cada execução |
| Gravações do InTerminal | faxina diária, retenção em dias |
| Staging de backup | faxina remove órfão acima de 24h |
| `audit_logs` | faxina, retenção em dias (crítico fica o triplo) |
| `terminal_sessions` | faxina, junto com a auditoria |
| `backup_runs.log` | faxina esvazia o texto e mantém a linha |
| `backup_runs` (a linha) | faxina, `faxina.execucoes_dias` (padrão 730) — **só a execução cujo artefato já não existe**. O texto saía em 60 dias e a linha ficava para sempre; agora ela também tem prazo, mas nunca à frente do arquivo que descreve: apagar a linha e deixar o `.tar.gz` no disco produziria um artefato que ninguém sabe de onde veio |
| `amostras` (monitor) | faxina, `monitor.retencao_dias` (padrão 30) |
| `incidentes` | faxina, `incidentes.retencao_dias` (padrão 30) — **só os fechados**; um aberto é estado atual, não histórico |
| `crescimentos` | faxina, `crescimento.retencao_dias` (padrão 90) — **só as encerradas**, pela mesma razão do incidente aberto. Prazo maior que o dos incidentes: "este disco já encheu antes?" é pergunta de mês |
| `amostras_container` | faxina, `containers.retencao_dias` (padrão 7). Muitas linhas por hora e vida curta de propósito: ela responde "quem está com a memória agora", não o histórico de capacidade |
| `log_padroes` | faxina, `analise.retencao_dias` (padrão 30). Guarda molde com contador, não a linha: mil ocorrências do mesmo erro são UMA linha |
| `notificacao_envios` | faxina, `notificacao.retencao_dias` (padrão 14) — log operacional, some inclusive as falhas |
| `licenca_amostras` | faxina, `faxina.licenca_dias` (padrão 365) |
| `hosts.servicos_conhecidos` | lista sobrescrita, não acumulada; só grava quando muda |
| Streams de log ao vivo | não gravam arquivo; sessão ociosa cai em 30 min |
| Sessões de terminal | caem por inatividade em 30 min |
| Conexões SSH | pool com TTL de 120 s |

A faxina roda **uma vez por dia**, no horário configurado. Prazos em
Configurações → Faxina automática. Detalhes em
[14_MANUTENCAO](14_MANUTENCAO.md).

### A prévia mostra tudo, e há teste para isso

A tela "Faxina do painel" lista **uma linha por categoria**, e a lista vem
do backend. Ela já esteve errada de um jeito perigoso: mostrava quatro
categorias enquanto a faxina apagava onze. Quem abria, via zero e
concluía que nada seria removido — no mesmo dia em que milhares de
amostras iam embora. Painel que apresenta um subconjunto como se fosse o
todo é a mesma falha de "serviço travado" e "câmera sem evento", em outro
lugar.

A trava é o cenário `previa da faxina nao esconde categoria`: ele lê os
contadores que `executar()` declara e exige uma linha correspondente na
prévia. Retenção nova sem linha na tela **quebra o teste**, nomeando o
contador esquecido.

Uma linha é declaradamente aproximada: a das execuções de backup mostra
"até N", porque quem decide se aquela linha sai é a existência do arquivo
no disco, não a consulta. Dizer "até" é o que a prévia sabe de fato.

## Volumes fantasma

Não há. Verificação:

```bash
docker volume ls | grep faceops
```

Deve mostrar **um** volume: `<pasta>_postgres_data`. Todo o resto é bind
mount para caminho visível no host.

Se aparecer volume anônimo (nome com hash), foi container removido de
forma incompleta:

```bash
docker volume prune    # remove só volume que nenhum container usa
```

## Antes de mexer no `.env`

Trocar a `SECRET_KEY` **torna ilegíveis todas as credenciais guardadas**.
O painel detecta e devolve mensagem explícita — não falha em silêncio nem
tenta conectar com lixo:

```
Segredo ilegível: a SECRET_KEY mudou desde que foi gravado.
Recadastre a credencial do host.
```

Se acontecer por engano: reponha a chave antiga no `.env` e reinicie. Se
a chave antiga se perdeu, é preciso recadastrar a credencial de cada
servidor e cada destino. Os dados de histórico e auditoria não são
afetados.

## Checklist de revisão

Para conferir num painel já instalado:

```
[ ] docker volume ls mostra só o postgres_data do projeto
[ ] DIR_BACKUPS aponta para disco com folga, fora da pasta do projeto
[ ] Cópia do .env guardada fora da máquina
[ ] Agendamento de backup do painel criado e já rodou com sucesso
[ ] Faxina automática com horário fora da janela dos backups
[ ] Configurações → Faxina com prazos coerentes com a exigência do contrato
[ ] Retenção de cada destino conferida em Destinos
```
