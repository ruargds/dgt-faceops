# Crescimento — o consumo que sobe sozinho e derruba o servidor

Um servidor de reconhecimento facial não costuma cair de uma vez. Ele
**enche**. Às 22h a memória estava em 40% e não havia nada para ver; às 3h
o kernel matou o `findface-video-worker` e as câmeras daquele worker
pararam de reconhecer — sem erro em tela, só ausência de evento.

O painel tinha três respostas e faltava a do meio:

| Pergunta | Onde já era respondida |
|---|---|
| o que está fora do limite **agora**? | alertas do Monitor |
| o que **quebrou**? | Rastreio |
| o que **causou** a queda? | Apuração, no fechamento do incidente |
| **isto está subindo e vai me derrubar quando?** | *não havia* |

A diferença entre a última e as outras é que só ela chega a tempo. Limiar
dispara quando o estrago está perto; apuração explica depois que
aconteceu.

---

## As três perguntas, nesta ordem

A ordem é o desenho inteiro, e ela existe por causa de custo.

### 1. Está subindo? — de graça

Sai das amostras que o coletor **já** gravou. Nenhuma ida nova ao
servidor, nenhum comando. A cada ciclo, para cada host monitorado, o
painel ajusta uma reta à janela (padrão 6h) de memória, disco e swap.

Três formas possíveis, e a diferença entre elas muda o diagnóstico:

| Regime | O que é | O que costuma ser |
|---|---|---|
| `linear` | sobe sempre no mesmo ritmo | acúmulo normal (foto de evento, log) |
| `acelerando` | a segunda metade da janela sobe pelo menos 2× mais que a primeira | vazamento, laço de reinício, câmera problemática |
| `serrote` | sobe, cai de uma vez, sobe de novo | algo reinicia e devolve o recurso — a pergunta vira "por que reinicia" |

`acelerando` é o que o pedido chama de exponencial. Só nesse caso o painel
fala em **tempo de dobra**, e só quando o ajuste exponencial explica a
série melhor que a reta — anunciar "dobra a cada 2h" a partir de uma nuvem
de pontos seria repetir o defeito do "acaba em 77 dias".

### 2. Quando estoura, e o que quebra? — de graça

Projeção da taxa medida até o teto do recurso. O teto **não** é o limiar
de alerta: limiar é quando avisar, teto é onde quebra.

| Recurso | Teto | O que acontece lá |
|---|---|---|
| memória | 95% | o kernel mata o container com mais memória. O serviço morto para sem avisar — as câmeras dele deixam de ser reconhecidas e **não aparece erro**, só ausência de evento |
| disco | 95% | o banco para de gravar e as passagens deixam de ser registradas. O reconhecimento segue rodando e nada é salvo |
| swap | 90% | nada quebra: fica lento. Em disco com teto de IOPS, ainda concorre com o banco |

### 3. Quem está empurrando? — aqui começa a custar

Duas fontes, e a primeira é de graça:

**Série por container** (memória). O ciclo do monitor já roda
`docker stats` a cada passada para desenhar os cartões, e o resultado era
descartado. Agora é gravado — sem comando novo, sem SSH novo. Com dois
pontos no tempo, "quem está com a memória" vira "quem **ganhou** 900 MB
nas últimas duas horas", que é outra pergunta e a única que acusa alguém.

**Rastreio seletivo** (disco, e o que a série não vê). UMA execução SSH,
só leitura, com `timeout` em cada comando e prioridade baixa de E/S
(`ionice -c3 nice -n19`) — porque num disco com teto de IOPS o
diagnóstico não pode virar a causa do próximo incidente
(ver [33_SATURACAO_DE_DISCO](33_SATURACAO_DE_DISCO.md)).

| O que ele lê (disco) | Por que |
|---|---|
| `df` e `df -i` | ocupação e **inodes** — milhões de fotos pequenas acabam com o inode antes do byte |
| `du -sb` de uma lista curta e fixa | `/var/log`, `/var/lib/docker/containers`, `data/` do Face Detect e o staging do próprio painel |
| `journalctl --disk-usage` | instantâneo, e o journal é suspeito recorrente |
| `find -newermt -size +50M` | arquivo **grande que mudou na janela** — quem está crescendo agora |
| `lsof +L1` | arquivo apagado que um processo mantém aberto: o `du` não acha e o `df` continua cheio |

