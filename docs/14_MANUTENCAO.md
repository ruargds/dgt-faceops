# Manutenção de disco e log

O problema mais comum num servidor de reconhecimento facial não é o
FindFace: é o disco raiz enchendo de log.

Num servidor real encontramos **99 GB de `/var/log` num disco de 123 GB**,
gerados pelo log de acesso HTTP do próprio FindFace, em operação
**normal** — cerca de 8 GB por dia. Todas as respostas eram `200`. Não
era erro; era telemetria sem limite.

Outro, na mesma instalação, estava com **0 bytes livres** havia 17 dias.
O sintoma que denunciou foi um `xauth: unable to write authority file` no
login — a máquina não conseguia mais escrever nada.

A tela de **Manutenção** resolve isso por servidor, pela web, sem linha
de comando.

## 1. Diagnóstico

Botão **Diagnosticar**. Leva cerca de 20 segundos e **não altera nada**.

| O que mostra | Por que importa |
|---|---|
| **Crescimento do log** em MB/dia | O único número que diz se vale conter — e depois, se a contenção funcionou |
| **`/var/log` ocupa** | Quanto já está lá, e quanto disso é arquivo rotacionado (recuperável) |
| **Contenção aplicada** | Se este servidor já foi tratado |
| **Discos** | Ocupação de cada montagem |
| **Amostra** | As últimas linhas do syslog — mostra *quem* está inundando |

A medição de crescimento amostra o tamanho do syslog duas vezes, com 15
segundos de intervalo, e extrapola para 24h. Rode **antes e depois** da
contenção: é a prova.

## 2. Conter o crescimento

O filtro age **na chegada ao rsyslog**, não na aplicação. Consequência
importante: **nada do FindFace reinicia.** O FindFace continua logando em
INFO; o painel só evita gravar o ruído no disco raiz.

Três arquivos são escritos:

| Arquivo | O que faz |
|---|---|
| `/etc/rsyslog.d/30-faceops-docker.conf` | Descarta requisição HTTP bem-sucedida vinda de container |
| `/etc/systemd/journald.conf.d/faceops-limite.conf` | Teto de 2 GB no journal |
| `/etc/logrotate.d/faceops-syslog` | Rotaciona ao passar de 500 MB, não só por data |

O filtro é **seletivo**. Descarta apenas isto:

```
status=(200|204|206|304)
"POST /… HTTP/1.1" 200 …
HTTP RESP GET /users/me/ 200 [
```

Erro, aviso e qualquer status fora de 2xx/3xx **continuam sendo
gravados**. E o log completo segue acessível por `docker logs` e
`journalctl` — o filtro só decide o que vai para o disco raiz.

### O que reinicia

Apenas `rsyslog` e `systemd-journald`. Ambos são instantâneos e não tocam
em container nenhum. O reconhecimento facial não é interrompido.

### A validação que evita o desastre

Antes de reiniciar, o painel roda `rsyslogd -N1`. Se a configuração
estiver inválida, **o filtro é removido e nada reinicia** — o servidor
fica exatamente como estava.

Isso não é zelo excessivo: um rsyslog com configuração quebrada não sobe,
e aí o servidor para de gravar log inteiro. Você trocaria "disco enchendo"
por "cego".

### Sempre veja antes

O botão **Ver o que será alterado** mostra o conteúdo exato dos três
arquivos, sem escrever nada. Só depois disso o botão de aplicar faz
sentido.

Aplicar exige a permissão `maintenance.apply` e confirmação digitando o
nome do servidor.

### Se quiser cortar mais

Dentro do arquivo do rsyslog há uma linha comentada que descarta **todo**
`level=info`. Reduz mais, mas perde o rastro de operação normal no syslog.
Eu deixaria como está primeiro e mediria — o filtro seletivo costuma
resolver.

## 3. Arquivar log antigo

Move os arquivos **já rotacionados** para um disco com folga e comprime
lá. O destino é sugerido automaticamente: a montagem com mais espaço
livre que não seja a raiz.

**Nada é apagado.**

### Por que `mv` funciona com o disco cheio

