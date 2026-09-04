# Invariantes do FaceOps

O que precisa continuar verdadeiro. Cada um nasceu de um defeito real ou
de uma decisão que custou discussão — não são princípios genéricos.

Coluna **Trava**: o cenário em `backend/tests/verificar.py` que falha se o
invariante for quebrado. Invariante sem trava é intenção, não contrato.

---

## 1. Honestidade da interface

### INV-1 — A tela nunca afirma sobre ausência de dado

Falha de leitura e ausência do fato **não podem** aparecer iguais. Toda
leitura que pode falhar carrega o motivo (`erros`) e um sinal de que
falhou (`falhou`), e a interface troca a afirmação por "não verificado".

*Por quê:* quatro defeitos distintos vieram daqui — "Serviço travado" (8x),
"200 câmeras sem evento", "Não consegui ler a licença" (3x). Alarme falso
permanente ensina a ignorar a tela, o que é pior que não ter alarme.

**Trava:** `camera sem evento nao mente quando a leitura falha`,
`sonda 404 405 nao e servico travado`

### INV-2 — Resposta HTTP é sinal de vida, não veredito de saúde

Qualquer código diferente de `000` prova que o componente respondeu.
404 e 405 são resposta — a sonda para na primeira porta que responde, e o
código guardado é dela.

*Por quê:* `findface-ntls` responde 404 em `/health` (o caminho dele é
`/v1/licenses.json`) e `extraction-api` responde 405 (espera POST).

**Trava:** `sonda 404 405 nao e servico travado`

### INV-3 — Arquitetura do fabricante não é defeito

Há **uma** instância de `findface-ntls` por instalação (manual da
NtechLab). Servidor que não a hospeda não gera achado de licença. Sem saber
se hospeda, não se afirma nada.

**Trava:** `licenca so cobra quem hospeda o ntls`

### INV-4 — Não inventar precisão que não existe

Horário típico de reincidência só aparece quando concentra de verdade
(>=50% das ocorrências). Espalhado mostra "espalhado".

*Por quê:* horário falso manda investigar a janela errada.

**Trava:** `reincidencia nao inventa horario`

---

## 2. Peso no ambiente

### INV-5 — O ciclo do monitor não ganha ida nova ao servidor

Uma execução SSH por host por ciclo (~60s), sequencial, só em host marcado
`monitorar`. Todo recurso novo reaproveita o que a passada já leu.

*Por quê:* é a promessa central do painel. Já foi violado uma vez — a
faixa do Monitor chamava `/api/painel`, que faz `docker ps` por SSH em cada
servidor, e o Monitor é a tela inicial.

**Trava:** revisão de código (sem trava automática — ver Pendência P-3)

### INV-6 — Tela de configuração não abre SSH

Listar servidores/serviços para marcar caixinha usa
`hosts.servicos_conhecidos`, preenchido pelo coletor.

**Trava:** revisão de código

### INV-7 — Log é lido só de quem está com problema

Leitura automática apenas de serviço com incidente aberto; 200 linhas, no
máximo 3 serviços por ciclo por host, no máximo 1x a cada 5 min por
serviço. Fora disso, só no clique.

*Por quê:* o appserver gera ~8 GB/dia de syslog.

**Trava:** `analise le o container e nao o servico` (cobre o caminho)

### INV-8 — Nenhuma dependência nova para tarefa pequena

`httpx` se a imagem tiver, senão `urllib` numa thread. Vale para qualquer
saída HTTP nova.

*Por quê:* peso de imagem e superfície de ataque não se pagam por um POST.

---

## 3. Segredo e permissão

### INV-9 — Segredo entra, nunca sai

Colunas `*_enc` cifradas com Fernet. Nenhum schema de saída as expõe; a
tela confirma por fingerprint. Vale para chave SSH, senha de sudo,
credencial da API do Face Detect e token do bot do Telegram.

**Trava:** `token do telegram nunca aparece`

### INV-10 — Segredo não vaza por mensagem de erro

A URL do Telegram carrega o token no caminho; qualquer erro de rede o
levaria para dentro de log e de chamado. É removido antes de registrar.

**Trava:** `token do telegram nunca aparece`

### INV-11 — Rota que devolve dado de outro domínio respeita a permissão dele