| O que ele lê (memória) | Por que |
|---|---|
| `docker stats` e `docker inspect` | uso por container e quem roda **sem limite de memória** |
| `ps --sort=-rss` + cgroup | processo fora de container, e o container dono de cada PID |
| `df -t tmpfs` | tmpfs ocupa **memória**, não disco — arquivo esquecido em `/dev/shm` some do `ps` |
| journal/`dmesg` filtrado por OOM | prova de que o kernel já matou alguém |

**A lista de caminhos é fixa de propósito.** Varrer o disco inteiro com
`du` custaria minutos de E/S em produção, exatamente quando ele já está
sob pressão.

---

## Vigilância: a busca persistente depois do alerta

Um pico não abre nada. Backup começando, câmera religando e cache do
kernel produzem subidas curtas que passam sozinhas — e vigilância que
abre e fecha a cada ciclo é ruído com cara de diagnóstico.

```
ciclo do monitor (60 s / 300 s no modo econômico)
   │
   ▼
tendência da janela ──── não preocupa ──▶ contador zerado
   │ preocupa
   ▼
3 ciclos seguidos confirmando  ──▶  abre a vigilância
   │                                      │
   │                                      ├── acusa pela série de containers (grátis)
   │                                      └── rastreia no servidor (1 SSH)
   ▼
enquanto durar: re-rastreia a cada 30 min, compara com o rastreio anterior
   │
   ▼
parou de subir  ──▶  fecha sozinha (estabilizou / recuou / estourou)
```

**Abrir pede a janela inteira; manter aberta pede o presente.** São
réguas diferentes de propósito: se a decisão de fechar olhasse as mesmas
6 horas, alguém resolveria o problema às 14h e a tela continuaria
acusando até as 20h — que é a definição de alarme em que ninguém
acredita. Enquanto a vigilância está aberta, quem decide é o último terço
da série.

**Servidor sem contato não fecha vigilância.** Sem alcançar a máquina não
se sabe se o consumo caiu ou se só parou de ser medido, e fechar ali
registraria uma melhora que ninguém observou. É a mesma regra do
incidente de serviço.

Cada vigilância guarda a série medida e o último rastreio, então a
conclusão pode ser conferida em vez de acreditada. Retenção própria:
**90 dias** (`crescimento.retencao_dias`), mais longa que a dos
incidentes porque "este disco já encheu antes, e por causa de quê?" é
pergunta de mês.

---

## O que impede isto de virar alarme falso

Cinco travas, cada uma de um jeito de errar conhecido:

1. **Poucos pontos não desenham tendência.** Abaixo de oito, a resposta é
   "indeterminado" — nunca uma reta puxada de três amostras.
2. **Nuvem de pontos não é subida.** R² abaixo de 0,70 (sem ajuste
   exponencial bom) vira "os pontos não formam tendência", e não um
   número com cara de certo.
3. **Reinício não é vazamento.** A série é cortada nas quedas bruscas e a
   análise usa só o trecho de depois.
4. **Cache não é uso.** A amostra grava `total - MemAvailable`, então
   buffer e cache cheios não viram alarme.
5. **Crescimento esperado não é defeito.** Foto de evento acumula porque
   gente passa na frente da câmera — ali o que se decide é retenção.

A tela repete os três últimos itens em texto, porque "cache alto" já fez
gente trocar de VM sem precisar.

---

## O catálogo: por que cresce, o que o fabricante manda fazer

`catalogo_crescimento.py` é a versão executável deste documento. Cada
entrada casa por **sinal explícito** — regex de caminho, regex de nome de
container — e responde quatro coisas: por que cresce, o dano, o contorno
e **o que a NtechLab recomenda**, quando ela recomenda algo.

