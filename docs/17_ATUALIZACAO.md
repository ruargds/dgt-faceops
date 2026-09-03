# Atualização

Como buscar versão nova nos servidores sem parar, travar ou pesar nada
que já esteja em operação.

## O comando

```bash
cd ~/dgt-faceops
bash atualizar.sh
```

| Variação | Para quê |
|---|---|
| `bash atualizar.sh --verificar` | Só diz se há versão nova. **Não altera nada.** |
| `bash atualizar.sh --sem-build` | Só código Python. Rápido, sem reconstruir imagem. |
| `bash atualizar.sh --forcar` | Atualiza mesmo com trabalho em curso ou carga alta. |

## As três garantias

### 1. O FindFace não é tocado

O painel vive no seu próprio projeto compose. O `docker compose up -d` do
`atualizar.sh` age **apenas** nos containers `faceops_*`. Os containers do
reconhecimento facial não são vistos, nem parados, nem reiniciados.

Isso vale mesmo quando o painel roda **na mesma máquina** que a aplicação
facial: são projetos compose diferentes, e o comando é escopado ao do
painel.

### 2. Trabalho em curso não é interrompido

Antes de qualquer coisa, o script consulta `/api/saude` e lê três números:

```json
{ "backups_executando": 0, "terminais_ativos": 0, "logs_ativos": 0,
  "ocupado": false }
```

Se qualquer um for maior que zero, **a atualização é adiada** com código
de saída `2`.

O motivo é concreto: reiniciar o painel no meio de um backup mata a
execução **depois** de ela já ter rodado o dump no servidor e copiado
dezenas de GB. Você perde o trabalho e o tempo, e o disco de staging fica
sujo.

Terminal aberto e log sendo acompanhado também bloqueiam — menos grave,
mas ninguém gosta de ter a sessão cortada no meio de um diagnóstico.

`--forcar` passa por cima, e o script avisa explicitamente quantos
backups serão interrompidos antes de seguir.

### 3. O build não compete por CPU

Duas proteções:

**Guarda de carga.** O script mede `loadavg` por núcleo e **recusa
construir** se estiver acima de `0.80`, com código de saída `3`. Numa
máquina que também roda 80 workers de detecção facial, isso importa.

**Prioridade mínima.** O build roda com `nice -n 19` e `ionice -c3`.

> Honestidade sobre o `nice`: ele vale para o cliente do Docker e para o
> envio do contexto. O trabalho pesado acontece dentro do `dockerd`, que
> não herda a prioridade. **A proteção que realmente conta é a guarda de
> carga** — por isso ela existe.

Se a máquina estiver ocupada e a mudança for só de código Python,
`--sem-build` resolve em segundos, sem construir nada.

## Reversão automática

Se o painel não responder em 80 segundos depois da subida, o script:

1. Volta o código para a revisão anterior (`git reset --hard`)
2. Reconstrói
3. Sobe de novo
4. Confirma que respondeu

Você fica com o painel antigo funcionando e a mensagem de erro da versão
nova no log — em vez de um painel fora do ar.

Se nem a reversão subir, o script para e diz exatamente o que rodar. Não
fica tentando às cegas.

## O que sobrevive à atualização

| Item | Preservado |
|---|---|
| `.env` (e a `SECRET_KEY`) | sim — cópia de segurança automática antes |
| Banco do painel | sim — volume Docker |
| Servidores e credenciais no cofre | sim |
| Destinos de backup | sim |
| Artefatos de backup | sim |
| Gravações de terminal | sim |
| Agendamentos | sim — a tabela é a fonte de verdade, e o agendador é remontado a partir dela na subida |
| Configurações da aba Configurações | sim |

Migrações de banco rodam sozinhas na subida.

## Saber o que está no ar

```bash
curl -s http://localhost:8080/api/saude
```

O campo `revisao` traz o commit curto do git. Aparece em três lugares:
no fim do `atualizar.sh`, neste `/api/saude`, e **no rodapé da barra
lateral do painel**, embaixo do usuário — que é onde alguém com a tela
aberta consegue olhar sem pedir acesso à VM.

O `deploy.sh` e o `atualizar.sh` carimbam a mesma revisão no bundle do
frontend (`BUILD_STAMP`). Quando o navegador está com `index.html` em
cache apontando para um bundle antigo, o rodapé mostra um aviso âmbar com
o selo do bundle e o pedido de `Ctrl+F5` — antes disso, esse caso custava
rodadas de "corrigi / não resolveu".

Isso existe porque "qual versão está rodando?" respondido por memória é
a origem de meia hora de confusão em qualquer incidente.

