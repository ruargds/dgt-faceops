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
| **Quando vale** | dias da semana e faixa de horário (o *time period* do Zabbix) |

**Regras diferentes valem ao mesmo tempo.** Uma mais específica não anula
as outras: o plantão pode receber tudo, e o dono de um serviço receber só
o dele. Cada uma manda para o seu destino.

### Tipos de evento

| Tipo | Quando |
|---|---|
| 🔴 **Serviço parado** | container fora do ar, unhealthy, morto por OOM ou reiniciando em laço |
| ⛔ **Servidor sem comunicação** | a máquina não respondeu ao coletor — costuma ser rede, não Face Detect |

A chave interna do tipo continua `host_sem_contato`: é ela que está
gravada nas regras já criadas, e renomeá-la faria toda regra existente
parar de casar. O que mudou é o texto que se lê.
| 🟢 **Voltou ao normal** | o que estava fora voltou |
| 🟡 **Limite de recurso** | disco, memória, swap, carga, VRAM ou temperatura da GPU acima do limiar |

### "Avisar depois de" — o `for:` do Prometheus

Um serviço que pisca por 20 segundos e volta não deveria acordar ninguém.
Com espera de 5 min, o aviso só sai se o problema **persistir** — o ciclo
reavalia a cada passada e a deduplicação garante um envio só.

O **retorno ao normal nunca espera**: boa notícia não tem por que atrasar.

### Dia da semana e horário — o `time period` do Zabbix

Cada regra escolhe **quando vale**: dias da semana e uma faixa de
horário. Fora dela, a regra não manda nada — nem a abertura, nem o
retorno.

É o que permite montar o desenho que toda operação com plantão acaba
precisando, com duas regras para o mesmo grupo:

| Regra | Dias | Horário | Gravidade mínima |
|---|---|---|---|
| Comercial | seg–sex | 08:00–18:00 | atenção e parada |
| Plantão | todos | 22:00–06:00 | só quando parar |

Três detalhes que importam:

* **Sem dia e sem horário marcado, a regra vale sempre.** É o padrão, e é
  o que mantém funcionando, sem migração, toda regra criada antes disto
  existir.
* **A janela pode cruzar a meia-noite.** `22:00–06:00` é uma janela só,
  não duas — que é justamente o turno da madrugada.
* **A hora é a local do painel**, a mesma que a pessoa leu ao configurar.

### O retorno depende da abertura ter saído

Gravidade mínima e espera valem só para a abertura — mas, quando um
desses filtros barrava a abertura, o retorno saía assim mesmo. O grupo
recebia "voltou a funcionar" de um problema que nunca foi anunciado.

Aconteceu de verdade com a vigilância de disco, que abre como **atenção**
e não passava numa regra de "só quando parar (crítico)": a cada episódio
chegava só o "✅ Resolvido", sem nunca o aviso correspondente.

Agora o retorno confere se a abertura chegou **naquele destino** antes de
sair. É como Zabbix e Alertmanager amarram o par, e pela mesma razão:
resolução sem alerta não informa, confunde.

## Como a mensagem chega

O formato é **deliberadamente o mesmo do template de Telegram do
Zabbix** que a equipe já lê todo dia. Copiar não é falta de ideia: quem
está de plantão não devia ter de aprender dois formatos para ler o mesmo
grupo, e três detalhes daquele template resolvem problemas reais:

| Detalhe | Por que existe |
|---|---|
| `ícone - Rótulo: valor` | o ícone dá a varredura visual, o rótulo dá o significado. Só ícone obriga a decorar legenda; só texto obriga a ler tudo |
| linha em branco entre campos | no cliente de Telegram as linhas ficam coladas; sem o respiro a mensagem vira um parágrafo cinza |
| ícone dobrado no resolvido (`✅✅`) | deixa a boa notícia reconhecível na rolagem, sem ler |
| segundos no horário e na duração | dois avisos no mesmo minuto são indistinguíveis sem eles, e "12m 0s" afirma o que "12min" deixa em dúvida |

### A primeira linha assina a origem

```
🤖 FaceOps · DGT
```

Não é enfeite. **No mesmo grupo caem avisos do Zabbix e do FaceOps**, e o
caminho de resolução é diferente em cada caso — quem lê precisa saber a
origem antes de decidir o que fazer. O nome do cliente vem de
`projeto.cliente` (Configurações → Identidade do projeto): o **mesmo
campo** que já nomeia o painel e a aba do navegador, para não haver dois
lugares dizendo quem é o cliente. Vazio, sai só `🤖 FaceOps`.

### Serviço parado

```
🤖 FaceOps · DGT

🔴 - vm-appserver (Aplicação) - 🔴

⚠️ - Problema: o serviço findface-video-worker parou de funcionar

💬 - Significa: É ele que processa o vídeo das câmeras. Enquanto estiver
     fora, este servidor não reconhece ninguém.

🔎 - Provável: reiniciou 7x nos últimos 30 min

🛠 - Fazer: Em Serviços, abra o log deste container.

⏳ - Iniciado em: 02/09 14:32:07 (há 6m 20s)

⚡ - Gravidade: Crítico
```

### Voltou ao normal