| Caso | Por que cresce | Recomendação do fabricante |
|---|---|---|
| `/var/log` | log de acesso HTTP do Face Detect em operação normal: 8 GB/dia num servidor deste ambiente | `SystemMaxUse=3G` no journald e driver `journald` no daemon.json (`logs.html`) — é o que a contenção de Manutenção aplica |
| `/var/lib/docker/containers` | driver `json-file` sem `max-size`: o arquivo só cresce | trocar para o driver `journald` (`logs.html`); exige reiniciar o Docker, então é janela de manutenção |
| `data/` (eventos e fotos) | cada passagem grava foto, miniatura e quadro | `manage.py cleanup` com prazo por tipo de evento e `CLEANUP_SCHEDULE` (`event-cleaner.html`) — e nada de reiniciar container durante a purga |
| `data/findface-tarantool-server` | base biométrica: cadastro + snapshots, em 16 shards e 16 réplicas | **não há** recomendação de limpeza. A purga de eventos não libera este espaço |
| Postgres / Mongo | eventos vivem no banco antes de virar arquivo | reduzir pela retenção do Face Detect, não mexendo no banco por fora |
| staging do painel | execução de backup interrompida deixou artefato | é problema **nosso** — faxina pontual, categoria "Sobras de staging" |
| arquivo apagado e aberto | `rm` num log que o rsyslog mantém aberto | comportamento do Linux; use `truncate -s 0`, nunca `rm` |
| inodes | milhões de arquivos pequenos | não documentado pelo fabricante |
| `findface-video-worker` | memória por stream; câmera problemática custa mais | o manual dimensiona por stream (`architecture.html`) e **não** documenta vazamento — tratar como dimensionamento antes de tratar como bug |
| `findface-extraction-api` | modelo e cache na subida | até 45 min de aquecimento na PRIMEIRA subida é normal, e está no manual |
| container sem `mem_limit` | cresce até acabar a RAM da máquina | o compose da NtechLab não define teto; alterá-lo é escolha nossa, sem respaldo |

A coluna do fabricante diz "não documentado" quando é o caso. Vestir
prática nossa de recomendação oficial é o tipo de erro que só aparece no
dia em que alguém abre chamado citando o painel.

---

## A tela

**Crescimento**, no grupo Monitoramento. Quatro blocos:

1. **Os três recursos** — valor atual, ritmo, projeção até o teto e o
   dano previsto. Recurso que não está subindo diz isso, com o motivo.
2. **Memória por container, num gráfico só** — todas as linhas juntas,
   para comparar quem sobe contra quem fica parado. Clicar na legenda
   esconde; passar o mouse destaca.
3. **Um container por vez** — tabela com agora, mínimo, média, pico,
   variação na janela, ritmo em MB/h, CPU e número de amostras; "ver
   gráfico" abre o container sozinho, com memória e CPU em gráficos
   separados (MB e % não dividem escala) e o período realmente coberto.
4. **Vigilâncias abertas** — culpado, por que ele cresce, o que fazer, o
   que o fabricante recomenda, e a evidência do rastreio.

### O seletor de período

O controle de tempo é o de qualquer painel de série, e serve às duas
perguntas que aparecem de verdade:

| Quero | Como |
|---|---|
| "as últimas N horas", acompanhando o tempo passar | atalhos: 1h, 6h, 12h, 24h, 2d, 7d, 30d, 90d, 6m, 1a |
| "a madrugada de terça", parada onde está | **Período…** → início e fim exatos |
| "e antes disso?" | `‹` e `›` andam uma janela do mesmo tamanho |

Andar para trás converte a janela em intervalo absoluto — "as 6 horas
anteriores a estas 6 horas" só existe com âncora. Voltar ao presente é
clicar num atalho.

**Atalho além do que o banco guarda aparece marcado com `·` e apagado**,
e o title diz desde quando há dado e qual é a retenção. Oferecer "1 ano"
e desenhar sete dias faria a tela mentir por omissão: a série por
container vive 7 dias e as amostras do host, 30 — números de custo, não
defeito. O servidor devolve `mais_antiga` justamente para a tela poder
dizer isso.

Quando o período escolhido não tem gravação nenhuma, a resposta é "não
há gravação por container neste período — o mais antigo que existe é de
X", e não um gráfico vazio, que se lê como "o servidor ficou parado".

### O gráfico

Desenhado na largura real do elemento (medida com `ResizeObserver`), e
não esticado a partir de um `viewBox` fixo — que era o que deformava
texto e ponto. A altura acompanha a largura até um teto: numa tela de
1080p ele cresce em vez de virar uma tira; no celular, não estoura a
dobra.

#### A escala tem de caber nos valores

Num servidor deste ambiente o `findface-multi-legacy` fica em **17,7 GB**
e o `healthcheck` em **6 MB** — quase 3.000 vezes de diferença. Numa
escala linear compartilhada, uma linha usa o gráfico inteiro e as outras
vinte e cinco viram um risco no chão: tecnicamente correto e inútil para
a pergunta que se está fazendo.

Quatro modos, e o padrão decide sozinho:

