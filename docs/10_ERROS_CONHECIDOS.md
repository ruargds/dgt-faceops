# Erros conhecidos

Sintoma → causa → solução. **Registre aqui o que custou tempo para achar** —
é o documento que mais paga o próprio custo.

## Conexão

### `host 'X' sem chave de identidade fixada`

O host foi cadastrado sem a leitura da chave. Não deveria acontecer pelo
fluxo normal (a criação faz a leitura), mas acontece com registro inserido à
mão no banco.

**Solução:** Servidores → Editar → **Ler chave do servidor** → Salvar.

### `a chave de 'X' NAO confere com a cadastrada`

Duas causas, e a diferença importa:

1. **Servidor reinstalado** — chave nova, legítima. Editar o host, ler a
   chave de novo, salvar.
2. **Alguém no caminho da rede** — investigue antes de refazer.

A conexão foi abortada **antes** de enviar credencial. Nada vazou.

**Como distinguir:** confira a chave direto no servidor, por um caminho que
você confia (console do Azure, por exemplo):

```bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Se bate com o fingerprint que o painel leu agora, foi reinstalação.

### `autenticacao recusada em 'X' para o usuario 'Y'`

- Chave PEM errada ou não publicada no `authorized_keys` do servidor
- Usuário errado (`azureuser` vs `ubuntu` vs `dgt`)
- Permissão do `authorized_keys` no servidor (precisa ser 600, e o `~/.ssh`
  700)

```bash
# No servidor
ls -la ~/.ssh/
sudo tail -30 /var/log/auth.log
```

O `auth.log` diz exatamente o motivo da recusa.

### `chave PEM de 'X' invalida ou com senha errada`

- A chave tem passphrase e o campo ficou vazio
- Colou a chave **pública** (`.pub`) em vez da privada
- Chave em formato PPK (PuTTY) — converta com `puttygen` para OpenSSH
- Copiou sem a linha `-----BEGIN`/`-----END`, ou com espaço no início

### `nao consegui alcancar X:22`

Rede. Do painel:

```bash
timeout 5 bash -c "echo > /dev/tcp/10.0.1.10/22" && echo OK || echo FALHOU
```

Falhou → NSG do Azure, VPN, ou rota. Ver [04_INSTALACAO](04_INSTALACAO.md).

Se o painel roda em Docker, teste **de dentro do container** — a rede dele
não é a do host:

```bash
docker compose exec backend sh -c 'timeout 5 nc -z 10.0.1.10 22 && echo OK'
```

### `Segredo ilegível: a SECRET_KEY mudou`

Exatamente o que diz. A `SECRET_KEY` no `.env` não é a que cifrou aquele
segredo.

**Se você tem a chave antiga:** volte-a ao `.env` e reinicie.

**Se não tem:** as credenciais guardadas estão perdidas. Recadastre cada
host (Editar → colar a chave PEM de novo). Os dados de histórico e auditoria
não são afetados.

**Prevenção:** guarde uma cópia do `.env` fora da máquina.

### Clicar em "Abrir terminal" não fazia nada

Sintoma: o InTerminal aceitava o login, a janela fechava e **nada**
acontecia — sem erro na tela, sem sessão, sem registro em auditoria.

Causa: o terminal (xterm.js) era criado num efeito de montagem com lista de
dependências vazia. Esse efeito rodava no primeiro render, quando a tela
ainda mostrava "carregando servidores" e a `div` do terminal não existia no
DOM. Ele saía pelo guard, nunca mais rodava, e a referência do terminal
ficava nula para sempre — o botão chamava a função de conectar, que
retornava na primeira linha, em silêncio.

Corrigido: a `div` entra por *ref de callback* e o efeito depende do nó, de
modo que o terminal nasce quando o elemento aparece de verdade na tela.

Se voltar a acontecer depois de um deploy, é cache do navegador com o
bundle antigo — confira o selo de versão no rodapé da barra lateral e dê
`Ctrl+F5`.

### `/opt/findface-multi nao existe. Confira o caminho cadastrado`

Sintoma: o backup falha em 0s, logo na primeira linha.

Causa: o **Diretório de instalação** cadastrado para aquele servidor não
existe lá. Acontece em instalação distribuída — o servidor escolhido pode
não ser o que hospeda a aplicação — e em instalação fora do caminho padrão.

O que o painel faz agora, nesta ordem:

1. **Antes de disparar**, se o caminho cadastrado não existe, ele detecta a
   instalação pelo diretório de trabalho do compose de um container em
   execução (a resposta autoritativa: é de onde a instalação subiu),
   corrige o cadastro e segue. A correção fica registrada no log da
   execução.
2. **Se não achar**, a mensagem diz o que encontrou: ou o caminho real
   (`encontrei a instalação em X`), ou que **nenhum container do FindFace
   roda ali** — caso em que a aplicação está em outra máquina, e a
   Topologia mostra qual.

Se ainda assim aparecer, o servidor provavelmente não hospeda o FindFace:
confira em Topologia e dispare o backup no servidor certo.

### `nenhum destino aceitou o artefato` logo após copiar

Se a mensagem for `'int' object has no attribute 'tipo'`, é a versão
anterior a 15deb11: o serviço mandava os IDs dos destinos onde o
armazenamento esperava o objeto, e **toda execução com destino morria**
depois de já ter copiado o artefato do servidor de produção. Corrigido —
atualize o painel.

## Serviços

### `nem 'docker compose' nem 'docker-compose' encontrados`

O usuário SSH não tem acesso ao Docker, ou o FindFace não está nesse
servidor.

```bash
# No servidor
docker ps                    # funciona sem sudo?
groups                       # o usuário está no grupo 'docker'?
sudo usermod -aG docker $USER   # e RELOGAR
```

### `container 'X' pertence ao projeto 'Y', não ao projeto do FindFace`

A cerca de segurança funcionando. O painel só age em containers do projeto
compose do FindFace naquele host.

Se o projeto foi renomeado de propósito, o painel descobre sozinho pelo
rótulo — mas confira se o `compose_file` cadastrado aponta para o arquivo
certo.

### Serviços não aparecem, mesmo com o FindFace rodando

O `compose_file` cadastrado não corresponde ao que subiu os containers. O
painel identifica o projeto pelo rótulo
`com.docker.compose.project.config_files`, que guarda o caminho exato.

```bash
# No servidor — qual caminho os containers registram?
docker inspect $(docker ps -q | head -1) \
  --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}'
