# Permissões e perfis

## Como funciona

As permissões são **fixas no código** (`backend/app/core/permissions.py`),
não em tabela SQL. Não existe tabela `permissions` — não tente inserir nada
nela.

Cada usuário tem um `role`. O perfil resolve para um conjunto de permissões
via `permissions_for()`. `is_super_admin` recebe tudo, sempre.

Na interface, a regra é: **botão sem permissão é omitido, não desabilitado.**
Botão cinza que não faz nada gera chamado de suporte; botão ausente não gera
dúvida.

## Os quatro perfis

### Observador — somente leitura

Vê o painel inteiro e não executa nada. É o perfil para gestão, para o
cliente e para quem precisa acompanhar sem risco de mexer.

```
hosts.view · metrics.view · services.view · backups.view · schedules.view
```

Não tem: terminal, reinício, backup, agendamento, auditoria, usuários.

### Operador de plantão

O que o Observador tem, mais o necessário para destravar um serviço às 3 da
manhã:

```
+ services.restart     reiniciar container individual
+ backups.run          disparar backup sob demanda
+ terminal.use         abrir terminal (sem sudo)
```

Não tem: parar o stack, restore, apagar backup, mexer em agendamento,
terminal com sudo.

O raciocínio: reiniciar um `findface-video-worker` travado resolve a maioria
dos incidentes e é reversível. Parar o stack inteiro não é decisão de
plantão.

### Técnico

Perfil da equipe de infraestrutura:

```
+ services.restart · backups.run · backups.download
+ schedules.manage        criar e editar recorrência
+ terminal.use · terminal.sudo
+ terminal.sessions.view  ver gravações de sessão
+ audit.view
```

Não tem: `services.stack`, `backups.restore`, `backups.delete`,
`hosts.manage`, `users.manage`.

> Observação honesta: `terminal.sudo` é root nos servidores. O Técnico pode,
> por dentro do terminal, fazer tudo que as permissões que faltam fariam. A
> diferença é que pelo terminal fica **gravado em asciicast**, e pelos botões
> ficaria como uma linha de auditoria. A separação existe para deixar
> explícito o caminho auditável, não para conter quem tem shell.

### Administrador

Tudo do catálogo, inclusive:

```
+ services.stack       parar/subir o stack (derruba o reconhecimento)
+ backups.restore      restaurar sobre produção
+ backups.delete       apagar artefato
+ hosts.manage         cadastrar e editar servidores, e suas credenciais
+ users.manage         gerenciar usuários
```

## Catálogo completo

| Código | O que libera |
|---|---|
| `hosts.view` | Ver servidores cadastrados |
| `hosts.manage` | Cadastrar, editar e remover servidores (inclui credenciais) |
| `metrics.view` | Ver RAM, GPU, disco e carga |
| `services.view` | Ver status dos containers do Face Detect |
| `services.power` | parar ou subir **um** container. Separada do restart de propósito: reiniciar volta sozinho, parar FICA parado — e serviço parado por descuido não gera erro, só ausência. Destrutiva (auditoria em nível crítico), exige o nome do container digitado, e **fora do perfil de plantão** |
| `services.restart` | Reiniciar um container individual |
| `services.stack` | Parar/subir o stack inteiro |
| `backups.view` | Ver histórico e artefatos |
| `backups.run` | Disparar backup sob demanda |
| `backups.download` | Baixar artefato |
| `backups.restore` | Restaurar backup sobre o servidor |
| `backups.delete` | Apagar artefato |
| `schedules.view` | Ver agendamentos |
| `schedules.manage` | Criar, editar, pausar e remover agendamentos |
| `terminal.use` | Abrir sessão de terminal SSH |
| `terminal.sudo` | Executar com sudo no terminal |
| `terminal.sessions.view` | Ver gravações de sessões |
| `audit.view` | Ver log de auditoria |
| `users.manage` | Gerenciar usuários e perfis |

## Ações destrutivas

Marcadas em `DESTRUCTIVE_PERMISSIONS`. Toda ação com uma dessas gera
registro de auditoria com nível `critical`:

```
services.stack · backups.restore · backups.delete · hosts.manage
```

### Dupla confirmação por digitação

Parar ou reiniciar o stack exige digitar **o nome exato do servidor**:

```
POST /api/services/{host_id}/stack
{ "acao": "stop", "confirmar_host": "vm-appserver" }
```

Nome errado devolve 400 com a mensagem explicando o que se espera. A
validação é no backend — não é enfeite de tela.

Por que digitar em vez de "tem certeza? [OK]": um diálogo de confirmação
vira reflexo na terceira vez. Digitar o nome obriga a olhar **qual**
servidor vai sofrer. É o mesmo motivo pelo qual o GitHub pede o nome do
repositório para apagá-lo.

### Aceite de janela para o perfil completo

O perfil `completo` para o Face Detect. Duas travas:

- **Sob demanda:** `aceito_downtime: true` no corpo. Sem isso, 400 com a
  explicação.
- **Agendado:** o campo `allow_downtime` no agendamento. Sem ele, na hora de
  executar o agendamento registra `bloqueado: perfil completo sem aceite de
  janela` e **não roda**.

Falhar visível é melhor que parar a produção às 3h sem ninguém ter
autorizado.

## Cercas que não são permissão

Duas proteções valem para todos, inclusive o super admin:

**Container de fora do projeto.** Antes de reiniciar, o painel confere
`com.docker.compose.project` e recusa se não for o projeto do Face Detect
daquele host. Sem isso, um nome arbitrário derrubaria o agente Zabbix ou
qualquer outro container do servidor.

**Travessia de caminho no download.** Nome de artefato com `/`, `\` ou
começando com `.` é recusado, e o caminho resolvido é conferido contra o
diretório base. A gravação de terminal passa pela mesma checagem.

## Proteções contra tiro no pé

| Situação | O que acontece |
|---|---|
| Desativar a própria conta | 400 — "você não pode desativar a própria conta" |
| Remover a própria conta | 400 |
| Remover o super admin | 400 |
| Perfil inexistente | 400 com a lista dos válidos |

## Adicionar uma permissão nova

1. Registre em `PERMISSION_CATALOG` (`core/permissions.py`)
2. Se for destrutiva, adicione a `DESTRUCTIVE_PERMISSIONS`
3. Inclua nos perfis que devem tê-la em `ROLE_PERMISSIONS`
4. Na rota: `Depends(require_permission("codigo.novo"))`
5. Na tela: esconda o botão com `has("codigo.novo")`

`require_permission` **levanta erro na subida** se o código não estiver no
catálogo — erro de digitação não passa para produção como permissão que
ninguém tem.

## A tela que explica isso

**Usuários → O que cada perfil pode** mostra a matriz completa, agrupada
por área, com o que cada permissão faz na prática e quais são
destrutivas. Ela vem de `/auth/perfis`, montada do mesmo catálogo que
autoriza — ver [32_SESSAO_E_PERFIS](32_SESSAO_E_PERFIS.md).