## Quando o servidor não alcança o GitHub

O `atualizar.sh` **para** com saída 4 e explica, em vez de seguir. A razão é
uma armadilha real: com o remoto inalcançável, comparar `HEAD` com `HEAD` dá
"já está na versão mais recente", o script reconstrói o mesmo código e o
operador vê um build que "falhou de novo" — quando a correção nunca chegou
na máquina.

O git também roda com `GIT_TERMINAL_PROMPT=0`: sem isso ele **abre prompt**
pedindo usuário e senha e trava o script no meio de uma janela de
manutenção.

Como resolver, na ordem em que costuma ser o problema:

```bash
# credencial expirada — token de acesso pessoal:
git remote set-url origin https://TOKEN@github.com/ruargds/dgt-faceops.git

# ou chave SSH de deploy (não expira):
git remote set-url origin git@github.com:ruargds/dgt-faceops.git

# sem saída para a internet: leve o pacote gerado por empacotar.sh
```

Para reconstruir de propósito o código que já está na máquina — depois de
uma edição local, ou para refazer a imagem — existe a opção explícita:

```bash
bash atualizar.sh --sem-git
```

## Códigos de saída

| Código | Significado |
|---|---|
| `0` | Atualizado, ou já estava na versão mais recente |
| `2` | Adiado: há trabalho em curso |
| `3` | Adiado: carga da máquina alta |
| `4` | `git pull` falhou (alteração local não commitada?) |
| `5` | Build falhou — o painel antigo continua no ar, nada foi trocado |
| `6` | A versão nova não subiu; **revertida** com sucesso |
| `7` | Nem a reversão subiu — intervenção manual |

Úteis para automatizar: um cron que roda `--verificar` e avisa quando há
versão nova respeita o código `0`.

## Atualização periódica, se quiser

O jeito seguro é **verificar** de forma automática e **aplicar** à mão:

```bash
# crontab -e — avisa, não aplica
0 9 * * 1 cd /home/dgt/dgt-faceops && bash atualizar.sh --verificar
```

Aplicar sozinho de madrugada é tentador e errado: é exatamente o horário
em que os backups rodam, e o script (corretamente) recusaria.

Se quiser automatizar de verdade, escolha uma janela sem agendamento —
por exemplo domingo às 10h, com os backups às 02:00.

## O que fica no servidor, e o que é lixo

O diretório da aplicação chegou a ter **cinco cópias do `.env`** em duas
horas. Cada uma carrega a `SECRET_KEY` e a senha do banco — não era só
lixo, era o segredo espalhado em cinco lugares.

**A causa era este script.** Ele grava `FACEOPS_REVISAO` no `.env` a cada
execução, então o arquivo sempre diferia da última cópia e toda
atualização gerava um backup novo. A cópia existe para proteger o que o
**operador** configurou; se só mudou o que o script escreve sozinho, não
há o que proteger.

Hoje a comparação ignora essa linha, ficam as **3 mais recentes**, e todas
com permissão `600`.

### Sobra do Docker

Cada atualização deixa a imagem anterior **sem tag**. Numa VM pequena isso
enche o disco em poucas semanas.

Ao fim de uma atualização bem-sucedida o script poda:

| O que | Filtro |
|---|---|
| imagens penduradas | `docker image prune -f` — **nunca `-a`**, que levaria imagem de container parado |
| cache de build | só o que tem mais de 7 dias; o recente acelera a próxima atualização e vale o espaço |

E poda **só depois** de a versão nova responder ao teste de saúde. Podar
antes de saber que deu certo é apagar a rede enquanto se atravessa o rio.
O espaço liberado aparece no fim da execução.

### O que NÃO é lixo

| Diretório | Para quê |
|---|---|
| `data/backups`, `data/sessions`, `data/marca` | volumes de execução — cada um com retenção própria, aplicada pela faxina diária |
| `tls/` | certificado, gerado na instalação |
| `.git` | é como este script sabe o que mudou e para onde voltar |
| `docs/`, `scripts/`, `specs/` | ~500 KB somados, e `scripts/` é usado pela própria atualização |

Nada aqui justifica poda: o que ocupa disco de verdade são artefato de
backup e imagem do Docker, e os dois têm prazo.

**Trava:** `servidor nao acumula sobra`.


## Nos servidores do FindFace

**Nada muda neles.** O painel é agentless: o script de backup vai pela
entrada padrão do `bash` remoto a cada execução, então a versão nova já
vale na próxima rodada, sem sincronizar arquivo nenhum.

Nenhum servidor do FindFace tem repositório git, credencial do GitHub ou
arquivo do painel. É proposital.
