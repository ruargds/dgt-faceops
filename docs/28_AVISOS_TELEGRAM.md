# Avisos no Telegram

Mesmo fluxo que a equipe já usa no Zabbix: um bot por cliente, adicionado
ao grupo de quem precisa receber. Aba **Avisos (Telegram)**, em
Administração — exige perfil administrador (`users.manage`).

---

## Configurar

1. Crie o bot no `@BotFather` e guarde o token.
2. Adicione o bot ao grupo que vai receber.
3. Pegue o id do grupo (começa com `-100…`).
4. Na aba, cole token e id, marque **Enviar avisos** e salve.
5. **Enviar teste** — prova que os dois estão certos, agora, e não às 3h
   da manhã quando algo cair.

O token é validado no Telegram (`getMe`) **antes** de ser guardado: token
errado falha na tela de configuração, não em silêncio depois.

**Trocar de conta** é colar um token novo — o antigo é substituído.
Deixar o campo vazio mantém o que já existe.

## O que receber

Sem nenhuma regra ligada, **nada é enviado**. Silêncio por omissão é o
padrão: ninguém é surpreendido por aviso que não pediu.

A regra mais específica vence:

```
(servidor + serviço)  >  (servidor)  >  (serviço em todo servidor)  >  (todos)
```

| Onde | O que faz |
|---|---|
| **Todos os servidores e serviços** | a rede de segurança — vale para o que não tiver regra própria |
| **Por servidor** | escolhe de quais máquinas receber |
| **Serviço a serviço** | dentro do servidor, marca só os que interessam |

Cada regra tem dois ajustes:

- **Nível**: *Só quando parar* (crítico) ou *Atenção e parada* (tudo).
- **Avisar quando voltar**: ligado por padrão — saber que normalizou evita
  alguém sair de casa por um problema que já passou.

A lista de serviços de cada máquina vem do que o coletor **já viu**
(`hosts.servicos_conhecidos`), sem abrir SSH: escolher caixinhas é
configuração, e configuração não pode custar uma ida a quatro servidores
de produção. Servidor recém-cadastrado aparece sem lista até o primeiro
ciclo do monitor passar.

## Como a mensagem chega

Curta de propósito — quem está de plantão decide "levanto ou não" pela
prévia do celular, sem abrir o app. No máximo quatro linhas:

```
🔴 PARADO · vm-appserver
findface-video-worker com problema
Provável: reiniciou 7x nos últimos 30 min
Desde 01/09 14:32
```

```
🟢 NORMALIZADO · vm-appserver
findface-video-worker voltou
Ficou fora 6min
```

Texto puro, sem Markdown: nome de container vem com `_`, `-` e `.`, que
quebram o parser do Telegram e fariam a mensagem falhar justamente no meio
de um incidente. E **sem endereço interno** — IP de servidor não vai para
um grupo de mensagens.

## Contra virar spam

Aviso que repete é aviso que se aprende a ignorar. Quatro travas:

| Trava | O que faz |
|---|---|
| Deduplicação | cada evento tem uma chave; o mesmo evento não é mandado duas vezes |
| Teto por ciclo | no máximo 8 mensagens por passada — dez serviços caindo juntos não viram dez mensagens |
| Só transição | manda quando **entra** e quando **sai** do problema, nunca "ainda está fora" |
| Retorno opcional | por regra, quem não quer o "voltou" desliga |

A chave carrega o horário de início: o mesmo serviço caindo de novo amanhã
é outro evento, e avisa de novo.

## Segurança

| Item | Como está |
|---|---|
| Token em repouso | cifrado com Fernet (`core.vault`), mesma caixa das chaves SSH |
| Token em resposta de API | **nunca sai** — a tela recebe nome do bot e impressão digital |
| Token em log/erro | removido antes de qualquer registro (`telegram_service._limpar`); a URL do Telegram carrega o token no caminho, e traceback vira anexo de chamado |
| Quem configura | só `users.manage` |
| Auditoria | trocar conta, criar/mudar/apagar regra — tudo registrado com autor e IP |
| Saída de rede nova | `api.telegram.org:443`. É a única saída externa do painel além do que já existia |
| Conteúdo | nome de host e serviço; sem IP, sem credencial, sem caminho de disco |

Há cenário de teste que falha se o token aparecer numa resposta ou numa
mensagem de erro.

## Peso no servidor

- **Nenhuma dependência nova.** Usa `httpx` se a imagem tiver, senão
  `urllib` numa thread — mesma convenção do `ffapi_service`. Para um POST
  pequeno num endereço fixo, trazer biblioteca seria peso e superfície de
  ataque sem retorno.
- **Sem processo de escuta.** O bot do InfraCore faz long-polling porque
  responde comandos; aqui só há envio, e um laço aberto 24h para nada
  seria custo permanente.
- **Fora do caminho crítico.** O aviso é o último passo do ciclo: amostra e
  incidente já estão gravados quando o Telegram é chamado. O serviço nunca
  levanta exceção — Telegram fora do ar não derruba o monitor. Há teste
  para isso.

## Rotatividade

O log de envios (`notificacao_envios`) é operacional, não histórico:
serve para deduplicar, para retentar e para responder "não recebi".
Retenção de **14 dias** (`notificacao.retencao_dias`), apagado pela faxina
diária — inclusive as falhas, porque o que importa delas é o agora.

Regra de servidor apagado sai junto com o servidor (`ON DELETE CASCADE`).

## API

| Método | Rota | Permissão |
|---|---|---|
| GET | `/notificacoes/conta` | `users.manage` |
| PUT | `/notificacoes/conta` | `users.manage` |
| POST | `/notificacoes/testar` | `users.manage` |
| GET | `/notificacoes/regras` | `users.manage` |
| PUT | `/notificacoes/regras` | `users.manage` |
| DELETE | `/notificacoes/regras/{id}` | `users.manage` |
| GET | `/notificacoes/envios` | `users.manage` |

## Verificação

`python tests/verificar.py` cobre: precedência das regras (serviço >
servidor > geral) e o silêncio por omissão, formato e tamanho da mensagem,
ausência de IP, deduplicação, falha do Telegram não derrubando o ciclo, e o
token não vazando nem em resposta nem em erro.
