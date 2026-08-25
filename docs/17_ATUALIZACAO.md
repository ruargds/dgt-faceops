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

O campo `revisao` traz o commit curto do git. Também aparece no
`atualizar.sh` ao final.

Isso existe porque "qual versão está rodando?" respondido por memória é
a origem de meia hora de confusão em qualquer incidente.

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

## Nos servidores do FindFace

**Nada muda neles.** O painel é agentless: o script de backup vai pela
entrada padrão do `bash` remoto a cada execução, então a versão nova já
vale na próxima rodada, sem sincronizar arquivo nenhum.

Nenhum servidor do FindFace tem repositório git, credencial do GitHub ou
arquivo do painel. É proposital.
