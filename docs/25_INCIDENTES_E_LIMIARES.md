# Incidentes e limiares por serviço (2026)

Duas peças que nasceram juntas, do mesmo pedido: "o painel de alerta já é
bom, mas eu quero saber **quando** um serviço caiu e **quando voltou**, e
quero poder ajustar o limite de alerta para uma máquina ou um serviço
específico, sem mexer no padrão de todo mundo".

---

## Histórico de indisponibilidade (`Incidente`)

### Por que não existia

O painel sempre respondeu "o que está quebrado **agora**" a partir da
última amostra de cada host — de propósito, para não precisar decidir
quando um alerta "fecha" (ver `monitor_service.py`, comentário original em
`alertas()`). Isso é suficiente para "tem algo errado?", mas não para "essa
câmera já caiu antes? por quanto tempo?".

### Como abre e fecha sozinho

Sem nenhuma consulta nova ao servidor. O ciclo do monitor contínuo (a cada
~60s, ver [23_MONITOR_E_CAMERAS](23_MONITOR_E_CAMERAS.md)) **já** pergunta
a cada host quem está de pé, para desenhar os cartões
(`MonitorService._amostrar` → `StackService.health_summary`). O
`IncidenteService` só compara esse resultado com o que já estava aberto:

```
ciclo do monitor (60s)
   │
   ▼
StackService.health_summary(host)  ──▶  {"servicos_doentes": [...]}
   │
   ▼
IncidenteService.registrar_ciclo(host, doentes)
   │
   ├── serviço na lista de doentes e SEM incidente aberto  → abre um
   └── incidente aberto cujo serviço SUMIU da lista         → fecha (fim = agora)
```

Regra de fechamento: se o mesmo serviço cair de novo nove segundos depois,
é um **incidente novo**, com início próprio — juntar os dois mentiria
sobre quanto tempo ficou fora.

Host inteiro sem contato (`a.erro`) também vira incidente, `tipo="host"`.

### Causa provável — heurística, não IA

Nenhum modelo, nenhuma chamada externa. `incidente_service._causa_provavel`
lê os mesmos sinais que o Docker já entrega em toda passada (ver
`StackService.list_services`) e traduz para o vocabulário de quem opera o
FindFace:

| Sinal do Docker | Causa provável |
|---|---|
| `oom_killed` | morto por falta de memória — confira Recursos antes de reiniciar |
| `exit_code != 0` | saiu com código de erro — veja o log |
| `saude == "unhealthy"` | healthcheck falhando — veja o log |
| `reinicios >= alerta.servico_reinicios` (padrão 5) | reiniciando em loop — câmera problemática ou falta de recurso |
| parado sem nenhum dos anteriores | verifique se foi manual ou por falta de memória/disco |

Isso responde ao pedido de "usar IA para rastrear logs e erros" sem
instalar nada: é regra sobre dado que o painel já lê, roda em
milissegundos e não consome CPU/RAM extra na VM do painel nem nos
servidores do FindFace. Um modelo local de verdade (tipo Ollama) para
análise de log foi deliberadamente **adiado** — os servidores do FindFace
são sensíveis a carga, e essa decisão (onde rodaria, qual modelo, que
orçamento de CPU/RAM) merece uma conversa própria antes de entrar em
produção.

### Onde aparece

- **Monitor → Serviços por máquina**: abertos agora, ou os últimos 3 dias
  (botão "Ver últimos 3 dias").
- **Alertas do Monitor**: um alerta por serviço com problema (não mais um
  agregado "3 serviço(s) com problema") — com a causa provável na ação e
  "há quanto tempo" no texto.
- API: `GET /api/incidentes/abertos`, `GET /api/incidentes/recentes?dias=N`
  (também espelhado em `GET /api/monitor/resumo` e
  `GET /api/monitor/incidentes/recentes`, para a tela de Monitor não
  precisar de uma segunda ida ao servidor).

### Retenção

Só incidentes **fechados** entram na faxina — um aberto é estado atual,
apagá-lo faria a tela achar que o problema nunca existiu enquanto ainda
está acontecendo. Padrão: **30 dias**, configurável em
**Configurações → Monitor → "Guardar histórico de indisponibilidade por"**
(`incidentes.retencao_dias`), rodando junto da faxina diária que já limpa
o histórico de métricas.

---

## Limiares por host e/ou serviço

### O que já existia, e o que faltava

`Configurações → Limiares de alerta` sempre teve um valor **só, para a
instalação inteira** — "disco acima de 90%" vale para todo host. Faltava a
exceção: "no vm-ftpserver, aceito carga mais alta", "o
findface-video-worker pode reiniciar mais vezes antes de virar alarme
crítico" (câmera de rua reinicia mais que uma de ambiente controlado, e
isso não deveria exigir mudar o padrão de todo mundo).

### Cascata de resolução

```
override (host + serviço)  >  override (host, geral)  >  override (serviço, todo host)  >  padrão global
```

Tabela nova, `limiar_overrides` (`host_id` opcional, `servico` opcional,
`chave`, `valor`). "Restaurar padrão" é **apagar a linha** — não existe um
valor "restaurado" para guardar, porque o padrão do catálogo nunca deixou
de existir. `LimiarService.resolver_lote(host_id)` traz, numa consulta só,
todo override que afeta aquele host (geral + por serviço) — o ciclo do
monitor não pode fazer uma consulta por métrica por host a cada rodada.

### Duas famílias de chave

| Nível | Chaves | Onde se aplica |
|---|---|---|
| Da máquina | `disco_pct`, `mem_pct`, `swap_pct`, `cpu_pct`, `gpu_mem_pct`, `gpu_temp` | um host inteiro |
| De um serviço | `servico_reinicios`, `servico_indisponivel_min` | um container específico, dentro de um host (ou de todos) |

`servico_reinicios` (padrão 5) é o que decide se um container "de pé" já
conta como problema — ver a tabela de causa provável acima.
`servico_indisponivel_min` (padrão 15) é quando um alerta de serviço parado
sobe de atenção para crítico.

### Onde editar

**Configurações → Limiares de alerta → "Limiares por servidor ou
serviço"**: tabela do que já foge do padrão, com botão **restaurar
padrão** por linha, e formulário para adicionar uma exceção nova (limite,
servidor — ou "todos os hosts" —, serviço quando aplicável, valor). Exige
perfil Administrador, mesma permissão da configuração global.

API: `GET /api/limiares` (lista + chaves aceitas), `PUT /api/limiares`
(salva/atualiza uma exceção), `DELETE /api/limiares/{id}` (restaura o
padrão).
