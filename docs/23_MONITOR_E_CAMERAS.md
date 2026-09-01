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

### Uso de CPU e carga são coisas diferentes

O Monitor mostra os dois, em gráficos separados, porque responder um pelo
outro leva à conclusão errada:

| | O que é | Quando assusta |
|---|---|---|
| **Processador em uso** | quanto da CPU foi realmente gasta, de 0 a 100% | perto de 100% de forma sustentada |
| **Carga por núcleo** | quantos processos querem CPU ao mesmo tempo | acima de 1,00 há alguém esperando a vez |

Uma máquina pode estar com **carga 4,0 e uso 20%**: ninguém está gastando
CPU, todos estão esperando disco. Trocar o servidor por causa desse número
é dinheiro no lixo — o gargalo é o disco. E pode estar com **uso 100% e
carga 1,0**: um processo só, usando tudo o que tem, sem fila.

O uso vem de duas leituras de `/proc/stat` na mesma coleta, com a diferença
entre elas — `/proc/stat` é contador acumulado desde o boot, não taxa.
`idle` e `iowait` não contam como uso; `steal` (CPU que o hipervisor tomou)
aparece à parte na tela de Recursos, que é o que explica lentidão em VM do
Azure sem nenhum processo pesado aparecendo.

Amostra gravada antes desta medição existir não tem uso de CPU: o gráfico
deixa buraco em vez de desenhar uma máquina ociosa que nunca existiu.

Em **Recursos** há ainda o **uso por núcleo**. Um núcleo cravado em 100%
com os outros parados é processo de uma thread só — mais CPU não resolve, e
o gargalo está no programa.

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

### Atalho de verdade (2026)

Cada alerta é clicável — leva direto ao cartão do servidor, com o histórico
já aberto. Quando o problema é de um serviço específico, o selo "ir para
X" navega para a tela certa **já com o host (e o serviço) selecionados**,
em vez de só escrever o nome do destino e deixar a pessoa procurar de
novo. Mecanismo: `AppShell.js` guarda um `alvo` além da aba ativa
(`nav(aba, alvo)`), e cada tela de destino (Serviços, Recursos, Manutenção,
Servidores) lê esse alvo ao montar para se posicionar sozinha — mesmo
padrão do `navContext` do InfraCore, sem framework de rotas novo.

### Serviços por máquina (2026)

Painel abaixo dos cartões do Monitor: todo serviço com problema, agrupado
por host, com a **causa provável** e **desde quando** — "voltou às 14:32,
ficou fora por 6min" quando já resolveu. Vem do histórico de
indisponibilidade (`Incidente`), aberto e fechado sozinho pelo próprio
ciclo do monitor, sem nenhuma consulta extra ao servidor. Documentado em
[25_INCIDENTES_E_LIMIARES](25_INCIDENTES_E_LIMIARES.md).

### Horário de pico (2026)

Na tela de detalhe de cada servidor: uma faixa com a média de uso por hora
do dia, últimos 14 dias — "este host trabalha mais entre 8h e 18h".
Nenhum modelo, nenhuma IA: é uma agregação SQL (`GROUP BY hora`) sobre o
histórico que o monitor já grava, do mesmo custo de uma consulta de tela
comum (`GET /api/monitor/pico`).

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

