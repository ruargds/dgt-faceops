# Avisos no Telegram

Um bot, quantos destinos precisar — grupo **ou** pessoa — e regras por
tipo de evento. Aba **Avisos (Telegram)**, em Administração; exige perfil
administrador (`users.manage`).

---

## O desenho, e de onde ele vem

Duas peças separadas, como em toda ferramenta que já resolveu isso:

| Ferramenta | Para onde | O que mandar |
|---|---|---|
| Zabbix | media type + usuário | action / trigger |
| Grafana | contact point | notification policy |
| Alertmanager | receiver | route |
| **FaceOps** | **destino** | **regra** |

Sem essa separação, cada destino novo exigiria duplicar todas as regras —
e foi por isso que a primeira versão (um bot, um grupo fixo) não escalava.

## 1. O bot

Crie no `@BotFather`, cole o token. Ele é validado no Telegram (`getMe`)
**antes** de ser guardado: token errado falha na tela de configuração, não
em silêncio às 3h da manhã.

**Habilitar o envio** é um interruptor próprio. Desligado, nada sai —
destinos e regras ficam guardados.

## 2. Destinos

| Tipo | O que é | O passo manual |
|---|---|---|
| **Grupo** | grupo ou canal; id negativo (`-100…`) | alguém precisa **adicionar o bot** ao grupo |
| **Pessoa** | conversa direta; id numérico dela | ela precisa mandar **`/start`** para o bot |

Os dois passos manuais são limite do **Telegram**, não do painel: um bot
não entra em grupo sozinho nem inicia conversa com alguém. Antes do
`/start`, qualquer envio volta recusado, com a mensagem de que o bot não
pode iniciar conversa com um usuário.

O que **dá** para automatizar está automatizado: o botão **descobrir
chats** lê o `getUpdates` e lista quem já falou com o bot — grupo e pessoa
— para escolher em vez de digitar id. Ressalva honesta: o `getUpdates` só
guarda o histórico recente (~24h).

Cada destino tem seu próprio **botão testar**, que manda uma mensagem
agora. É o que prova o caminho inteiro: token válido, chat existente e bot
com permissão de escrever ali. Há também **testar todos**, no topo.

## 3. Regras — o que mandar, e para quem

Sem regra ligada, **nada** é enviado: silêncio por omissão.

Cada regra combina:

| Campo | O que faz |
|---|---|
| **Enviar para** | um destino, ou *todos os destinos ativos* |
| **Servidor** | um, ou todos |
| **Serviço** | um, ou todos daquele servidor |
| **Tipos de evento** | quais eventos passam (abaixo) |
| **Gravidade mínima** | *só quando parar* (crítico) ou *atenção e parada* |
| **Avisar depois de** | só avisa se persistir por esse tempo |

**Regras diferentes valem ao mesmo tempo.** Uma mais específica não anula
as outras: o plantão pode receber tudo, e o dono de um serviço receber só
o dele. Cada uma manda para o seu destino.

### Tipos de evento

| Tipo | Quando |
|---|---|
| 🔴 **Serviço parado** | container fora do ar, unhealthy, morto por OOM ou reiniciando em laço |
| ⛔ **Servidor sem contato** | a máquina não respondeu ao coletor — costuma ser rede, não FindFace |
| 🟢 **Voltou ao normal** | o que estava fora voltou |
| 🟡 **Limite de recurso** | disco, memória, swap, carga, VRAM ou temperatura da GPU acima do limiar |

### "Avisar depois de" — o `for:` do Prometheus

Um serviço que pisca por 20 segundos e volta não deveria acordar ninguém.
Com espera de 5 min, o aviso só sai se o problema **persistir** — o ciclo
reavalia a cada passada e a deduplicação garante um envio só.

O **retorno ao normal nunca espera**: boa notícia não tem por que atrasar.

## Como a mensagem chega

Curta de propósito — quem está de plantão decide pela prévia do celular.
Um formato por tipo, para distinguir no primeiro caractere:

```
🔴 PARADO · vm-appserver
findface-video-worker com problema
Provável: reiniciou 7x nos últimos 30 min
Desde 02/09 14:32

🟢 NORMALIZADO · vm-appserver
findface-video-worker voltou
Ficou fora 6min

⛔ SEM CONTATO · vm-dbserver
A máquina não respondeu ao coletor
Provável: rede fora, VM desligada ou parada
Desde 02/09 03:10

🟡 LIMITE · vm-appserver
disco / em 94% — só 6 GB livres
Em Manutenção, use Diagnosticar para ver o que ocupa
```