| Modo | O que faz | Quando serve |
|---|---|---|
| **auto** (padrão) | linear até 50x de razão, log acima disso | quase sempre |
| **linear** | proporção real | séries de tamanho parecido |
| **log** | cada faixa do eixo é 10x a anterior | 17 GB e 6 MB no mesmo gráfico |
| **variação** | cada série menos o próprio início do período | a pergunta desta tela: quem **cresceu** — quem está parado fica em zero |

Com os valores reais acima, o eixo em log põe as marcas em 1 MB, 10 MB,
100 MB, 1000 MB, 9,8 GB e 19,5 GB, e as linhas passam a ocupar de 18% a
99% da altura — em vez de todas empilhadas no primeiro por cento.

Dois cuidados no log: o topo sobe até o próximo 1, 2 ou 5 acima do pico
(arredondar para a década cheia jogaria metade do eixo no vazio e
achataria tudo de novo), e o piso fica na década do menor valor positivo,
com teto de cinco décadas. Valor zero encosta no piso em vez de sumir — e
a legenda continua mostrando o número exato.

Três detalhes que mudam a leitura:

* **eixo com marcas redondas** (1, 2 ou 5 vezes potência de dez). "3847
  MB" no meio do eixo é número que ninguém usa para comparar;
* **eixo de tempo adaptativo** — minuto, hora, dia ou mês, escolhido pelo
  tamanho da janela, com a data aparecendo na virada do dia;
* **buraco de coleta não vira reta.** Intervalo maior que 2,5x a cadência
  da série interrompe a linha: o painel esteve fora, e ligar os dois
  pontos inventaria uma medição que ninguém fez.

O cursor mostra os valores daquele instante ordenados do maior para o
menor — é a pergunta que se faz com muitas linhas na tela: "às 3h, quem
estava por cima?". Ponto distante demais do instante apontado não entra
na leitura, pelo mesmo motivo do buraco.

O cursor acompanha o mouse na vertical, preso dentro do gráfico: fixo no
topo, ele cobria justamente as linhas de cima, que são as que se está
olhando.

A tabela ordena por qualquer coluna, clicando no cabeçalho ou nos botões;
clicar de novo inverte, e a seta diz qual está valendo.

**Tudo isso lê o banco.** Abrir a tela e trocar o período não tocam em
servidor nenhum. Só "Rastrear agora" abre SSH, e por isso é botão.

---

## O custo, item por item

| Item | Custo |
|---|---|
| Detecção de tendência | uma consulta indexada por host por ciclo, sobre colunas numéricas |
| Série por container | **nenhum** comando novo — o `docker stats` do ciclo já existia. Grava a cada 5 min (`containers.intervalo_min`), no máximo 60 containers por passada |
| Rastreio automático | 1 execução SSH por vigilância aberta, a cada 30 min, no máximo **uma por passada do monitor** |
| Linhas no banco | ~34 mil/dia de série por container (4 servidores, 30 containers, 5 min), retidas 7 dias. Vigilância: uma linha por episódio |
| Faxina | duas categorias novas, com retenção própria e prévia na tela |

Desligar sem perder tudo: `crescimento.rastrear_sozinho` desliga só o SSH
automático (a detecção continua, e o rastreio vira clique);
`containers.historico_ativo` desliga só a série por container.

---

## O aviso no Telegram

Tipo de evento novo, **Consumo subindo sem parar** (`crescimento`). A
previsão vai no texto do problema, e não numa linha extra, porque é ela
que faz alguém agir e precisa caber na primeira linha da mensagem:

```
🎥 FaceOps · PROCERGS

📈 - VM-APPSERVER-01 (Aplicação) - 📈

⚠️ - Problema: memória em 78.4%, subindo 4.20 pontos por hora — chega a
     95% em 3h 56min

💬 - Significa: Ao encostar no limite, o kernel mata o container que
     estiver com mais memória — hoje o maior é
     findface-multi-findface-video-worker-1. O serviço morto para sem
     avisar…

🔎 - Provável: findface-multi-findface-video-worker-1: cada stream de
     câmera custa memória, e câmera problemática custa mais…

🛠 - Fazer: Reiniciar o worker devolve a memória e é seguro…

⚡ - Gravidade: Atenção
```

Quando a vigilância fecha, sai um retorno dizendo que parou de subir e em
quanto ficou.

**Nas instalações que já tinham regra de aviso, este tipo entra
marcado.** É uma decisão explícita: ninguém pode ter marcado uma caixa
que não existia quando a regra foi criada, e justo o aviso que chega
antes do limite ficaria mudo para sempre. Quem não quiser tem uma caixa a
desmarcar em **Avisos**.

