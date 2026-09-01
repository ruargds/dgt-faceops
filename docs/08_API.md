# Referência da API

Base: `/api`. OpenAPI interativo do painel em execução: `/api/docs`.

Autenticação: `Authorization: Bearer <token>` em tudo, exceto
`POST /auth/login` e `GET /saude`.

## Autenticação

| Método | Rota | Permissão | O que faz |
|---|---|---|---|
| POST | `/auth/login` | — | Autentica; devolve token e usuário |
| GET | `/auth/me` | autenticado | Usuário atual + permissões efetivas |
| GET | `/auth/catalogo` | autenticado | Catálogo de permissões e perfis |
| POST | `/auth/trocar-senha` | autenticado | Troca a própria senha |
| GET | `/auth/usuarios` | `users.manage` | Lista usuários |
| POST | `/auth/usuarios` | `users.manage` | Cria usuário |
| PATCH | `/auth/usuarios/{id}` | `users.manage` | Atualiza usuário |
| DELETE | `/auth/usuarios/{id}` | `users.manage` | Remove usuário |

```http
POST /api/auth/login
{ "username": "admin", "password": "admin123" }

200
{
  "access_token": "eyJ…",
  "token_type": "bearer",
  "usuario": { "id": 1, "username": "admin", "role": "admin",
               "senha_padrao": true, … }
}
```

`senha_padrao: true` faz a UI mostrar a faixa de aviso.

## Servidores

| Método | Rota | Permissão |
|---|---|---|
| POST | `/hosts/scan-chave` | `hosts.manage` |
| GET | `/hosts` | `hosts.view` |
| GET | `/hosts/{id}` | `hosts.view` |
| POST | `/hosts` | `hosts.manage` |
| PATCH | `/hosts/{id}` | `hosts.manage` |
| DELETE | `/hosts/{id}` | `hosts.manage` |
| POST | `/hosts/{id}/testar` | `hosts.view` |

```http
POST /api/hosts/scan-chave
{ "address": "10.0.1.10", "ssh_port": 22 }

200
{ "host_key_pub": "ssh-ed25519 AAAA…", "fingerprint": "SHA256:vK3…" }
```

Lê a chave **sem autenticar**. Passo obrigatório antes de cadastrar.

```http
POST /api/hosts
{
  "name": "vm-appserver",
  "role": "appserver",
  "address": "10.0.1.10",
  "ssh_port": 22,
  "ssh_user": "azureuser",
  "auth_method": "key",
  "ssh_key": "-----BEGIN OPENSSH PRIVATE KEY-----\n…",
  "sudo_password": "…",
  "has_gpu": false
}
```

Os segredos **só entram**. A resposta traz `key_fingerprint` e
`host_key_fingerprint`, nunca os valores.

No `PATCH`, campo de segredo omitido mantém o que está no cofre. Enviar
string vazia **apagaria** — a UI só envia o que foi digitado.

```http
POST /api/hosts/1/testar

200
{
  "ok": true, "usuario": "azureuser", "hostname": "vm-appserver",
  "kernel": "5.15.0-1058-azure", "latencia_ms": 187,
  "sudo": true, "findface_presente": true,
  "docker_presente": true, "gpu_presente": false,
  "ffmulti_dir": "/opt/findface-multi"
}
```

Detecta GPU sozinho e grava em `has_gpu`. Falha devolve **200** com
`{"ok": false, "erro": "…"}` — não é erro de requisição, é resultado de
diagnóstico.

## Painel e recursos

| Método | Rota | Permissão |
|---|---|---|
| GET | `/painel` | `hosts.view` |
| GET | `/metrics` | `metrics.view` |
| GET | `/metrics/{id}` | `metrics.view` |
| GET | `/metrics/{id}/armazenamento` | `metrics.view` |

`GET /api/dispositivos/{host_id}/licenca` (`metrics.view`) devolve o
licenciamento do FindFace daquele servidor: `itens[]` com `recurso`,
`limite`, `usado`, `restante` e `ilimitado`, mais `cameras_cadastradas`,
o `caminho` que respondeu e o corpo `bruto`. Exige URL e token da API
cadastrados no host — responde 400 explicando quando não há.

`POST /api/manutencao/faxina/pontual` (`maintenance.view` para simular,
`maintenance.apply` para aplicar) faz a limpeza pontual do painel:
`{categorias: [...], dias: 90, simular: true}`. Aplicar exige
`simular: false` e `confirmar: "LIMPAR"`; o piso de sete dias é do
servidor, não da tela. Categorias: `gravacoes`, `staging`, `auditoria`,
`sessoes`, `logs_execucao`, `amostras`.

`POST /api/terminal/ticket/{host_id}` (`terminal.use`) aceita, no corpo,
`{usuario, senha}` — a credencial daquela sessão de terminal. Senha vazia
usa a credencial do cofre. A senha vai no corpo, nunca na query string, e
não é gravada em lugar nenhum.