O nome do servidor na mensagem é o **apelido**, quando houver — quem
recebe o aviso quer saber onde é, não como a VM se chama. Sem apelido, sai
o nome técnico.

Texto puro, sem Markdown: nome de container tem `_`, `-` e `.`, que
quebram o parser do Telegram e fariam a mensagem falhar justamente durante
um incidente. E **sem endereço interno** — IP de servidor não vai para um
grupo de mensagens.

## Contra virar spam

Aviso que repete é aviso que se aprende a ignorar. Cinco travas:

| Trava | O que faz |
|---|---|
| Deduplicação | por evento **e por destino**; o mesmo aviso não sai duas vezes |
| Teto por ciclo | no máximo 8 mensagens por passada |
| Espera | o `for:` acima — piscada não avisa |
| Só transição | manda quando entra e quando sai; nunca "ainda está fora" |
| Repetição desligada | `notificacao.repetir_apos_h` = 0 por padrão. Com 6, problema que dura dias volta a lembrar de 6 em 6h (o `repeat_interval` do Alertmanager) |

## Segurança

| Item | Como está |
|---|---|
| Token em repouso | cifrado com Fernet (`core.vault`), mesma caixa das chaves SSH |
| Token em resposta de API | **nunca sai** — a tela recebe nome do bot e impressão digital |
| Token em log/erro | removido antes de registrar (`telegram_service._limpar`); a URL do Telegram o carrega no caminho, e traceback vira anexo de chamado |
| Quem configura | só `users.manage` |
| Auditoria | bot, destino e regra — criar, mudar e apagar, com autor e IP |
| Saída de rede | `api.telegram.org:443`, só quando há evento |
| Conteúdo | nome de host e serviço; sem IP, sem credencial, sem caminho de disco |

## Peso no servidor

- **Nenhuma dependência nova.** `httpx` se a imagem tiver, senão `urllib`
  numa thread — mesma convenção do `ffapi_service`.
- **Sem processo de escuta.** O bot do InfraCore faz long-polling porque
  responde comandos; aqui só há envio, e só quando há evento.
- **Fora do caminho crítico.** O aviso é o último passo do ciclo: amostra e
  incidente já estão gravados quando o Telegram é chamado. `despachar()`
  nunca levanta — Telegram fora do ar não derruba o monitor.

## Rotatividade

`notificacao_envios` é log operacional: serve para deduplicar, diagnosticar
"não recebi" e retentar. Retenção de **14 dias**
(`notificacao.retencao_dias`), apagado pela faxina — inclusive as falhas.

Destino apagado leva junto as regras que apontavam só para ele
(`ON DELETE CASCADE`): regra que não manda para lugar nenhum seria
configuração fantasma.

## API

| Método | Rota | O que faz |
|---|---|---|
| GET/PUT | `/notificacoes/conta` | o bot e o interruptor de envio |
| GET | `/notificacoes/chats` | quem já falou com o bot (descoberta de id) |
| GET/PUT | `/notificacoes/destinos` | lista e cria/atualiza destino |
| DELETE | `/notificacoes/destinos/{id}` | remove destino |
| POST | `/notificacoes/testar?destino_id=` | manda teste (um destino, ou todos) |
| GET/PUT | `/notificacoes/regras` | lista (com catálogos) e cria/atualiza regra |
| DELETE | `/notificacoes/regras/{id}` | remove regra |
| GET | `/notificacoes/envios` | o que já foi mandado, por destino |

Todas em `users.manage`.

## Verificação

`python tests/verificar.py` — nove cenários só desta parte, incluindo um
**de ponta a ponta** que configura bot, dois destinos (grupo e pessoa) e
duas regras, e confere que cada evento chegou em quem devia:

| Cenário | Trava |
|---|---|
| roteia para os destinos certos | regras somam em vez de se anular; destino desligado fica fora |
| filtra por tipo e gravidade | tipo não marcado nunca passa, nem sendo crítico |
| espera antes de avisar | piscada não avisa; retorno não espera |
| mensagem curta e sem IP | ≤4 linhas, formato por tipo, sem endereço interno |
| não repete o mesmo evento | dedup por evento **e** destino |
| manda para dois destinos | um evento, dois envios rastreados |
| nunca derruba o ciclo | Telegram fora do ar registra falha e segue |
| token nunca aparece | nem em resposta de API, nem em mensagem de erro |
| ponta a ponta | o fluxo inteiro, com as funções que as rotas usam |