---

## Configurações

Todas em **Configurações → Monitoramento contínuo**:

| Chave | Padrão | O que decide |
|---|---|---|
| `crescimento.ativo` | ligado | vigiar ou não |
| `crescimento.janela_h` | 6 | quanto tempo para trás a tendência é calculada |
| `crescimento.horizonte_h` | 72 | projeção dentro deste prazo vira vigilância |
| `crescimento.mem_pp_por_h` | 2 | subida mínima de memória que vale atenção |
| `crescimento.disco_pp_por_dia` | 1 | o mesmo para disco — a escala dele é por dia |
| `crescimento.ciclos_para_abrir` | 3 | quantas confirmações antes de avisar |
| `crescimento.rastrear_sozinho` | ligado | rastrear o culpado sem clique |
| `crescimento.rastrear_a_cada_min` | 30 | intervalo do re-rastreio |
| `crescimento.retencao_dias` | 90 | histórico de vigilâncias encerradas |
| `containers.historico_ativo` | ligado | guardar memória por container |
| `containers.intervalo_min` | 5 | cadência da gravação |
| `containers.retencao_dias` | 7 | quanto tempo a série vive |

---

## API

| Rota | Custo | Devolve |
|---|---|---|
| `GET /api/crescimento/analise/{host_id}?horas=` ou `?de=&ate=` | banco | tendência, projeção e dano dos três recursos, vigilâncias abertas e culpados de memória |
| `GET /api/crescimento/containers/{host_id}?horas=&limite=` ou `?de=&ate=` | banco | uma série por container, ordenada por quem mais cresceu |
| `GET /api/crescimento?host_id=&dias=` | banco | vigilâncias abertas e encerradas na janela |
| `GET /api/crescimento/{id}` | banco | uma vigilância com o rastreio inteiro |
| `POST /api/crescimento/{id}/rastrear` | **1 SSH** | rastreia agora; só em vigilância aberta |
| `POST /api/crescimento/rastrear/{host_id}?recurso=` | **1 SSH** | rastreio avulso, sem vigilância |

Todas exigem `metrics.view`. Nenhuma altera estado no servidor.

`de` e `ate` são ISO 8601 e **mandam** quando vêm — `horas` é o atalho
para o mesmo par. Data inválida devolve 400 explicando o formato, em vez
de série vazia; fim antes do início e período acima de 400 dias também.
A janela relativa nunca passa do instante atual: sem esse teto, carimbo à
frente do relógio viraria linha no gráfico.

---

## Verificação

| Cenário | Trava |
|---|---|
| `crescimento distingue linear de exponencial` | os três regimes, e "dobra" só onde o exponencial explica melhor |
| `crescimento nao inventa tendencia` | poucos pontos, reinício no meio da série e serrote |
| `crescimento projeta o estouro e diz o dano` | a conta da projeção, o que não se projeta, e o dano escrito em operação |
| `crescimento abre e fecha vigilancia sozinha` | uma leitura não abre; estabilizou, fecha |
| `crescimento acusa quem cresceu nao quem e grande` | atribuição por diferença entre medições, nos dois caminhos (disco e container) |
| `periodo absoluto manda e nao inventa dado` | absoluto ganha do relativo, janela relativa não lê o futuro, intervalo invertido não vira série vazia, e o período sem coleta diz desde quando há dado |
| `rastreio de crescimento so le` | nenhum comando que altera estado; `ionice`, `nice` e `timeout` presentes; `du` estourado vira "não medido" |
| `faxina apaga vigilancia fechada so` | vigilância aberta é estado atual, não histórico |

---

## O que ele deliberadamente não faz

- **Não age.** Não reinicia container, não apaga arquivo, não muda
  configuração — nem quando a projeção diz que a máquina cai em duas
  horas. Diagnóstico que age sozinho é alarme de incêndio que abre a
  janela.
- **Não afirma causa.** O rastreio aponta quem cresceu e o catálogo
  explica por que aquilo costuma crescer. Correlação apontada como causa
  é o erro que este projeto já cometeu quatro vezes.
- **Não varre o servidor.** A lista de caminhos é curta e fixa, e cada
  comando tem teto de tempo.
- **Não prevê o que não mediu.** Sem série, sem projeção — e a tela diz
  qual das duas está faltando.
