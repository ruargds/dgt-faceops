# Monitor, alertas e câmeras

Três coisas nascidas do mesmo pedido: acompanhar o ambiente em tempo real,
ser avisado quando algo sai do lugar, e ver os dispositivos — tudo sem
pesar e simples o bastante para um N1 de plantão.

---

## Monitor

Aba **Monitor**. Cartão por servidor com gráficos, atualizado sozinho a
cada 10 segundos.

### Por que não pesa

O que engana num monitor contínuo é achar que a tela conversa com os
servidores. Aqui **não**:

```
coletor de fundo  ──SSH──▶  servidores    (no ritmo dele, 1×/min)
        │
        ▼
   banco do painel
        ▲
        │ (lê daqui)
  a tela do Monitor  ──────  atualiza a cada 10s, sem tocar em servidor
```

O coletor grava uma amostra estreita — **~80 bytes, só número** — de cada
servidor marcado, uma execução SSH por vez, espaçadas. Quatro servidores
a 60 s custam **~1,5% de um núcleo** no painel e nada mensurável nos
servidores.

A tela lê o histórico já gravado. Ela pode atualizar de 10 em 10 segundos
justamente porque não é ela que faz o trabalho pesado.

E **para quando a aba perde o foco.** Nada roda escondido sem ninguém
olhando.

### Gráficos

SVG puro, sem biblioteca — um gráfico de linha é uma lista de pontos e um
`<path>`. Trazer Chart.js somaria centenas de KB a um painel cujo
compromisso é ser leve.

Cada gráfico mostra a linha do tempo, o limite de alerta (linha
tracejada) e explica em uma frase o que o número significa. "Carga por
núcleo: 1,4" não diz nada sozinho; "há processo esperando CPU" diz.

Janelas: 1 h, 6 h, 24 h, 7 dias, 30 dias. Cada uma exporta em CSV.

### Ligar e desligar

Cada servidor tem a opção **monitorar**, ligada por padrão — cadastrar e
querer acompanhar são a mesma coisa na prática. Desligue para servidor que
não interessa vigiar.

O coletor inteiro liga/desliga em **Configurações → Monitoramento**, junto
com o intervalo e a retenção do histórico.

---

## Alertas

Derivados da última amostra de cada servidor. Aparecem no topo do Monitor,
ordenados por gravidade.

### Feitos para quem nunca viu o sistema

Cada alerta traz três coisas:

1. **O que está errado** — "disco / em 94% — só 6 GB livres"
2. **O que isso causa** — "cheio, o banco para de gravar e o
   reconhecimento para junto"
3. **O que fazer** — "Em Manutenção, use Diagnosticar para ver o que está
   ocupando"

Alerta que só diz o que está errado obriga a pessoa a descobrir o que
fazer — e é exatamente aí que ela liga para alguém às 3h da manhã.

### Aviso sonoro

Quando surge alerta **novo**, o navegador toca um som — dois tons para
atenção, três graves para crítico, distinguíveis sem olhar a tela.

Gerado pelo próprio navegador: **sem arquivo de áudio**, nada para baixar
ou hospedar, funciona offline. Só toca com a aba aberta, e só uma vez por
alerta — alerta que repete sem parar é alerta que se aprende a ignorar.

Ligável em Configurações e no botão do canto da tela.

### Limiares

Todos configuráveis em **Configurações → Limiares de alerta**:

| Métrica | Padrão | Vira crítico |
|---|---|---|
| Disco | 90% | 95% |
| Memória | 90% | 95% |
| Swap | 50% | — |
| Carga por núcleo | 90% (0,90) | — |
| Memória de vídeo | 92% | sempre |
| Temperatura da GPU | 85 °C | — |

---

## Câmeras

Aba **Câmeras**. Quantas estão cadastradas, quais estão comunicando, e o
volume de eventos por dispositivo.

### Duas vias de leitura

O painel lê os dados das câmeras de uma de duas formas, preferindo a
primeira:

**1. API HTTP do FindFace** (preferida) — quando o servidor tem URL e
token da API cadastrados. É a via oficial, limpa, e não depende de acesso
ao banco. Autenticação `Authorization: Token`, contagens em
`/cameras/count/` e `/events/{tipo}/count/`.

**2. Leitura direta do PostgreSQL via SSH** (alternativa) — quando não há
credencial de API. O painel descobre o esquema em tempo de execução,
perguntando ao banco quais tabelas existem, e conta os eventos com filtro
de data.

A segunda existe para quem não quer expor a API do FindFace. A primeira é
melhor onde a credencial estiver disponível — configure em **Servidores →
Editar → API do FindFace**.

### O que mostra

| Coluna | O que é |
|---|---|
| Câmera | Nome e grupo |
| Situação | Ativa (gerou evento) ou sem eventos no período |
| Eventos | Contagem no período escolhido |
| Participação | Fatia do total — mostra quais câmeras dominam o volume |
| Volume estimado | Rateio do tamanho das tabelas de evento (só na via SSH) |
| Último evento | Quando a câmera falou pela última vez |

**Câmera "sem eventos" pode estar offline.** É o sinal mais útil da tela:
uma câmera que sempre gerou e parou merece atenção.

### Por que é sob demanda

Contar evento em tabela grande custa. Por isso a consulta é **no clique**,
com janela de tempo limitada, usando a coluna de data indexada — o custo
fica proporcional ao período, não ao tamanho da tabela. **Nada disso
entra no coletor contínuo.**

Períodos: 1 hora, 24 horas, 7 dias, 30 dias.

---

## Exportações

Tudo que a operação e a auditoria precisam levar para fora do painel, em
**CSV** — abre em qualquer coisa, sem biblioteca no painel.

| Exportação | De onde | Permissão |
|---|---|---|
| Auditoria | Auditoria → Exportar | `audit.view` |
| Histórico de backups | (rota) | `backups.view` |
| Agendamentos | (rota) | `schedules.view` |
| Sessões de terminal | (rota) | `terminal.sessions.view` |
| Histórico de monitoramento | Monitor → botão de download | `metrics.view` |
| Câmeras | Câmeras → Exportar | `metrics.view` |

Cada exportação gera registro de auditoria — **exportar auditoria é, ela
mesma, um ato auditável**. O CSV usa `;` como separador e BOM UTF-8, para
o Excel abrir a acentuação corretamente sem configuração.

Teto de 100 mil linhas por exportação: sem limite, "toda a auditoria" num
painel de anos montaria centenas de MB em memória.

---

## Faxina do histórico

O histórico de monitoramento tem retenção própria (padrão 30 dias),
aplicada pela faxina diária. Uma amostra ocupa ~80 bytes; 30 dias de
quatro servidores a 60 s são alguns MB. Ajuste em **Configurações →
Monitoramento**.