```

Ajuste o campo no cadastro para bater exatamente (`.yaml` vs `.yml` conta).

### `findface-video-worker` com contagem de reinícios subindo

Não é container quebrado. Costuma ser:

- **Câmera problemática** — stream instável faz o worker morrer e voltar.
  Veja o log dele para achar qual.
- **Memória de GPU esgotada** — confira `oom_killed` na tela de Serviços e a
  VRAM em Recursos.
- **Rede até a câmera** — perda de pacote no RTSP.

Reiniciar resolve o sintoma. A causa está no log.

## Backup

### `script de backup falhou` sem detalhe claro

Abra o detalhe da execução (ícone de log). O log completo do script está lá,
com a linha exata que falhou.

### `espaço insuficiente em /var/backups/faceops`

O perfil `completo` exige 60% do tamanho bruto de `data/` livre no servidor.

Opções:

1. Montar disco maior e apontar `REMOTE_STAGING_DIR` para ele
2. Usar o perfil `essencial` (não precisa de espaço para `data/`)
3. Limpar eventos antigos no FindFace antes (a plataforma tem essa opção)

### `AVISO: container do PostgreSQL não encontrado`

O serviço `postgresql` não está no projeto compose daquele host — normal em
topologia onde o banco fica no `vm-dbserver`.

O backup **não falha**, só registra `postgres: ausente`. Faça o perfil
`essencial` no servidor onde o PostgreSQL realmente roda.

### `tarantool_metodo = copia-direta` no log

O `box.snapshot()` não pôde ser disparado. Os arquivos foram copiados de
qualquer forma, e o Tarantool reaplica os xlogs no restore — mas a
consistência não fica garantida.

**Investigar:**

```bash
# No servidor
docker exec -it <container-tarantool> ls -la /var/run/tarantool/
docker exec -it <container-tarantool> which tarantoolctl
```

O script tenta o socket `.control` e, em seguida, `tarantool` via stdin. Se
a sua instalação usa outro caminho, ajuste
`backup_tarantool()` em `scripts/ffmulti-backup.sh` — e registre aqui o que
funcionou.

### `checksum nao confere apos a transferencia`

Transferência corrompida. Não é bug do backup: o artefato no servidor estava
bom (o checksum foi calculado lá).

Rode de novo. Se repetir, suspeite de rede instável ou disco com problema no
painel:

```bash
dmesg | grep -i "i/o error"
```

### `nenhum destino aceitou o artefato`

Todos os destinos falharam. A mensagem lista o erro de cada um.

- **azure:** `AZURE_STORAGE_CONNECTION_STRING` vazia ou vencida
- **gdrive:** `rclone.conf` ausente ou remote com nome diferente de
  `RCLONE_REMOTE`
- **local:** disco cheio ou permissão em `data/backups`

### Backup fica em `executando` para sempre

Não deveria — há bloco de segurança que marca `falha`. Se acontecer, o
processo do backend caiu no meio.

```bash
docker compose logs --tail 100 backend
```

Para destravar o registro:

```sql
UPDATE backup_runs SET status='falha', stage='Interrompido',
       error='painel reiniciado durante a execução'
