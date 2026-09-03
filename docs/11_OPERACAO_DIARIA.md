# Operação diária

## Rotina

### Todo dia, 2 minutos

Abra o **Painel**. Um cartão por servidor. Você está procurando três coisas:

1. **Ponto verde** em todos — sem conexão ou serviço com problema aparece
   como âmbar ou vermelho
2. **Último backup de ontem**, com selo verde — se o selo diz `nenhum` ou
   está vermelho, resolva hoje
3. **Disco de backup do painel** com espaço — se passar de 85%, a retenção
   está curta demais ou o disco é pequeno

Se os três estão bem, acabou.

Uma quarta, quando o painel avisou: **Crescimento**, no grupo
Monitoramento, responde "isto está subindo e vai estourar quando". Ela lê
só o histórico já gravado, então abrir não custa nada ao servidor — e é a
única tela que dá tempo de agir antes do estrago. Ver
[38_CRESCIMENTO_E_VAZAMENTO](38_CRESCIMENTO_E_VAZAMENTO.md).

### Toda semana, 10 minutos

- **Backups** → confira que os agendamentos rodaram todos os dias. Falha
  isolada acontece (rede); falha repetida é problema.
- **Serviços**, em cada servidor → coluna **Reinícios**. Número subindo em
  algum serviço é sinal de causa não resolvida.
- **Recursos**, em cada servidor → Atualizar. Disco acima de 80% ou VRAM
  constantemente acima de 90% pedem ação antes de virar incidente.
- **Crescimento**, janela de 7 dias → algum container com ritmo positivo
  constante em MB/h? Consumo alto e ESTÁVEL é normal; o que interessa é o
  que sobe e não volta.
- **Auditoria** → filtre por `critical`. Alguém parou stack, apagou backup
  ou mexeu em credencial? Era esperado?

### Todo mês, 30 minutos

- **Recursos → Analisar** em cada servidor. O `data/` está crescendo em que
  ritmo? Projete: em quantos meses enche o disco?
- Confirme que o **perfil completo** mensal rodou, e quanto tempo de parada
  causou
- **Restore de teste** em VM separada — o único jeito de saber se o backup
  serve. Ver [03_RESTORE](03_RESTORE.md)
- Revise usuários: quem saiu da equipe ainda tem acesso?

## Como ler os números

### Memória

O painel mostra "usado" como `total - MemAvailable`, descontando cache e
buffers. É o número honesto.

| Faixa | Leitura |
|---|---|
| até 70% | normal |
| 70–88% | acompanhe; sem urgência |
| acima de 88% | risco de OOM kill nos containers |

**Cache e buffers altos são bons**, não ruins — é o kernel usando RAM
ociosa. Não some com o "usado".

**Swap em uso** num servidor de reconhecimento facial é sinal ruim: latência
de disco no caminho de dados. Se o swap está sendo usado com frequência, a
VM está pequena.

### Carga por núcleo

O número que importa, não a carga bruta. Carga 8 em 8 núcleos é 1,00 por
núcleo — cheio, não sobrecarregado. Carga 8 em 2 núcleos é 4,00 — fila.

| Faixa | Leitura |
|---|---|
| até 0,7 | folga |
| 0,7–1,0 | trabalhando no limite saudável |
| acima de 1,0 | há processo esperando CPU |

### GPU

`findface-extraction-api` e `findface-video-worker` disputam a mesma GPU.

- **Utilização alta e constante (>90%)** — a GPU é o gargalo. Mais câmeras
  vão piorar o reconhecimento, não aumentar a cobertura.
- **VRAM perto do limite** — a próxima câmera vai causar falha de alocação,
  e o worker entra em ciclo de reinício.
- **Temperatura** — acima de 85 °C costuma haver throttling.
- **`[N/A]` nos campos** — GPU virtualizada (NV-series com GRID). Normal, o
  hipervisor não expõe esses sensores.

A lista de **processos usando a GPU** mostra quem está consumindo VRAM.

### Reinícios de container

A coluna mais informativa da tela de Serviços.

| Valor | Leitura |
|---|---|
| 0 | container subiu e ficou |
| 1–3 | provavelmente reinício de deploy ou manutenção |
| acima de 3 e subindo | **há causa não resolvida** |

`oom_killed` marcado significa que o container foi morto por falta de
memória — não é bug do FindFace, é dimensionamento.

`findface-video-worker` reiniciando é quase sempre câmera problemática ou
VRAM esgotada. O log dele diz qual câmera.

## Plantão — o reconhecimento parou

Ordem de investigação. Não pule etapas: a maioria dos incidentes termina no
passo 3.

### 1. Painel (10 segundos)

Qual servidor está vermelho? Serviço com problema, ou sem conexão?

- **Sem conexão** → problema de rede ou a VM caiu. Verifique no portal do
  Azure antes de mexer em qualquer coisa.
- **Serviço com problema** → siga para o passo 2.

### 2. Serviços (30 segundos)

Qual container não está `running`, ou está `unhealthy`?

Olhe também: `oom_killed` marcado? Contagem de reinícios alta?

### 3. Log do container (1 minuto)

Ícone de log, no serviço afetado. As últimas 400 linhas. A causa costuma
estar nas 20 últimas.

### 4. Reiniciar o container (30 segundos)

Se o log aponta para algo transitório — conexão perdida, timeout, câmera
fora — reinicie **só aquele container**. Resolve a maioria dos casos, e é
reversível.

Confirme na tela que voltou a `running` e `healthy`.

### 5. Recursos e Crescimento (1 minuto)

Se o reinício não resolveu, ou se o container voltou a morrer:

- Memória perto do limite? → OOM kill vai repetir
- Disco cheio? → PostgreSQL e Tarantool param de escrever
- VRAM esgotada? → serviços de GPU não sobem

