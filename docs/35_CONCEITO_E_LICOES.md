# O conceito, e as lições que não podem se repetir

Este é o documento que se lê **antes de acrescentar qualquer coisa** ao
FaceOps. Ele registra duas coisas: o que o painel é (e o que
deliberadamente não é), e os erros que já custaram caro — para não
custarem de novo.

---

## O conceito, em uma frase

> Um painel de operação que é **consultado**, não vigiado; que **vigia**
> sozinho o suficiente para avisar; e que **jamais** pode ser motivo de
> lentidão no que monitora.

As três partes têm exigências diferentes, e confundi-las já produziu
defeito.

### As duas necessidades, separadas

| | Vigiar | Mostrar |
|---|---|---|
| Para quê | detectar queda, abrir incidente, avisar no Telegram | desenhar gráfico e cartão |
| Quando importa | **sempre**, sem ninguém olhando | só com a tela aberta |
| Cadência necessária | minutos — 5 min avisa igual a 1 min | segundos, para o gráfico ter pontos |

Durante muito tempo as duas foram servidas pelo **mesmo ciclo de 60 s**,
dimensionado para a tela. Isso significava que o painel **fechado**
gastava exatamente o mesmo que o painel aberto: 5.760 idas por dia e
outras tantas linhas no banco, para ninguém ver.

Hoje o coletor tem duas velocidades, e a troca é imediata nos dois
sentidos:

| Situação | Intervalo | Idas/dia (4 servidores) |
|---|---|---|
| alguém usando | 60 s | 5.760 |
| ninguém há 10 min | 300 s | 1.152 |

Abrir o painel **acorda o coletor na hora** — não se espera o resto do
intervalo longo — então a primeira tela vem com leitura fresca. E a tela
recebe do servidor de quanto em quanto tempo perguntar: buscar a cada
10 s um dado que só muda a cada 5 min é pedir trabalho para nada.

**O que não muda no modo econômico:** incidente continua sendo aberto e
fechado, aviso continua saindo, backup e faxina continuam no horário. Há
teste que falha se o ciclo passar a *pular* trabalho por causa do modo —
economia que desliga a vigilância é só desligar o painel.

---

## As regras que vieram de erro real

Cada uma custou um defeito em produção. A trava de cada uma está em
`backend/tests/verificar.py`.

### 1. Não afirmar o que não se leu

A regra mais cara do projeto, aprendida **cinco vezes**:

| O painel dizia | O que era |
|---|---|
| "Serviço travado" (8×) | 404/405 são resposta válida — o serviço estava de pé |
| "200 câmeras sem evento" | a consulta falhou; ninguém tinha olhado câmera nenhuma |
| "Não consegui ler a licença" (3×) | o manual define UM servidor de licença; os outros não têm mesmo |
| "acaba em 77 dias" | a projeção ignorava a retenção |
| página de erro 502 da Cloudflare dentro do cartão | falha de leitura exibida como se fosse o dado lido |

**A regra:** antes de escrever uma afirmação na tela, responda três
perguntas — *eu li isso? a leitura funcionou? a ausência prova o que eu
estou dizendo?* Se qualquer resposta for "não", a afirmação correta é
**"não verificado"**.

"Não encontrei evidência" é uma resposta legítima e frequentemente a
única honesta. A apuração de incidente devolve exatamente isso quando não
acha, e há teste garantindo que ela **não** inventa explicação.

### 2. Informar sem explicar não é informar

Também aprendida várias vezes, do mesmo jeito:

* `carga em 1.16 por núcleo` — jargão; virou "CPU sobrecarregada — 1,16
  processo por núcleo (o normal é abaixo de 1,00)" mais o que isso causa.
* `findface-video-worker` — não diz nada a quem é acordado às 3h; hoje o
  aviso explica o que aquele serviço faz e o que para sem ele.
* `[Errno 111] Connection refused` — correto e inútil. Recusado, timeout
  e negado têm causas **opostas** e a mesma cara; mandar conferir a rede
  quando o problema é o `sshd` parado custa o dobro do tempo.
* `3 serviço(s) com problema` — a mesma frase para container morto e para
  container de pé respondendo errado.

**A regra:** todo texto que aponta problema responde três coisas — *o
que é, o que isso significa na prática, e o que fazer.*

### 3. Ação sem resultado observável parece defeito

O botão "Atualizar" do Monitor funcionava. Como os números não mudavam,
parecia quebrado. Hoje mostra "atualizado às HH:MM:SS" e desabilita
enquanto busca.

Mesma família: tabela vazia que não diz o que o vazio significa, e prévia
de faxina que mostrava 4 de 11 categorias — quem via zero concluía que
nada seria removido.

### 4. Não duplicar o que já existe em outra tela