`GET /metrics` coleta de todos os hosts ativos em paralelo. Host fora do ar
vira `{"ok": false, "erro": "…"}` no seu item, sem derrubar a resposta.

```http
GET /api/metrics/1

200
{
  "host": "vm-appserver", "uptime_segundos": 4823910, "coleta_ms": 892,
  "cpu": { "nucleos": 8, "carga_1min": 2.14, "carga_por_nucleo": 0.27 },
  "memoria": {
    "total_bytes": 33654886400, "usado_bytes": 18203451392,
    "disponivel_bytes": 15451435008, "cache_bytes": 8123456789,
    "percentual": 54.1, "swap_percentual": 0.0
  },
  "discos": [ { "ponto": "/", "usado_bytes": …, "percentual": 61.2 } ],
  "gpus": [ { "indice": "0", "nome": "Tesla T4", "utilizacao_pct": 47.0,
              "memoria_pct": 62.3, "temperatura_c": 58.0 } ],
  "gpu_processos": [ … ],
  "containers": [ { "nome": "…", "cpu_pct": 12.4, "memoria_bytes": … } ],
  "tem_gpu": true
}
```

`usado_bytes` é `total - MemAvailable` — não conta cache como uso.

`GET /metrics/{id}/armazenamento` roda `du` em `data/`. **Leva minutos.**
`"parcial": true` indica timeout em parte da árvore.

## Serviços

| Método | Rota | Permissão |
|---|---|---|
| GET | `/services/{id}` | `services.view` |
| GET | `/services/{id}/logs/{container}` | `services.view` |
| POST | `/services/{id}/restart` | `services.restart` |
| POST | `/services/{id}/stack` | `services.stack` |

```http
GET /api/services/1

200
{
  "projeto": "findface-multi",
  "compose_file": "/opt/findface-multi/docker-compose.yaml",
  "total": 27, "rodando": 26, "com_problema": 1,
  "servicos": [
    { "nome": "findface-multi-findface-video-worker-1",
      "servico": "findface-video-worker",
      "estado": "running", "saude": "healthy", "reinicios": 4,
      "oom_killed": false, "usa_gpu": true, "guarda_dados": false }
  ]
}
```

```http
POST /api/services/1/stack
{ "acao": "stop", "confirmar_host": "vm-appserver" }
```

`acao`: `stop` | `up` | `restart`. `stop` e `restart` exigem
`confirmar_host` igual ao nome do servidor — nome errado devolve 400.

## Monitor, incidentes e limiares

Ver [23_MONITOR_E_CAMERAS](23_MONITOR_E_CAMERAS.md) e
[25_INCIDENTES_E_LIMIARES](25_INCIDENTES_E_LIMIARES.md).

| Método | Rota | Permissão |
|---|---|---|
| GET | `/monitor/estado` | `metrics.view` |
| GET | `/monitor/alertas` | `metrics.view` |
| GET | `/monitor/serie/{host_id}` | `metrics.view` |
| GET | `/monitor/resumo` | `metrics.view` |
| GET | `/monitor/incidentes/recentes` | `metrics.view` |
| GET | `/monitor/pico` | `metrics.view` |
| GET | `/incidentes/abertos` | `metrics.view` |
| GET | `/incidentes/recentes` | `metrics.view` |
| GET | `/limiares` | `hosts.view` |
| PUT | `/limiares` | `users.manage` |
| DELETE | `/limiares/{override_id}` | `users.manage` |
| GET | `/diagnostico/reincidencia` | `metrics.view` |
| GET | `/diagnostico/padroes` | `services.view` |
| POST | `/diagnostico/analisar/{host_id}` | `services.view` |
| GET | `/diagnostico/catalogo` | `services.view` |

As rotas de `/diagnostico` são leitura barata (banco do painel), com uma
exceção: `POST /diagnostico/analisar/{host_id}?servico=` **lê o log no
servidor** — por isso é POST e só roda no clique. Ver
[27_DIAGNOSTICO](27_DIAGNOSTICO.md).

```http
GET /api/monitor/resumo

200
{
  "servidores": [ ... ],
  "alertas": [
    { "host_id": 3, "host": "vm-ftpserver", "chave": "servico",
      "nivel": "critico", "texto": "findface-video-worker — findface-video-worker com problema",
      "servico": "findface-video-worker", "desde": "2026-08-26T18:07:00Z",
      "acao": "reiniciando repetidamente (7x) — sinal de câmera problemática ...",
      "onde": "Serviços", "onde_aba": "servicos" }
  ],
  "incidentes_abertos": [
    { "id": 42, "host_id": 3, "tipo": "servico", "servico": "findface-video-worker",
      "nivel": "critico", "causa_provavel": "reiniciando repetidamente (7x) ...",
      "inicio": "2026-08-26T18:07:00Z", "fim": null, "aberto": true }
  ]
}
```