WHERE status='executando';
```

### `ja existe um backup em andamento em 'X'`

Trava por host, intencional: dois backups concorrentes no mesmo servidor
competem por disco e podem corromper o staging. Espere o primeiro terminar.

## Agendamento

### Agendamento não roda na hora marcada

Verifique, nesta ordem:

1. **Está ativo?** Coluna "Próxima" mostra `pausado` se não.
2. **Perfil `completo` sem aceite?** `last_status` mostra
   `bloqueado: perfil completo sem aceite de janela`.
3. **Relógio da máquina do painel.** `timedatectl` — cron desalinhado por
   relógio errado falha em silêncio.
4. **O painel estava no ar?** Se ficou fora, o `misfire_grace_time` de 1h
   recupera; passado disso, a execução é perdida.
5. **Mais de um worker?** Duplicaria a execução em vez de perder. Confira
   o `CMD` do Dockerfile.

### `cron inválido` no `last_status`

A expressão foi aceita no cadastro mas rejeitada na remontagem. Confira os 5
campos. O painel valida na criação — registro inserido à mão no banco escapa
disso.

## Terminal

### Terminal conecta e morre em ~60 segundos

Proxy sem configuração de WebSocket. Se houver **outro** proxy na frente do
nginx do painel (Cloudflare Tunnel, load balancer, nginx externo), ele
precisa de:

```nginx
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
proxy_read_timeout 3600s;
```

### `Ticket inválido ou expirado`

O ticket vale 30 segundos e serve uma vez. Causas:

- Demora entre clicar e conectar (aba em segundo plano, máquina lenta)
- Recarregar a página no meio da abertura
- Relógio do servidor desalinhado

Clique em "Abrir terminal" de novo.

### Terminal abre mas não aparece nada

CSP bloqueando o WebSocket. O console do navegador (F12) mostra erro de CSP.
Confira que `connect-src` inclui `ws:` e `wss:` em `frontend/nginx.conf`.

## Painel

### `Sem resposta do servidor. O painel está no ar?`

```bash
docker compose ps
docker compose logs --tail 60 backend
curl http://localhost:8080/api/saude
```

### Painel sobe mas login falha com admin/admin123

O admin só é criado se o banco estiver **vazio**. Se houver qualquer
usuário, o seed não roda.

```bash
docker compose exec postgres psql -U faceops -d faceops \
  -c "SELECT username, role, is_active FROM users;"
```

Para redefinir a senha de um usuário:

```bash
docker compose exec backend python -c "
from app.core.security import hash_password
print(hash_password('nova-senha'))
"
# copie o hash e:
docker compose exec postgres psql -U faceops -d faceops \
  -c "UPDATE users SET hashed_password='<hash>', senha_padrao=false WHERE username='admin';"