```
🤖 FaceOps · DGT

✅✅ - vm-appserver (Aplicação) - ✅✅

✅ - Resolvido: findface-video-worker voltou a funcionar

⏱ - Duração: 6m 20s

🕐 - Horário: 02/09 14:38:27
```

### A linha "Significa" — o que faltava

`findface-video-worker` não significa nada para quem recebe o aviso às 3h
da manhã. "Serviço parado" informava sem explicar: não dizia **o que
deixa de acontecer**, que é a única coisa que decide se alguém levanta da
cama.

A fonte é o catálogo do manual da NtechLab que
`internos_service.COMPONENTES` já mantinha para sondar as portas — cada
componente ganhou um campo `impacto`, e `descrever()` resolve o nome:

| O que chega | O que a linha diz |
|---|---|
| `findface-video-worker` | processa o vídeo das câmeras; sem ele o servidor não reconhece ninguém |
| `findface-ntls` | serviço de licença; em algumas horas o reconhecimento inteiro para |
| `findface-multi-postgresql-1` | banco principal; sem ele não há login, consulta nem gravação |
| `findface-multi-mongodb-1` | guarda as imagens; passagens continuam sendo detectadas, sem foto |

**Um catálogo só, não dois.** Uma segunda lista de nomes amigáveis noutro
módulo divergiria da primeira na próxima versão do Face Detect. O nome do
container também não é o nome do serviço no compose
(`findface-multi-postgresql-1` contra `postgresql`), então a busca aceita
as duas formas, do mais específico para o mais genérico. Serviço
desconhecido **não** ganha descrição inventada: a linha simplesmente não
aparece.

### O apelido do servidor vai no aviso

Quando o servidor tem apelido (Servidores → Identificação), é ele que
aparece no cabeçalho — em todos os quatro tipos de evento. Quem recebe o
aviso quer saber **onde é**, não como a VM se chama. O papel entra entre
parênteses, porque "vm-dbserver" só diz algo para quem convive com os
nomes.

A regra continua casando por `host_id`, nunca por nome: casar por nome
faria toda regra parar de valer no dia em que alguém trocasse o apelido.
Há teste para isso.

### Alertas de recurso, em vez de jargão

Cada limite passou a dizer o número **e** o que ele significa:

| Antes | Agora |
|---|---|
| `carga em 1.16 por núcleo` | `CPU sobrecarregada — 1.16 processo por núcleo (o normal é abaixo de 1,00)` + "há processo esperando a vez de usar o processador; nada parou, mas tudo responde mais devagar" |
| `memória em 93%` | `memória em 93% — 14.9 GB de 16 GB em uso` + "o sistema começa a encerrar serviços para liberar memória" |
| `swap em 60%` | `swap em 60% — a máquina está usando disco como se fosse memória` + "disco é muito mais lento; é sinal de VM pequena para a carga" |
| `3 serviço(s) com problema` | um aviso por serviço, com o que aconteceu (`parou com erro`, `de pé mas respondendo com falha`, `reiniciando em laço`, `encerrado por falta de memória`) |

"com problema" era verdadeiro e inútil: dava a mesma frase para container
morto e para container de pé respondendo errado — dois problemas com
urgência e solução diferentes.

O campo `significa` **também aparece no Monitor**, acima da ação. Dado que
só o Telegram vê seria dado que a tela deixou de explicar.

Texto puro, sem Markdown: nome de container tem `_`, `-` e `.`, que
quebram o parser do Telegram e fariam a mensagem falhar justamente
durante um incidente. E **sem endereço interno** — IP de servidor não vai
para um grupo de mensagens.

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
| mensagem tem campos e assina a origem | assinatura no topo, campo por linha com respiro, sem endereço interno |
| duração no formato do Zabbix | `4d 18h 50m 42s`; zero no meio não desaparece |
| aviso explica o serviço | catálogo do manual alimenta a linha "Significa"; nome de container casa com nome de serviço |
| não repete o mesmo evento | dedup por evento **e** destino |
| manda para dois destinos | um evento, dois envios rastreados |
| nunca derruba o ciclo | Telegram fora do ar registra falha e segue |
| token nunca aparece | nem em resposta de API, nem em mensagem de erro |
| ponta a ponta | o fluxo inteiro, com as funções que as rotas usam |


## Consumo subindo sem parar (2026)

Tipo de evento novo: **`crescimento`**. Ele não avisa que um limite foi
ultrapassado — avisa que, no ritmo medido, ele **vai** ser, e diz em
quanto tempo, quem está empurrando e o que fazer. É o único aviso do
painel que chega antes do estrago; ver
[38_CRESCIMENTO_E_VAZAMENTO](38_CRESCIMENTO_E_VAZAMENTO.md).

Duas coisas para saber antes de recebê-lo:

* **Nas regras que já existiam, ele entra marcado.** Ninguém poderia ter
  marcado uma caixa que não existia quando a regra foi criada, e o aviso
  ficaria mudo para sempre em toda instalação já em uso. Quem não quiser é
  uma caixa a desmarcar aqui em Avisos.
* **Ele não repete a cada ciclo.** Enquanto a vigilância está aberta, só
  volta a avisar se a situação **piorar de nível**. Quando ela fecha, sai
  um retorno dizendo que o consumo parou de subir — pela mesma razão de
  sempre: saber que passou evita alguém sair de casa por um problema que
  já se resolveu.