`/api/monitor/resumo` é liberado por `metrics.view` e devolve também backup
e disco do painel, que são de `backups.view`. Isso só é aceitável enquanto
todo perfil com um tiver o outro.

**Trava:** `resumo do painel nao expoe backup indevido` — falha no dia em
que existir perfil só de métrica

---

## 4. Ação destrutiva

### INV-12 — Ação em container só dentro do projeto do Face Detect

`_garantir_do_projeto()` antes de qualquer reinício ou ação de stack.

*Por quê:* sem a cerca, "reiniciar container" vira controle remoto
irrestrito do Docker — inclusive do próprio painel.

**Trava:** revisão de código

### INV-13 — Nunca matar PID solto

Ação sobre processo age no **container dono**, pela rota cercada.

*Por quê:* matar processo solto num servidor de reconhecimento facial pode
corromper banco.

**Trava:** revisão de código

### INV-14 — Nada reinicia durante limpeza de eventos

Regra do manual da NtechLab. O painel recusa o reinício enquanto há purga
em andamento, e a purga agendada não começa com backup em curso.

**Trava:** revisão de código

---

## 5. Dado guardado

### INV-15 — Tudo que grava tem prazo

Nenhuma tabela cresce sem teto. Prazos em Configurações, aplicados pela
faxina diária.

| Tabela | Chave | Padrão |
|---|---|---|
| `amostras` | `monitor.retencao_dias` | 30 |
| `incidentes` | `incidentes.retencao_dias` | 30 |
| `log_padroes` | `analise.retencao_dias` | 30 |
| `notificacao_envios` | `notificacao.retencao_dias` | 14 |
| `crescimentos` | `crescimento.retencao_dias` | 90 |
| `amostras_container` | `containers.retencao_dias` | 7 |
| `licenca_amostras` | `faxina.licenca_dias` | 365 |

**Trava:** `faxina so apaga fechado`

### INV-16 — Incidente aberto é estado, não histórico

A faxina só apaga incidente **fechado** — e o mesmo vale para a
vigilância de crescimento. Apagar um aberto faria a tela achar que o
problema nunca existiu enquanto ele acontece.

**Trava:** `faxina so apaga fechado`, `faxina apaga vigilancia fechada so`

### INV-17 — Guardar molde, não a linha

`log_padroes` guarda o padrão com contador. Mil ocorrências do mesmo erro
são uma linha.

*Por quê:* guardar log seria virar um segundo syslog.

**Trava:** `analise soma ocorrencias sem duplicar molde`,
`fingerprint agrupa o que e o mesmo erro`

---

## 6. Robustez

### INV-18 — Aviso externo nunca derruba o ciclo

`despachar()` não levanta. Amostra e incidente já estão gravados quando o
Telegram é chamado.

**Trava:** `notificacao nunca derruba o ciclo`

### INV-19 — Erro de render não apaga o painel

Fronteira de erro em volta do conteúdo. Tela que quebra fica contida; menu
segue de pé.

*Por quê:* uma variável fora de escopo em JSX deixou o painel inteiro em
tela preta.

**Trava:** revisão de código

### INV-20 — A subida do painel sobrevive ao esquema

Nenhum índice com nome repetido no metadata; coluna nova entra em
`COLUNAS_NOVAS` com `ADD COLUMN IF NOT EXISTS`.

*Por quê:* dois `CREATE INDEX` de mesmo nome mataram o startup e o deploy
foi revertido automaticamente.

**Trava:** `ddl sem indice duplicado`

### INV-21 — A tela inicial não pode dar 500

O resumo do topo do Monitor degrada para zeros se a consulta falhar.

**Trava:** `resumo do painel degrada sem quebrar`

### INV-22 — Apelido é rótulo; `hosts.name` é identidade

O apelido (`hosts.alias`) só aparece onde o nome é texto lido por gente.
Onde o nome é chave — alvo da auditoria, diretório dos artefatos de
backup, nome do arquivo exportado e palavra de confirmação de ação
destrutiva — vale `hosts.name`, sempre.

*Por quê:* `StorageService.caminho_artefato` monta o diretório da cópia
com o nome do host. Se o rótulo entrasse ali, renomear um servidor
deixaria todo backup anterior órfão num diretório que ninguém procura —
e a trilha de auditoria passaria a apontar para um nome editável.

