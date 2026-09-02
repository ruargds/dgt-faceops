# Apuração — o que causou, respondido quando o incidente fecha

O painel sempre disse "provável" na **abertura** do incidente, a partir do
que o Docker contava. Faltava a pergunta seguinte, que é a que interessa
depois: *e o que foi, afinal?*

Ela só tem resposta num instante específico — **quando a máquina volta a
atender**. É o único momento em que dá para perguntar a ela.

---

## A pergunta que paga o recurso inteiro

Servidor sem comunicação tem duas explicações opostas:

| O que aconteceu | Onde está o problema | Para quem se abre chamado |
|---|---|---|
| a máquina **reiniciou** | nela | provedor da VM (Azure) |
| a máquina **não reiniciou** | no caminho até ela | provedor de rede |

Distinguir as duas custa ler um número: o *uptime*. Se o sistema subiu
dentro da janela, reiniciou. Se já estava de pé desde antes, ficou ligado
o tempo todo — e o que falhou foi a rede, a rota ou o firewall.

O cálculo usa `date +%s` menos `/proc/uptime`, e **não** `uptime -s`:
aquele imprime hora local, e comparar hora local com a janela em UTC daria
a conclusão errada duas vezes por ano.

## O que chega no Telegram

Como a apuração roda no fechamento, a resposta cabe na **mesma** mensagem
de retorno — uma segunda mensagem depois seria mais spam para dizer o que
cabia na primeira:

```
🎥 FaceOps · PROCERGS

✅✅ - VM-APPSERVER-01 (Aplicação) - ✅✅

✅ - Resolvido: a máquina inteira voltou a funcionar

⏱ - Duração: 3m 10s

🔎 - Causa: A máquina NÃO reiniciou — ficou ligada durante toda a janela

💬 - Evidência: o sistema já estava de pé desde 24/08 03:11:02 (UTC), 9d
     antes do incidente começar — então o servidor não caiu: o que falhou
     foi o caminho até ele (rede, rota ou o firewall).

🕐 - Horário: 02/09 15:00:50
```

Sem apuração, o retorno sai exatamente como era. Não existe linha
"Causa: —" ocupando espaço para não dizer nada.

## Quando não se acha nada

O veredito é **"Não encontrei evidência da causa"**, e o achado explica o
que faltou ler. Isso é regra da casa, não modéstia: deduzir "não
reiniciou" a partir de uma leitura que falhou é o mesmo erro de "serviço
travado" e "câmera sem evento" — apresentar falha de leitura como fato
observado.

Há teste para isso: uptime ilegível resulta em `reiniciou = None` e
confiança `nenhuma`. Ao quebrar essa regra de propósito, o painel passou
a afirmar que a máquina estava "de pé há 20697 dias" — e o teste falhou.

## Custo, item por item

| Item | Contenção |
|---|---|
| Chamadas SSH | **uma**, por incidente apurado |
| Conexão | a que o ciclo do monitor já tem aberta (pool com TTL) — sem novo handshake |
| Quando | só no fechamento, e só para o que fechou naquela passada |
| Quantos | `MAX_POR_CICLO = 2` — dez serviços voltando juntos não viram dez comandos |
| Duração | `TIMEOUT_S = 30`; passado disso, o ciclo segue |
| Saída | `-n` no journalctl e corte de caracteres antes de gravar |
| Escrita no servidor | **nenhuma** — só leitura, com teste que barra comando que altere estado |

## Rotatividade: nenhuma faxina nova

A apuração mora na **própria linha do incidente** (`incidentes.apuracao`),
não em tabela própria. Duas consequências: é um-para-um com o incidente,
e a retenção de incidentes (`incidentes.retencao_dias`, padrão 30 dias) já
a apaga junto. Nada a configurar, nada a lembrar.

## Nível de registro

Dois níveis, porque as duas necessidades são reais e opostas:

| Nível | O que lê | Para quê |
|---|---|---|
| **resumido** (padrão) | uptime, `last`, journal do kernel e do sistema na janela, `docker inspect` e log do container | responder "o que foi" em poucas linhas — o que cabe no aviso do celular |
| **completo** | tudo acima, mais `systemctl --failed`, `dmesg`, estado das interfaces (`ip -br link`), memória e disco; e mais linhas de cada fonte | material de investigação de um caso |

Configurável em **Configurações → Monitoramento → Profundidade da
apuração**.

O completo **não** é o padrão de propósito: ele lê mais do servidor e
grava mais no banco, em toda queda, todo dia. Vale ligar quando se está
investigando um caso — e desligar depois. Há teste que falha se o nível
caro virar padrão.

Quando o teto corta achados, a tela diz **quantos** ficaram de fora. Sem o
número, o fim da lista parece o fim da evidência.

## Onde ver

Serviços → coluna **Histórico (7d)** → a tabela traz a coluna **Causa
apurada**; clicar abre o popup com o veredito, a confiança e cada linha
lida, com a fonte de onde veio (uptime, kernel, journal, dmesg, docker,
log do container).

Incidente **aberto** não tem causa apurada, e a tela diz isso: a apuração
roda no fechamento, porque é quando a máquina volta a responder.

### Apurar sob demanda

Há botão para incidente já fechado, em dois casos: ele é anterior a esta
função, ou a apuração automática não conseguiu falar com o servidor. Não
funciona em incidente aberto — enquanto está aberto, o que serve é a tela
de log ao vivo, e o journal do período ainda está crescendo.

## Verificação

`python tests/verificar.py` — quatro cenários:

| Cenário | Trava |
|---|---|
| `apuracao distingue reboot de rede` | as três conclusões (reiniciou / não reiniciou / não sei), a folga da janela e a ausência de comando que altere estado |
| `apuracao le o container certo e aponta oom` | nome de container do compose; OOM vence código de saída (137 com OOM é memória, não bug) |
| `apuracao respeita o nivel e os tetos` | o nível caro não é padrão, valor desconhecido cai no padrão, fontes extras só no completo, tetos finitos |
| `apuracao entra no aviso de retorno` | a causa na mesma mensagem; sem apuração, nada de linha vazia |