Isto é o **padrão global** — vale para toda a instalação. Para um limite
diferente num host ou serviço específico ("o vm-ftpserver aceita carga
mais alta", "o video-worker pode reiniciar mais vezes"), veja
[25_INCIDENTES_E_LIMIARES](25_INCIDENTES_E_LIMIARES.md).

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

### Licenciamento

O cartão no topo da aba responde a pergunta que abre toda conversa de
expansão: **cabem quantas câmeras ainda?** Antes ela só tinha resposta
entrando na interface da NtechLab.

Vem pela API HTTP (`GET /api/dispositivos/{id}/licenca`), é leitura barata
e carrega sozinha ao trocar de servidor — diferente da contagem de
eventos, que continua no clique. O número de uso muda o tempo todo, então
há **Atualizar uso**: quem quer o número de agora pede o número de agora,
em vez de a tela ficar consultando sozinha.

O cartão traz o mesmo que a tela de licenças da NtechLab: identificação,
validade, tipo, arquivo e a tabela de recursos com **em uso** e
**liberado**. Recurso estourado — usado acima do liberado, como o
`Objects TNT API` a 2.400.054 de 2.400.000 no ambiente levantado — sobe
para o topo da tabela e aparece em vermelho, porque é o que trava operação
sem avisar ninguém.

### Credencial da API: usuário e senha

O FindFace é acessado com **usuário e senha** — os mesmos da plataforma da
NtechLab. O contrato está na documentação da própria instalação
(`/api-docs/`, spec em `/swagger.json`):

```
POST /auth/login/
Authorization: Basic base64(usuario:senha)
{"uuid": "dgt-faceops-<id do host>", "mobile": false, "device_info": {…}}
→ {"token": "…", "token_expiration_datetime": "…", "user": {…}}
```

Detalhes que custaram uma tentativa frustrada: a credencial vai no
cabeçalho **Basic**, não no corpo, e o `uuid` de dispositivo é
**obrigatório**. O painel se identifica como `dgt-faceops-<id>`, estável
por servidor — assim a sessão aberta por ele é reconhecível na tela de
sessões da plataforma em vez de virar um dispositivo novo a cada login. O
token devolvido vira `Authorization: Token <token>` nas leituras
seguintes, reaproveitado por 20 minutos em memória.

A URL pode ser cadastrada com ou sem `/api` no fim: o painel tenta as duas
e guarda a que autenticou. Token continua aceito para instalação que gere
um — preenchido, substitui usuário e senha.

O botão **Testar API** consulta `/users/me/` antes da contagem de câmeras
e mostra sob que conta o painel entrou: conta errada aparece ali, e não
depois, numa permissão negada no meio de uma operação.

O certificado do FindFace é autoassinado em rede interna, então a
verificação de cadeia é dispensada **nesta chamada e em mais nenhuma** —
exigir cadeia válida aqui só impediria o painel de ler a plataforma que
ele opera.

Para cada recurso licenciado: **liberado**, **em uso**, **livre** e a
ocupação em barra. Limite negativo aparece como `ilimitado`. O número de
câmeras cadastradas é lido de `/cameras/count/` e confronta o limite
licenciado — limite sem uso ao lado não responde nada.

**A licença é lida de dentro do servidor, por SSH.** O NTLS atende em
`localhost` sem exigir login — quem já está dentro da máquina não precisa
provar de novo quem é —, e o painel já tem SSH com sudo em todos os
servidores. Depender também de uma senha de aplicação para ler licença é
uma credencial a mais para alguém errar, e foi o que aconteceu aqui: a
senha da API voltou `Invalid username/password` enquanto o SSH funcionava
o tempo todo. A API fica como alternativa, para quem preferir não abrir
shell.

A tabela mostra o **total** (`products.<produto>.resources`), que é o que a
aba General do fabricante mostra — e não a linha de cada licença
(`licenses[].products…`), que repetia o mesmo recurso com limites
parciais. Os nomes saem do mapa da própria interface: `objects_tntapi`
vira "Objects TNT API", `extapi` vira "Extraction API".

A contagem de câmeras do painel **não** entra nessa tabela. Ela entrava
quando a licença não informava uso, e o resultado foi uma linha dizendo
"425 em uso de 5 liberados, 8500%" — número real ao lado de número
inventado, sem aviso de qual era qual. O total de câmeras fica no cartão
ao lado, onde é só o que é.

O aviso de vencimento usa o mesmo limiar da interface do fabricante: 60
dias.

**A licença não vem da API do FindFace.** Ela é servida pelo **NTLS**, um
serviço à parte, e por isso os caminhos saem da *raiz* do site — se a API
foi cadastrada como `https://ip/api`, a licença continua em
`https://ip/ntls/...`:

| Caminho | O que traz |
|---|---|
| `GET /ntls/v1/licenses.json` | a licença: `license_id`, `expire_date` (epoch), `valid.valid` e os recursos |
| `GET /ntls/v1/usage-report.json` | o consumo por intervalo — o que explica um recurso estourado |

Cada recurso traz **`current`** (em uso) e **`value`** (liberado); a tela
do fabricante rotula `source` como "File" e `type` como "Type of license".
Isso não está no swagger da API: saiu da leitura do bundle da própria
interface do FindFace da instalação, que é quem chama esses endereços.

Um detalhe de honestidade:

* **O caminho pode mudar entre versões.** Depois do endereço do NTLS, o
  painel ainda tenta `/licenses/ffsecurity/`, `/license/` e `/licenses/`.
  Se nenhum responder, a tela diz **o que foi tentado e o erro de cada
  tentativa** — foi assim que se descobriu que os quatro primeiros
  candidatos devolviam 404 nesta instalação.
* **Não existe contrato público para o corpo da licença.** Em vez de fixar
  um formato, o painel percorre o JSON e reconhece campos de limite
  (`limit`, `max`, `quota`, `total`…) e de uso (`used`, `current`,
  `count`…) onde eles estiverem. O botão **Ver resposta bruta** mostra o
  JSON como veio — melhor mostrar JSON do que esconder o dado.

Sem URL e token cadastrados, o cartão diz isso e aponta para **Servidores
→ Editar → API do FindFace**. O limite de licença **não** existe no banco
lido por SSH; é a única informação desta tela que exige a API.

### Quando as duas vias falham

A tela mostra os **dois** erros — o da API e o da leitura direta do banco.
Antes, a falha da API ficava só no log do painel e a mensagem na tela era
a do `psql`: alguém investigava banco quando o problema era token de API
vencido.

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
