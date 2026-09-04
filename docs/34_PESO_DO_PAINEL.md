# Peso do painel — o que ele custa, item por item

O FaceOps monitora servidores de reconhecimento facial em produção. Se
ele for motivo de lentidão em alguma coisa, deixou de valer a pena.

Este documento é a **auditoria de custo**, com o que roda, com que
frequência e por quê. Não é declaração de intenção: cada item foi
verificado no código, e os compromissos têm teste que falha se forem
quebrados.

---

> O conceito por trás disto — duas necessidades com cadências diferentes,
> e por que o painel desacelera sozinho — está em
> [35_CONCEITO_E_LICOES](35_CONCEITO_E_LICOES.md).

## Nos servidores do Face Detect

### O ciclo de coleta — uma ida a cada 60 s

**Uma única chamada SSH por servidor, por ciclo.** Não é uma por
métrica: um script só devolve tudo em seções.

| O que lê | Custo real |
|---|---|
| `/proc/stat`, `/proc/uptime`, `/proc/loadavg`, `/proc/meminfo`, `/proc/swaps`, `/proc/diskstats` | **zero E/S de disco** — procfs é memória |
| `df` | chamada `statfs`, microssegundos |
| `docker stats --no-stream` | lê cgroup pelo daemon; dezenas de ms de CPU, sem disco |
| `nvidia-smi` | só onde há GPU |

A conexão vem de um **pool com TTL de 120 s**: ciclos seguidos
reaproveitam a sessão em vez de refazer o handshake.

> **A medição de saturação de disco não custa disco.** Foi por isso que
> ela saiu de `/proc/diskstats`, e não de `iostat`: medir E/S gastando
> E/S seria contraditório. As duas leituras aproveitam a mesma janela que
> a medição de CPU já usava — nenhuma ida a mais.

### Duas velocidades

O painel não fica aberto o dia inteiro. O coletor acompanha:

| Situação | Intervalo | Idas/dia (4 servidores) |
|---|---|---|
| alguém usando o painel | `monitor.intervalo_s` (60 s) | 5.760 |
| ninguém há `monitor.ocioso_apos_min` (10 min) | `monitor.intervalo_ocioso_s` (300 s) | **1.152** |

Abrir o painel **acorda o coletor na hora**, então a primeira tela vem
com leitura fresca em vez de dado de cinco minutos atrás. E a tela recebe
do servidor de quanto em quanto tempo perguntar.

Vigiar não precisa de 60 s: uma queda detectada em 5 min avisa igual no
Telegram. O que **não** muda no modo econômico é o trabalho do ciclo —
incidente, aviso, backup e faxina seguem idênticos. Há teste que falha se
o ciclo passar a pular trabalho por causa do modo.

### O que **nunca** entra no ciclo

| Operação | Quando roda |
|---|---|
| `du` da árvore do Face Detect | só por clique, com `timeout` e `ionice` |
| `find /var/log` | só no diagnóstico de manutenção |
| `docker logs` | só de serviço com **incidente aberto**: máx. 3 serviços por ciclo, 1× a cada 5 min por serviço |
| `journalctl` da apuração | só quando um incidente **fecha**: máx. 2 por ciclo, com `-n` e teto de 30 s |
| Backup | agendado, e agora em `ionice -c3` (ver [33_SATURACAO_DE_DISCO](33_SATURACAO_DE_DISCO.md)) |

Enquanto está tudo de pé, o painel **não lê log de produção nenhum**. É
uma promessa com teste: se a análise deixar de depender de incidente
aberto, a suíte falha.

---

## Na VM do próprio painel

### O defeito que estava aqui

A tela do Monitor busca `/api/monitor/resumo` **a cada 10 segundos**. Essa
rota montava, a cada chamada:

| Etapa | Consultas (4 servidores) |
|---|---|
| lista de servidores | 1 |
| última amostra de cada um | 4 |
| incidentes abertos | 1 |
| `alertas()` — relê servidores e amostras, mais limiar e incidente por servidor | 13 |
| resumo do painel (backup + disco local) | 2 |
| **Total** | **~21 por chamada** |

Vinte e uma consultas a cada 10 s, **multiplicado por aba aberta**. Com
três abas, mais de 6 consultas por segundo, para sempre — e **cinco em
cada seis eram trabalho jogado fora**, porque os dados só mudam quando o
coletor roda, a cada 60 s.

Pior: a docstring da rota dizia *"uma consulta só: N+1 aqui seria N+1 a
cada poucos segundos"*. O comentário descrevia a intenção; o código fazia
exatamente o N+1 que ele condenava.

### O conserto

O resumo passou a ser **cacheado por ciclo do coletor**. A chave vem de
`MonitorService.chave_cache()` e muda quando:

* o ciclo termina — há dado novo;
* alguém mexe em servidor ou limiar — há configuração nova, e a
  alteração precisa aparecer na hora, sem esperar a próxima passada.

Resultado:

| | Antes | Depois |
|---|---|---|
| 1 aba | 21 consultas / 10 s | 21 consultas / 60 s |
| 3 abas | 63 consultas / 10 s | 21 consultas / 60 s |
| 10 abas | 210 consultas / 10 s | 21 consultas / 60 s |

**N abas custam o mesmo que uma.**

### Escrita no banco

| O que grava | Volume |
|---|---|
| Amostra do monitor | 1 linha por servidor por ciclo — 4/min, ~5.700/dia |
| Incidente | só quando algo cai ou volta |
| Molde de log | um por erro **novo**; mil ocorrências iguais são uma linha com contador |
| Aviso enviado | um por mensagem |

Tudo com retenção, e a faxina diária aplica. Nada aqui fica sem prazo —
ver [20_PERSISTENCIA](20_PERSISTENCIA.md).

### Processo do painel

| Item | Contenção |
|---|---|
| Workers do backend | **1** (`--workers 1` no Dockerfile): dois duplicariam o agendamento |
| Log dos containers | `max-size` 10m/20m, `max-file` 2/3 no compose |
| Sessões de terminal | caem por inatividade em 30 min |
| Conexões SSH | pool com TTL de 120 s |
| Sessão do navegador | expira parada em 20 min; renova só com interação de gente, **nunca** pelo polling |

---

## Os quatro compromissos, com trava

O cenário `painel nao pesa no que monitora` falha se qualquer um cair:

1. **A coleta de cada ciclo não roda nada caro.** `du`, `find`, `iostat` e
   `docker logs` são barrados no script; `docker stats` só sem stream.
2. **O resumo é cacheado por ciclo**, e a chave muda com dado novo *e* com
   configuração nova — as duas coisas verificadas.
3. **Log de produção só é lido de serviço com incidente aberto.**
4. **Toda escrita tem prazo** — as oito retenções conferidas no catálogo.

Verificado por injeção: pôr `du -sb /var` no script da coleta e desligar
o cache do resumo fizeram o teste falhar, apontando cada um.

---

## Se ainda assim pesar

Os números estão à mão, no próprio painel:

* **Monitor → rodapé**: intervalo do coletor e quantos ciclos rodaram;
* **`coleta_ms`** de cada amostra: quanto tempo a leitura levou naquele
  servidor;
* **Recursos**: agora com IOPS e utilização de disco.

E dois ajustes diretos, em Configurações:

| Ajuste | Efeito |
|---|---|
| `monitor.intervalo_s` | espaça o ciclo — 120 s em vez de 60 s corta a coleta pela metade |
| `monitor.retencao_dias` | menos amostra guardada, menos banco |

O painel também pode **deixar de monitorar** um servidor específico
(Servidores → editar → monitorar), mantendo-o cadastrado para backup e
terminal.
