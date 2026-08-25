# Persistência — o que sobrevive a quê

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
| Streams de log ao vivo | não gravam arquivo; sessão ociosa cai em 30 min |
| Sessões de terminal | caem por inatividade em 30 min |
| Conexões SSH | pool com TTL de 120 s |

A faxina roda **uma vez por dia**, no horário configurado. Prazos em
Configurações → Faxina automática. Detalhes em
[14_MANUTENCAO](14_MANUTENCAO.md).

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