Mover entre discos diferentes escreve no destino **antes** de liberar a
origem. Funciona mesmo com 0 bytes livres no disco de origem — que foi
exatamente o caso do `vm-dbserver`.

### Por que `truncate` e nunca `rm` no arquivo ativo

O `syslog` ativo, se você marcar a opção, é **copiado** para o destino e
depois zerado com `truncate -s 0`.

Isso não é preciosismo. O rsyslog mantém o arquivo aberto: apagar libera o
*nome*, mas o inode só é devolvido quando o processo fecha. Você ficaria
**sem o log e sem o espaço** — o pior dos dois mundos, e um erro que
parece funcionar até você olhar o `df`.

### Compressão em segundo plano

Comprimir 33 GB demora. O painel dispara o `gzip` em segundo plano, no
disco de destino, depois que o espaço já foi liberado. A tela não fica
esperando.

## Depois de aplicar

Rode **Diagnosticar** de novo em alguns minutos. O crescimento em MB/dia
é o número que prova. Antes da contenção, num servidor real: ~8 GB/dia.
Depois, algumas centenas de MB.

## O que a contenção não resolve

Ela impede o disco de encher. **Não** reduz o volume dentro de
`docker logs` nem no journald — só evita a gravação no `/var/log/syslog`.

Se você quiser reduzir na origem, é preciso editar o `configs/` do
FindFace e reiniciar o container afetado. Como o filtro resolve o
problema de disco sem reiniciar nada e sem perder erro nenhum, isso só
vale se houver outra razão.

## Monitoramento contínuo

O painel avisa, mas ninguém abre o painel todo dia. Duas camadas
complementares:

**No painel:** qualquer montagem acima de 90% deixa o cartão do servidor
vermelho, na frente até de "serviço com problema" — porque disco cheio é
o que causa o resto.

**No Zabbix**, que vocês já têm:

```
vfs.file.size[/var/log/syslog]     trigger em 2 GB
vfs.fs.size[/,pfree]               trigger em 15%
```

O segundo é o que faltou. Um disco levou semanas para encher; houve
tempo de sobra para alertar.

## Limpeza pontual do painel

A faxina diária resolve o regime. Não resolve o caso pontual: "o disco do
painel encheu por causa das gravações de terminal, quero só elas, e só as
de mais de 180 dias". O caminho antigo era mexer na retenção configurada
— que vale para **todo dia**, e alguém esquece de voltar.

Em **Manutenção → Limpeza pontual**: marque as categorias, informe a
idade, veja a conta, confirme.

| Categoria | O que sai |
|---|---|
| Gravações do InTerminal | arquivos `.cast` no disco do painel |
| Sobras de staging de backup | arquivo deixado por execução que falhou no meio |
| Registros de auditoria | linhas de auditoria **não críticas** |
| Sessões de terminal encerradas | a linha do histórico da sessão |
| Texto do log das execuções | o texto; a linha da execução fica |
| Amostras do monitor | os pontos dos gráficos da aba Monitor |

As cercas, porque apagar histórico não tem volta:

1. **Simulação primeiro.** O botão `Ver o que sai` só conta. Mudar a
   seleção descarta a conta anterior — número velho ao lado de um botão de
   apagar é o jeito mais fácil de apagar o que não se queria.
2. **Piso de sete dias, no servidor.** Mesmo que a requisição peça zero.
3. **Confirmação por digitação** (`LIMPAR`) e registro de auditoria em
   nível crítico.
4. **Fora de alcance, sempre:** auditoria de nível crítico, artefato de
   backup, execução de backup em andamento, sessão de terminal aberta,
   cadastro de servidor, usuário, agendamento e destino. A retenção
   automática também não é tocada — a faxina de amanhã roda igual.

Requer `maintenance.view` para simular e `maintenance.apply` para
aplicar.

## Permissões

| Ação | Permissão |
|---|---|
| Diagnosticar e simular | `maintenance.view` |
| Aplicar contenção e arquivar | `maintenance.apply` |

Simular exige apenas `view` — ver o que mudaria não muda nada. Aplicar é
ação destrutiva no catálogo: gera auditoria de nível `critical` e exige
confirmação digitando o nome do servidor.
