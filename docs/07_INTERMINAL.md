# InTerminal — terminal SSH no navegador

Shell real no servidor, pela interface do painel. Não é emulação nem
executor de comando: é um PTY de verdade, com cores, `htop`, editor,
`docker logs -f`, tudo.

## Por que existe

Investigar um problema no FindFace normalmente termina no shell. Alternar
entre o painel e um cliente SSH separado significa: outra ferramenta, outra
credencial, e — o que mais importa — **nenhum rastro**. Pelo InTerminal, a
sessão é gravada e associada a um usuário nominal.

## Como funciona

```
Navegador                       Painel                      Servidor
─────────                       ──────                      ────────
xterm.js
   │ 1. POST /terminal/ticket/{id}
   │ ──────────────────────────▶  valida permissão
   │ ◀──────────────────────────  ticket (30s, uso único)
   │
   │ 2. WS /terminal/ws?ticket=…
   │ ──────────────────────────▶  consome ticket
   │                              asyncssh.connect()  ────────▶ SSH
   │                              create_process(PTY) ────────▶ bash
   │ ◀──────────────────────────  {"tipo":"pronto"}
   │
   │ 3. {"tipo":"in","dados":"ls\n"}
   │ ──────────────────────────▶  stdin do PTY ──────────────▶
   │ ◀──────────────────────────  {"tipo":"out",…} ◀────────── stdout
   │                              grava em .cast
```

### Ticket de uso único

O navegador não permite mandar cabeçalho `Authorization` ao abrir WebSocket.
O caminho fácil é `?token=<jwt>` — e aí o JWT vai para o log de acesso do
nginx, para o histórico do navegador e para qualquer proxy no meio.

O ticket:

- 32 bytes aleatórios (`secrets.token_urlsafe`)
- vale **30 segundos**
- **uso único** — é removido do dicionário ao ser consumido
- amarrado a **um** host e a **um** usuário
- carrega a decisão sobre `terminal.sudo`, tomada na emissão

Vazar o ticket depois do uso não dá nada.

### Conexão dedicada

A sessão **não** usa o pool de conexões do painel. Um PTY interativo fica
ocupado a sessão inteira; dividir canal com coleta de métrica atrapalharia
as duas coisas.

A mesma pinagem de chave de host vale aqui — sessão em servidor cuja chave
mudou é recusada.

## Protocolo

JSON em texto, nos dois sentidos.

**Cliente → servidor**

```json
{"tipo": "in",     "dados": "ls -la\n"}
{"tipo": "resize", "colunas": 140, "linhas": 40}
{"tipo": "ping"}
```

**Servidor → cliente**

```json
{"tipo": "pronto", "host": "vm-appserver", "usuario_ssh": "azureuser",
                   "sudo": true, "gravando": true}
{"tipo": "out",    "dados": "total 24\r\n…"}
{"tipo": "erro",   "mensagem": "…"}
{"tipo": "fim",    "motivo": "shell encerrado"}
{"tipo": "pong"}
```

Códigos de fechamento: `4401` ticket inválido, `4404` host não encontrado,
`4500` falha ao abrir a sessão.

## Gravação

Formato **asciicast v2** (asciinema). Uma linha JSON por evento.

Escolhido por dois motivos: dá para reproduzir com `asciinema play`, e dá
para achar um comando com `grep` quando alguém precisa numa auditoria — o
que não acontece com formato binário.

```
{"version":2,"width":120,"height":32,"timestamp":1756000000,
 "title":"rua@vm-appserver","env":{"TERM":"xterm-256color","SHELL":"/bin/bash"}}
[0.412,"o","Last login: …\r\n"]
[1.203,"i","docker ps\n"]
[1.310,"o","CONTAINER ID   IMAGE …"]
```

`"i"` é entrada (o que foi digitado), `"o"` é saída (o que apareceu na tela).

**Baixar:** Auditoria → Sessões de terminal → botão de download.

**Reproduzir:**

```bash
asciinema play 20260824-143012_vm-appserver_rua.cast

# em velocidade dobrada, sem pausas longas
asciinema play -s 2 -i 0.5 arquivo.cast

# procurar um comando específico
grep '"i"' arquivo.cast | grep -i "rm "
```

Arquivos ficam em `data/sessions` (mapeado para `/data/sessions` no
container). Desligar com `TERMINAL_RECORD=false` — não recomendado.

## Limites e proteções

| Proteção | Valor | Motivo |
|---|---|---|
| Timeout por inatividade | 30 min (`TERMINAL_IDLE_TIMEOUT_MIN`) | shell aberto e esquecido em servidor de produção |
| Entrada máxima por mensagem | 64 KB | colar 50 MB não pode virar consumo de memória |
| Colunas / linhas | 20–500 / 5–200 | valor absurdo derrubaria o PTY |
| Varredura de ociosas | a cada 60s | tarefa de fundo |

Ao sair da tela, o React fecha a sessão no `useEffect` de limpeza. PTY órfão
consome recurso e mantém shell aberto sem ninguém olhando.

Na parada do painel, `encerrar_todas()` fecha tudo com motivo
`painel reiniciado`.

## Marcação de sudo

Quando `sudo` aparece na entrada, a sessão é marcada com `sudo_used = true`,
visível no histórico.

**Isto é rastro, não controle.** Um alias, um script ou um `sudo` digitado
com espaço extra escapam da detecção. Quem tem `terminal.use` pode escalar
se o usuário SSH tiver sudo — a diferença é que o `.cast` mostra exatamente
o que foi feito.

O campo `sudo` no `{"tipo":"pronto"}` reflete a permissão `terminal.sudo` e
serve para a UI mostrar o selo. Não bloqueia nada no PTY: bloquear de
verdade exige `sudoers` no servidor.

## Proxy — o detalhe que quebra

WebSocket através de nginx precisa de configuração explícita. Sem ela, o
terminal conecta, funciona por 60 segundos e morre sem mensagem:

```nginx
location = /api/terminal/ws {
    proxy_pass http://backend:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_buffering off;
}
```

Já está em `frontend/nginx.conf`. Se houver **outro** proxy na frente
(Cloudflare Tunnel, nginx externo, load balancer), ele precisa do mesmo
tratamento.

A CSP também precisa liberar: `connect-src 'self' ws: wss:`. Sem isso o
navegador bloqueia o WebSocket em silêncio, e o console mostra erro de CSP
que ninguém procura.

## Auditoria da sessão

Duas linhas por sessão:

```
terminal.open   {"sessao_id": 42, "gravacao": true}
terminal.close  {"sessao_id": 42, "motivo": "navegador desconectou",
                 "bytes_enviados": 1204, "bytes_recebidos": 88431,
                 "usou_sudo": true}
```

Mais a linha em `terminal_sessions` com início, fim, tráfego e caminho da
gravação.

## Sessões abertas agora

Auditoria → Sessões de terminal mostra quem está com terminal aberto,
tempo parado e tráfego. Requer `terminal.sessions.view`.

Útil para achar sessão esquecida antes do timeout, e para saber quem está
mexendo durante um incidente.

## Limitações conhecidas

- **Sem reconexão.** Cair a rede encerra a sessão; é preciso abrir de novo.
  O histórico de rolagem do xterm permanece na tela até recarregar.
- **Uma sessão por aba.** Abrir outra na mesma aba encerra a anterior.
- **Sem upload/download de arquivo.** Use o download de artefato de backup,
  ou `scp` fora do painel.
- **Sem multiplexação (`tmux`/`screen`).** Se precisar de sessão que
  sobrevive à desconexão, rode `tmux` dentro do terminal — funciona
  normalmente.