**Disco cheio é o incidente mais comum em servidor de reconhecimento
facial**, porque as fotos de evento crescem sem parar. Use
**Recursos → Analisar** para ver onde está indo.

E quando a pergunta for "quem está comendo a RAM desta máquina", abra
**Crescimento**: o gráfico de memória por container mostra as curvas lado
a lado, e a tabela abaixo abre um container por vez, com o ritmo em MB/h.
Sai do histórico já gravado — não custa uma ida ao servidor no meio de um
incidente.

### 6. InTerminal

O que os botões não cobrem. Comandos que costumam ajudar:

```bash
cd /opt/findface-multi

sudo docker compose ps                       # visão geral
sudo docker compose logs -f --tail 100 <serviço>
df -h                                        # disco
free -h                                      # memória
nvidia-smi                                   # GPU
sudo dmesg | tail -50                        # OOM kill, erro de I/O
sudo journalctl -u docker --since "1 hour ago"
```

Tudo o que você fizer aqui fica gravado. Isso é bom: no dia seguinte, dá
para reconstruir o que foi feito.

### 7. Último recurso — parar e subir o stack

Só quando um container individual não resolve e o log aponta para estado
inconsistente entre serviços.

**Isto derruba o reconhecimento.** Exige digitar o nome do servidor.

Prefira `restart` a `stop` + `up`: menos tempo fora.

## Agir num serviço, e ver o histórico dele

Cada linha da tela **Serviços** tem quatro ações, na ordem do menos para
o mais invasivo:

| Ação | O que faz | Quem pode |
|---|---|---|
| log | últimas linhas do container | `services.view` |
| histórico | as quedas dos últimos 7 dias | `metrics.view` |
| play / stop | sobe ou para **aquele** serviço | `services.power` |
| Reiniciar | derruba e sobe de novo | `services.restart` |

**Parar pede o nome do container digitado.** O risco aqui não é errar a
ação, é errar *qual serviço* — digitar o nome prova que o dedo estava na
linha certa. Subir não pede nada: religar o que estava parado não tem
como piorar a situação.

E parar um serviço **vai** gerar aviso: o monitor registra a queda como
qualquer outra e, se houver regra de notificação, manda no Telegram. É o
comportamento certo — parada planejada que não avisa ninguém é
indistinguível de queda real para quem está de plantão.

O perfil de plantão (`operador`) **não** tem `services.power`, e isso é
deliberado: o plantão precisa destravar serviço travado (reiniciar), não
deixar serviço parado. São riscos de ordem diferente.

### O histórico não custa nada ao servidor

A coluna "Histórico (7d)" e a aba que ela abre saem da tabela de
`incidentes`, que o ciclo do monitor **já** preenche a cada passada.
Consequências práticas:

* **nenhum SSH** — abrir o histórico não toca no servidor de produção;
* **nenhuma tabela nova** e nenhuma retenção nova — a de incidentes
  (`incidentes.retencao_dias`, padrão 30 dias) já recicla, e a faxina
  diária já a aplica;
* **nenhuma requisição extra** — os itens vêm junto com a lista de
  serviços, então a aba abre instantânea.

Há teste que falha se essa consulta passar a depender de SSH, stack ou
docker: `historico do servico nao toca no servidor`.

O que **não** está nessa aba: quem parou, subiu ou reiniciou pelo painel.
Isso é auditoria, tem tela própria com busca e filtro, e repetir aqui
seria um segundo lugar contando a mesma coisa — que divergiria. O rodapé
da aba aponta para lá.

## Espaço em disco — antes de virar incidente

O `data/findface-upload` cresce com cada evento. Em servidor movimentado são
gigabytes por dia.

**Sintomas de disco cheio chegando:**

- Disco acima de 85% em Recursos
- Em **Crescimento**, o disco com projeção de estouro dentro de dias — e a
  tela já diz qual caminho está ganhando espaço por hora
- PostgreSQL com erro de escrita no log
- Eventos novos sem imagem

**O que fazer, em ordem de preferência:**

1. **Limpeza de eventos antigos na plataforma do FindFace** — a NtechLab tem
   configuração de retenção. É a solução certa.
2. **Aumentar o disco da VM no Azure** — resolve, custa.
3. **Tiered storage** — mover eventos antigos para disco mais lento. A doc
   da NtechLab cobre.
4. `docker system prune -f` — recupera espaço de imagem e container órfão,
   não de dado. Ajuda pouco, mas é rápido.

Não apague nada em `data/` na mão. O PostgreSQL e o Tarantool guardam
referências; apagar arquivo por baixo deles gera inconsistência silenciosa.

## Bom hábito

**Antes de mudança planejada** (atualizar FindFace, mexer em configuração,
mudar rede): rode um backup `essencial` sob demanda. Leva minutos e dá um
ponto de retorno preciso.

**Depois de mudança:** confira Serviços e faça outro `essencial`. Se algo
quebrar dias depois, você tem o "antes" e o "depois".

**Nunca:** confie num backup que nunca foi restaurado.

## Zabbix junto com o painel

Os dois se complementam:

| Zabbix | FaceOps |
|---|---|
| histórico e tendência | leitura instantânea na investigação |
| alerta ativo | ação (reiniciar, backup, terminal) |
| visão de infraestrutura | visão do FindFace especificamente |

Vale monitorar o próprio painel pelo Zabbix:

```
http://<painel>:8080/api/saude
```

Devolve JSON com `ok`, número de agendamentos ativos e terminais abertos.
Se o painel cair, os backups param — e é bom saber disso por alerta, não por
descobrir no dia seguinte que o backup de ontem não existe.