```http
PUT /api/limiares
{ "chave": "servico_reinicios", "valor": 10, "host_id": 3, "servico": "findface-video-worker" }
```

`chave` de host (`disco_pct`, `mem_pct`, `swap_pct`, `cpu_pct`,
`gpu_mem_pct`, `gpu_temp`) exige `servico` vazio; `chave` de serviço
(`servico_reinicios`, `servico_indisponivel_min`) exige `servico`
preenchido. `host_id` omitido/nulo vale para todos os hosts.
`DELETE /api/limiares/{id}` apaga a exceção — o limite volta ao padrão
global de Configurações.

## Backups

| Método | Rota | Permissão |
|---|---|---|
| POST | `/backups/{host_id}` | `backups.run` |
| GET | `/backups` | `backups.view` |
| GET | `/backups/{run_id}` | `backups.view` |
| GET | `/backups/{run_id}/download` | `backups.download` |
| DELETE | `/backups/{run_id}` | `backups.delete` |
| GET | `/backups-armazenamento` | `backups.view` |

```http
POST /api/backups/1
{
  "perfil": "essencial",
  "destinos": ["local", "azure"],
  "retencao_dias": 30,
  "aceito_downtime": false
}

202
{ "id": 87, "status": "pendente", "stage": "Na fila", "progress": 0, … }
```

Responde **202** e segue em segundo plano. Acompanhe por
`GET /backups/{id}` — `stage` e `progress` avançam.

`perfil: "completo"` sem `aceito_downtime: true` devolve 400.

Filtros em `GET /backups`: `host_id`, `perfil`, `status`, `limite`.

`GET /backups/{id}` traz o campo `log` completo, que os outros não trazem.

`DELETE` apaga o arquivo local e marca `expired`. **Não** apaga da nuvem.

## Agendamentos

| Método | Rota | Permissão |
|---|---|---|
| GET | `/schedules` | `schedules.view` |
| POST | `/schedules` | `schedules.manage` |
| PATCH | `/schedules/{id}` | `schedules.manage` |
| DELETE | `/schedules/{id}` | `schedules.manage` |
| POST | `/schedules/{id}/executar` | `backups.run` |

```http
POST /api/schedules
{
  "name": "Essencial diário — appserver",
  "host_id": 1,
  "perfil": "essencial",
  "cron": "0 2 * * *",
  "destinos": ["local", "azure"],
  "retencao_dias": 30,
  "enabled": true,
  "allow_downtime": false
}

201
{ "id": 3, "cron_legivel": "todo dia às 02:00",
  "next_run_at": "2026-08-25T02:00:00-03:00", … }
```

Cron de 5 campos, fuso do painel. Inválido devolve 400 com mensagem legível.

`perfil: "completo"` exige `allow_downtime: true`.

O `host_id` não muda no `PATCH` — crie outro agendamento.

## InTerminal

| Método | Rota | Permissão |
|---|---|---|
| POST | `/terminal/ticket/{host_id}` | `terminal.use` |
| WS | `/terminal/ws?ticket=…` | (ticket) |
| GET | `/terminal/ativas` | `terminal.sessions.view` |
| GET | `/terminal/sessoes` | `terminal.sessions.view` |
| GET | `/terminal/sessoes/{id}/gravacao` | `terminal.sessions.view` |

Protocolo do WebSocket em [07_INTERMINAL](07_INTERMINAL.md).

## Auditoria

| Método | Rota | Permissão |
|---|---|---|
| GET | `/auditoria` | `audit.view` |
| GET | `/auditoria/resumo` | `audit.view` |

Filtros: `usuario`, `action`, `level`, `desde`, `limite`.

## Saúde

```http
GET /api/saude

200
{ "ok": true, "servico": "dgt-faceops", "versao": "0.1.0",
  "agendamentos": 6, "terminais_ativos": 1 }
```

Sem autenticação. Usado pelo healthcheck do container — e serve para o
Zabbix monitorar o painel.

## Erros

| Código | Significado |
|---|---|
| 400 | Entrada inválida, confirmação faltando, aceite de janela ausente |
| 401 | Sem token, token expirado, usuário inativo |
| 403 | Perfil sem a permissão — a mensagem diz qual |
| 404 | Recurso não existe |
| 409 | Conflito: nome duplicado, ou backup já em andamento no host |
| 422 | Falha de validação do Pydantic |
| 502 | Falha ao falar com o servidor remoto (SSH, docker) |

Formato: `{"detail": "mensagem legível"}`.

As mensagens são escritas para aparecer direto na tela. `ValueError` do cofre
é convertido em 400 com a explicação da `SECRET_KEY` trocada, em vez de 500
genérico.