Duas telas contando a mesma coisa divergem na primeira alteração. O
histórico de um serviço sai da tabela de incidentes que já existe; a
matriz de perfis vem do mesmo catálogo que autoriza; quem parou um
serviço fica na Auditoria, que já tem busca — a aba de histórico só
aponta para lá.

### 5. Nada fica sem prazo

Toda tabela que cresce tem retenção, e a faxina diária aplica. "Pouco
para sempre" continua sendo para sempre.

Já falharam aqui: `licenca_amostras` nasceu sem prazo; a linha de
`backup_runs` ficava eterna enquanto só o texto do log era apagado; e a
prévia da faxina escondia sete das onze categorias.

### 6. O painel não pode pesar no que monitora

Detalhado em [34_PESO_DO_PAINEL](34_PESO_DO_PAINEL.md). Em resumo: uma
ida por servidor por ciclo, leitura de arquivo virtual (`/proc`), nada
caro no ciclo, log de produção só de serviço com incidente aberto, e o
resumo da tela cacheado por ciclo.

E a lição mais desconfortável: **o backup do próprio painel era candidato
a derrubar o servidor** que ele protege — rodava em prioridade normal de
E/S contra um disco com teto de IOPS. Hoje roda em `ionice -c3`, e a
apuração de incidente **acusa o próprio painel** quando a janela coincide
com um backup.

### 7. Falhar fechado no que é segurança

A `SECRET_KEY` de exemplo subia em produção sem nenhuma verificação — e
ela assina o token *e* deriva a chave do cofre com as credenciais SSH.
Hoje o painel **recusa subir**.

Um painel de pé com a chave de exemplo é pior que um fora do ar: ele
parece funcionar.

### 8. Teste que se satisfaz com comentário não guarda nada

Aconteceu **três vezes** nesta base, e é sutil:

| Trava | Passava por causa de |
|---|---|
| cerca do projeto compose | a **docstring** da função, que cita a cerca para explicá-la |
| `ionice` no backup | a primeira aparição de `pg_dump` no script — um **comentário** |
| ausência de SSH no histórico | a docstring, que fala de SSH para dizer que não usa |

**A regra:** trava que lê código-fonte inspeciona o **corpo**, nunca o
texto; e verifica a linha que **executa**, não a primeira que menciona.

E acima de tudo: **toda trava nova é verificada por injeção.** Quebra-se
o comportamento de propósito e confirma-se que o teste falha, com a
mensagem certa. Trava não verificada é decoração.

### 9. O repositório fala do produto, e de mais nada

Nada do que está versionado carrega marca de ferramenta de desenvolvimento
— nem em código, nem em documento, nem em nome de diretório.

Não é preferência estética. Este painel é entregue a um cliente público e
auditado por terceiros: o que está no repositório tem de falar do FaceOps
e do FindFace. Quem escreveu é decisão de quem assina o projeto, não
pegada deixada por acidente.

O conhecimento que vivia em arquivos de ferramenta foi consolidado em
[36_REFERENCIA_RAPIDA](36_REFERENCIA_RAPIDA.md) — nada se perdeu, e agora
está num lugar que qualquer pessoa lê.

**Trava:** `projeto sem marca de ferramenta` varre tudo o que o `git`
rastreia, conteúdo e caminho.

---

## Antes de acrescentar qualquer coisa

Seis perguntas, nesta ordem:

1. **Isso já existe em outra tela?** Se sim, ajuste lá.
2. **Custa o quê, e com que frequência?** Se entra no ciclo, tem de ser
   leitura de arquivo virtual. Se é caro, é sob demanda e com teto.
3. **O que grava, e por quanto tempo?** Sem retenção, não entra.
4. **O que a tela vai afirmar, e eu realmente li isso?** Se não,
   "não verificado".
5. **Como isso quebra em silêncio, e qual teste pega?** Escreva a trava
   e prove que ela pega, injetando o defeito.
6. **Sobra alguma marca de ferramenta no que vou versionar?** Não pode.

---

## O que o painel deliberadamente NÃO é

| Não é | Por quê |
|---|---|
| Substituto do Zabbix | o Zabbix vigia infraestrutura; este painel opera o FindFace. Convivem no mesmo grupo do Telegram, e por isso o aviso assina a origem |
| Shell remoto disfarçado | comando livre existe no InTerminal, que **grava** a sessão. Ação rápida é catálogo fixo |
| Ferramenta com perfis editáveis por tela | quem edita perfil se concede acesso root sem passar por ninguém |
| Painel de parede 24×7 | é consultado; daí o modo econômico e a sessão que expira parada em 20 min |
| Lugar de modelo de linguagem | ver [27_DIAGNOSTICO](27_DIAGNOSTICO.md): a máquina do painel não tem folga para isso, e regra + estatística cobrem o caso |