*Dano se quebrar:* perda silenciosa do histórico de backup e auditoria
não rastreável. Nenhum dos dois dá erro na hora.

**Trava:** `apelido e rotulo nunca identidade` (inclui varredura do
código de `backups.py` e das rotas que auditam) e
`aviso mostra apelido e roteia por id`

### INV-23 — Apuração não deduz o que não leu

Uptime ilegível resulta em `reiniciou = None` e confiança `nenhuma` —
nunca em "não reiniciou".

*Por quê:* é a mesma família de "serviço travado", "câmera sem evento" e
"não consegui ler a licença": apresentar falha de leitura como fato
observado. Aqui o dano é maior, porque a conclusão decide para QUEM se
abre chamado — provedor de VM ou provedor de rede.

*Dano se quebrar:* verificado por injeção — o painel passou a afirmar que
a máquina estava "de pé há 20697 dias" e mandou isso no Telegram.

**Trava:** `apuracao distingue reboot de rede`

### INV-24 — Apuração só lê

O comando da apuração não altera estado no servidor: sem `rm`, sem
`systemctl restart/stop/reboot`, sem `docker restart/stop/rm`, sem
redirecionamento de escrita.

*Por quê:* é um comando montado a partir de dados do incidente e rodado
com sudo em servidor de produção, no pior momento possível — logo depois
de uma queda.

**Trava:** `apuracao distingue reboot de rede` (lista de formas que
executam, mais a checagem positiva das fontes de leitura)

---

### INV-25 — Tendência não se afirma sem série que a sustente

Menos de oito pontos, ou R² abaixo de 0,70 sem ajuste exponencial melhor,
resulta em `indeterminado` **com motivo** — nunca em taxa e projeção.
Queda brusca (reinício, rotação) corta a série, e a análise usa o trecho
de depois.

*Por quê:* é a família de INV-1 e INV-23 aplicada a número: uma reta
puxada de três amostras é falha de leitura vestida de previsão, e a
previsão aparece na tela e no Telegram com hora marcada.

*Dano se quebrar:* alarme falso com data e hora. Depois do segundo, a
tela deixa de ser lida.

**Trava:** `crescimento nao inventa tendencia`,
`crescimento distingue linear de exponencial`

### INV-26 — Acusação vem da diferença, não do retrato

Quem cresceu é apontado comparando **duas** medições com carimbo de hora
— por caminho no disco e por container na memória. O maior ocupante só é
citado como "o maior de agora", nunca como causa.

*Por quê:* `data/` é o maior diretório do servidor desde o primeiro dia.
Apontá-lo manda a investigação para o lugar errado toda vez.

**Trava:** `crescimento acusa quem cresceu nao quem e grande`

### INV-27 — Diagnóstico caro não vira a causa do próximo incidente

Todo comando de leitura pesada (`du`, `find`) roda com teto de tempo e
prioridade baixa de E/S, e o rastreio tem teto de um por passada do
monitor. Estourar o prazo devolve "não medido".

*Por quê:* num disco com teto de IOPS, o diagnóstico compete com o
Face Detect — e o pior momento para competir é quando o recurso já está no
limite. Mesma lição de [33_SATURACAO_DE_DISCO](../docs/33_SATURACAO_DE_DISCO.md).

**Trava:** `rastreio de crescimento so le`

### INV-28 — O rastreio de crescimento só lê

Nenhum comando altera estado: sem `rm`, `truncate`, `restart`, `stop`,
`kill`, `chmod` ou redirecionamento de escrita. Vale a mesma régua de
INV-24, e pela mesma razão — roda com sudo, em produção, sob pressão.

**Trava:** `rastreio de crescimento so le`

---

## Cobertura

Os cenários vivem em `backend/tests/verificar.py` e o total aparece na
saída da execução — não é repetido aqui, porque número copiado em dois
lugares diverge no primeiro cenário novo. Rodam sem Postgres e sem
framework:

```bash
cd backend && pip install -r requirements-dev.txt && python tests/verificar.py
```

Sete invariantes acima estão marcados como "revisão de código" — são os
candidatos naturais aos próximos cenários (ver
[pendencias.md](pendencias.md), P-3).
