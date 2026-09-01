# Diagnóstico — reincidência, log agrupado e base de erros

Responde três perguntas que o painel não respondia: **o que repete**, **o
que o log está dizendo** e **o que fazer**. Aba `Diagnóstico`, em
Monitoramento.

---

## Por que não tem modelo de linguagem

Foi decisão, não falta de tempo. Três motivos, na ordem em que pesaram:

**1. Precisão.** Um modelo pequeno o bastante para caber aqui não conhece
`findface-video-worker`, sharding de Tarantool nem o procedimento de
limpeza da NtechLab. Ele produziria comando com cara de certo. Num painel
que reinicia container de reconhecimento facial em produção, sugestão
errada às 3h da manhã é dano real, não resposta ruim.

**2. A máquina.** A VM do painel é de 2–4 GB e os containers já ocupam
1,6 GB em regime. O build do frontend no `atualizar.sh` precisa de
1,5–2 GB. Um Ollama residente (1–2,5 GB com modelo de 1B–3B) não deixaria
o painel se atualizar — o efeito não seria lentidão, seria deploy
quebrado.

**3. A maior parte do pedido não pede modelo.** Agrupar erro é
fingerprint. Contar reincidência é `GROUP BY`. Sugerir reparo em domínio
estreito é catálogo curado — que ainda por cima é auditável, coisa que
saída de modelo não é.

Onde um modelo ganharia: explicar em português um erro **desconhecido**.
Isso fica para uma decisão futura, sob demanda, fora da VM do painel e
sem permissão para gerar comando executável.

---

## O que repete (reincidência)

Contagem sobre a tabela de incidentes (ver
[25_INCIDENTES_E_LIMIARES](25_INCIDENTES_E_LIMIARES.md)), na janela
escolhida (7, 14 ou 30 dias). Por serviço e por host:

| Coluna | O que é |
|---|---|
| Quedas | quantas vezes caiu na janela |
| Tempo fora | soma das durações |
| Horário típico | a hora que concentra as quedas — **só aparece se concentrar de verdade** (≥50% das ocorrências); espalhado mostra "espalhado" |
| Intervalo médio | tempo médio entre uma queda e a seguinte |
| Tendência | metade recente da janela contra a anterior: piorando, estável ou melhorando |

O limite para aparecer é `alerta.reincidencia_min` (padrão 3).

Sobre não inventar horário: eleger uma hora quando as quedas estão
espalhadas manda a pessoa investigar a janela errada. "Sem horário
típico" é uma resposta melhor que uma falsa.

Um resumo curto aparece também no topo do Monitor — "isto não é a
primeira vez" é a informação que muda a conduta de quem está de plantão,
e ela não pode depender de alguém abrir outra tela.

## O que o log está dizendo (agrupamento)

Cada linha é reduzida a um **molde**: timestamp, UUID, IP, hash, caminho
com número e medida viram marcador.

```
2026-09-01T10:00:00Z ERROR camera 17 timeout after 5031ms from 10.0.1.5
2026-09-02T23:11:44Z ERROR camera 42 timeout after 87ms  from 10.0.1.9
        ↓ mesmo molde, mesma impressão digital
<ts> ERROR camera <n> timeout after <medida> from <ip>
```

Mil linhas iguais viram **uma** com `ocorrencias = 1000`. É o "auto
filtro": o que importa é quantos tipos de erro existem e qual domina, não
o log cru.

Só `erro` e `aviso` são guardados. Guardar `info` seria virar um segundo
syslog — e o problema aqui já é log demais (8 GB/dia de syslog num
servidor real).

### O custo é limitado de propósito

O painel **não varre log sozinho**. A leitura automática acontece só
quando há incidente aberto, que é exatamente quando alguém abriria o log
na mão:

| Trava | Padrão | Config |
|---|---|---|
| Só serviço com incidente aberto | — | — |
| Linhas por leitura | 200 | `analise.linhas` |
| Reler o mesmo serviço | a cada 5 min | `analise.intervalo_min` |
| Serviços por ciclo, por host | 3 | `analise.servicos_por_ciclo` |
| Desligar tudo | ligado | `analise.ativa` |

Fora disso, só no clique: o botão **analisar log** lê aquele container
naquele instante (`POST /api/diagnostico/analisar/{host_id}`).

> **Serviço ≠ container.** `docker logs` quer
> `findface-multi-findface-video-worker-1`; o incidente guarda
> `findface-video-worker`. O painel traduz um no outro
> (`health_summary.containers`). Trocar os dois faz a leitura falhar **em
> silêncio** — a tela mostraria "nenhum padrão" para sempre, sem erro.
> Há cenário de teste travando isso.

## O que fazer (base de erros conhecidos)

`app/services/catalogo_erros.py` é a versão executável do
[10_ERROS_CONHECIDOS](10_ERROS_CONHECIDOS.md). Quando um molde casa com
uma entrada, o achado deixa de ser "erro estranho no log" e vira
**título + causa + ação + atalho para a tela que resolve**.

Cada entrada declara a origem:

| Origem | Significa |
|---|---|
| `campo` | aconteceu neste ambiente, está no 10_ERROS_CONHECIDOS |
| `manual` | documentado pela NtechLab |
| `campo+manual` | visto aqui e confirmado pelo fabricante |

### O que o manual da NtechLab acrescentou

Consulta ao manual do FindFace Multi 2.4.1 durante a montagem do
catálogo, com dois achados que mudaram entradas:

- **Licença/NTLS** (`ntls_status.html`): o diagnóstico é
  `curl http://localhost:3185/v1/licenses.json -s | jq`, e o campo
  `.last_updated` tem faixas documentadas — até 5s normal; 5–30s problema
  de rede ou disco; 30–120s "algo ruim aconteceu"; acima de 120s a fonte
  de licença deu timeout. `.licenses[].valid.valid == false` significa que
  a conexão nunca foi estabelecida, com o motivo em `.valid.description`.
  O licenciamento online precisa alcançar `license.ntechlab.com` na 443.
- **GPU demora a ficar útil**: o fabricante documenta que, na primeira
  subida, `findface-extraction-api-gpu` e `findface-video-worker-gpu`
  podem levar **até 45 minutos** por causa do cache. Ou seja, GPU em 0%
  logo depois de um restart ou de troca de perfil vGPU pode ser normal —
  isso está escrito na entrada `vram`, para o painel não empurrar
  ninguém a reiniciar container que só estava aquecendo.
- **Rotação de log** (`logs.html`): a NtechLab recomenda desligar o
  rsyslog (rotação ruim) e limitar o journald com `SystemMaxUse` — o que
  confirma o caso de campo dos 99 GB em `/var/log`.

Acrescentar um padrão é uma entrada na lista. Quando algo custar tempo
para achar, registre no doc **e** no catálogo.

---

## API

| Método | Rota | Permissão |
|---|---|---|
| GET | `/diagnostico/reincidencia?dias=N` | `metrics.view` |
| GET | `/diagnostico/padroes?dias=N&host_id=` | `services.view` |
| POST | `/diagnostico/analisar/{host_id}?servico=` | `services.view` |
| GET | `/diagnostico/catalogo` | `services.view` |

Retenção dos moldes: `analise.retencao_dias` (padrão 30), pela faxina
diária.

## Verificação

`python tests/verificar.py` cobre: agrupamento (mesmo erro junta, erros
diferentes não), catálogo reconhecendo os casos reais, soma de ocorrências
sem duplicar molde, tradução serviço→container, reincidência com horário
típico, e a recusa de inventar horário quando as quedas são espalhadas.