```

### `$'\r': command not found` no log do backend

Script `.sh` com fim de linha CRLF. Aconteceu porque o projeto veio de ZIP
ou o Git converteu.

```bash
# no repositório
find scripts -name "*.sh" -exec sed -i 's/\r$//' {} \;
bash deploy.sh --build
```

O `.gitattributes` previne em clone; o instalador Windows corrige
automaticamente.

### `container name already in use`

Deploy anterior morreu no meio.

```bash
docker rm -f faceops_backend faceops_frontend faceops_postgres
bash deploy.sh
```

### Build do frontend falha com "Treating warnings as errors"

`CI=true` no ambiente. O Dockerfile já usa `CI=false npm run build`. Se
estiver buildando à mão:

```bash
CI=false npm run build
```

## Casos encontrados em campo

Registro do que já apareceu num servidor real. Vale mais que o resto do
documento, porque aconteceu de verdade.

### `/var/log` com 99 GB num disco de 123 GB

**Sintoma:** disco raiz a 100%, 1,1 GB livres, FindFace ainda de pé.

**Investigação:**

```
/var        120G
/var/log     99G   ← aqui
syslog       64G   ← ativo
syslog.1     33G   ← rotacionado, sem compressão
```

**Causa:** o log de acesso HTTP do próprio FindFace indo para o syslog.
Cada face detectada gera 2+ linhas (`POST /events/faces/add/ 200`), mais o
polling da UI (`GET /users/me/ 200`). Deu **~8 GB/dia** em operação normal.

**O que enganou:** parecia loop de erro. Não era — todas as respostas eram
`200`. O sistema estava funcionando; era telemetria sem limite. A rotação
padrão do Ubuntu é semanal, o que não segura 8 GB/dia.

**Correção:** `scripts/endurecer_servidor_ff.sh` — rotação por tamanho
(`maxsize 500M`), teto no journald, e filtro no rsyslog para o log de
container não cair no `/var/log/syslog`.

**Para liberar sem perder nada:** mover os rotacionados para um disco com
folga, copiar o ativo e depois `truncate -s 0`. Nunca `rm` no arquivo
ativo — o rsyslog o mantém aberto, e apagar libera o nome mas não o
espaço; você fica sem o log E sem o disco.

### `/opt/findface-multi` não existe

**Sintoma:** backup falha procurando `configs/`; o script avisa que o
diretório não existe.

**Causa:** o caminho de instalação não é o padrão da documentação. Em
instalação distribuída, muda.

**Correção:** o "Testar conexão" detecta sozinho, lendo os rótulos que o
compose grava em cada container (`project.working_dir` e
`project.config_files`), e corrige o cadastro. Para conferir na mão:

```bash
sudo docker inspect $(sudo docker ps -q | head -1) --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
```

### Tela de Serviços vazia, sem erro

**Causa:** o usuário SSH não está no grupo `docker` — que é o padrão da
instalação do FindFace. `docker ps` falha com permissão negada e a leitura
volta vazia.

**Correção:** o painel detecta e usa sudo por host
(`SSHService.docker_needs_sudo`). Se preferir resolver na origem:
`sudo usermod -aG docker <usuario>` e relogar.

### "6 serviços com problema" que nunca somem

**Causa:** os containers `*-migrate` são jobs de execução única — rodam na
subida, migram o banco e saem com código 0. Contá-los como "não está
running" gerava alarme falso permanente.

**Correção:** job é classificado à parte e só conta como problema se sair
com código diferente de 0. Aparece como "Concluído" na tela.

### Instalação distribuída, não single-node

**Sintoma:** o perfil `essencial` gera artefato menor que o esperado.

**Causa:** os componentes estão espalhados entre as VMs. A presença de
`findface-extraction-api-**lb**` (balanceador) indica que a extração roda
em outra máquina. O Tarantool — que guarda os vetores faciais — pode estar
num servidor diferente do PostgreSQL.

**Correção:** rode `scripts/inventario.sh` em cada VM e agende o perfil
`essencial` onde cada componente realmente está. Um agendamento por
servidor com dado, não um só.

## Método quando nada aqui serve

1. `docker compose logs --tail 200 backend` — o erro está lá, quase sempre
2. **Testar conexão** no host afetado — separa problema de rede/credencial
   de problema de lógica
3. Rode o comando na mão pelo **InTerminal**, no servidor — isola se o
   problema é do painel ou do servidor
4. Confira a **Auditoria** — mostra o que foi tentado, por quem, e o erro
5. `GET /api/saude` — o agendador subiu? quantos jobs?

E depois **escreva aqui** o que era.
