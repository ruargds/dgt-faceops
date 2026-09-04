# Backup do próprio painel

O painel protege quatro servidores. Até certo ponto do projeto, **nada
protegia o painel** — uma lacuna encontrada em revisão, não em incidente,
que é a hora certa de encontrar.

## O que se perderia

Se a máquina do painel morresse:

| Perda | Custo de refazer |
|---|---|
| Cadastro dos servidores | Recadastrar quatro máquinas |
| **Credenciais cifradas** | Recolher chave PEM e senha de sudo de novo |
| Destinos de backup | Reconfigurar Azure, rclone, caminhos |
| Todos os agendamentos | Reprogramar a recorrência |
| Histórico de execuções | **Perde-se a prova de que os backups rodaram** |
| Auditoria inteira | Perde-se a trilha de quem fez o quê |
| Configuração e visões de log | Refazer ajustes |

O histórico e a auditoria são os piores: não têm como ser refeitos.

**E são alguns MB.** O custo de salvar é irrisório perto de qualquer
item dessa tabela.

## Como fazer

**Backups → Backup do painel.** Só isso.

Vai para os mesmos destinos configurados, na pasta `_painel`, com
retenção própria. Aparece no histórico como perfil `painel`.

Enquanto não houver nenhum backup do painel bem-sucedido, a tela de
Backups exibe um aviso âmbar no topo.

## O que vai no artefato

```
faceops_painel_<data>.tar.gz
├── MANIFESTO.txt      inventário e procedimento de restauração
├── faceops.dump       banco do painel (pg_dump -Fc)
└── marca/             logotipos enviados pela tela
```

O `faceops.dump` contém tudo: usuários, servidores com as credenciais
**cifradas**, destinos, agendamentos, histórico, auditoria, configuração
e visões de log.

## O que NÃO vai — e por quê

**A `SECRET_KEY` não entra no artefato.** É deliberado.

Ela é o que decifra as credenciais guardadas nesse mesmo dump. Colocá-la
junto seria guardar a chave dentro do cofre trancado: qualquer um com
acesso ao arquivo de backup teria as chaves SSH dos quatro servidores em
claro.

**Consequência prática:** para restaurar você precisa de **duas coisas**,
guardadas separadas:

1. O artefato do backup
2. O arquivo `.env` do painel, com a `SECRET_KEY` original

Guarde o `.env` fora da máquina do painel, em lugar controlado — cofre de
senhas da equipe, por exemplo. Sem ele, o backup restaura o cadastro mas
nenhuma credencial funciona, e você recebe `Segredo ilegível: a
SECRET_KEY mudou`.

## Como restaurar

O procedimento completo viaja dentro do artefato, no `MANIFESTO.txt`.
Resumo:

```bash
# 1. Painel instalado na máquina nova
bash instalar.sh

# 2. Parar o backend (o banco não pode estar em uso)
sudo docker compose stop backend

# 3. Restaurar o banco
sudo docker compose exec -T postgres psql -U faceops -d postgres \
    -c "DROP DATABASE IF EXISTS faceops"
sudo docker compose exec -T postgres createdb -U faceops faceops
sudo docker compose exec -T postgres pg_restore -U faceops -d faceops \
    --no-owner < faceops.dump

# 4. Colocar a SECRET_KEY original no .env
#    (é o passo que faz as credenciais voltarem a funcionar)

# 5. Restaurar os logotipos
cp -r marca/* data/marca/

# 6. Subir
sudo docker compose up -d
```

**Conferência que prova que deu certo:** Servidores → **Testar conexão**
em cada um. Tem que vir verde. Se der `Segredo ilegível`, a `SECRET_KEY`
não é a mesma — volte ao passo 4.

## Agendar

Como qualquer outro backup, em **Agendamentos**. Sugestão:

| Nome | Perfil | Cron | Retenção |
|---|---|---|---|
| Painel — diário | `painel` | `0 1 * * *` | 90 dias |

Às 01:00, antes dos backups dos servidores (02:00 e 02:15). Assim o
estado salvo do painel reflete a configuração com que a noite vai rodar.

## O que isso não substitui

**Não substitui guardar o `.env`.** Repetindo porque é o erro que
inutiliza tudo: o backup sem a `SECRET_KEY` não devolve acesso aos
servidores.

**Não substitui backup do Face Detect.** São coisas diferentes: este salva o
painel, aquele salva o reconhecimento facial. Perder um não afeta o
outro.
