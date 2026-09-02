---
name: monitor-e-incidentes
description: Como funciona o monitor continuo do FaceOps (coletor de 60s), o historico de indisponibilidade (tabela incidentes), os limiares com excecao por host/servico, e a regra de nunca acrescentar SSH ao ciclo. Use sempre que for ler/editar backend/app/services/monitor_service.py, incidente_service.py, limiar_service.py, backend/app/api/routes/monitor.py, frontend/src/components/views/MonitorView.js, ou quando o usuario falar de alerta, limiar, servico que caiu, tempo fora do ar, reinicio em laco, ou pedir para o painel avisar de algo novo.
---

# Monitor contínuo e incidentes

Docs: `docs/23_MONITOR_E_CAMERAS.md` e `docs/25_INCIDENTES_E_LIMIARES.md`.
Se algo aqui divergir do código, **o código manda**.

## A regra inegociável: o ciclo não ganha SSH novo

O coletor faz **uma execução SSH por host por ciclo** (~60s), sequencial,
só nos hosts marcados `monitorar`. Todo recurso construído em cima dele
(incidente, análise de log, catálogo de serviços, notificação) reaproveita
o que aquela passada **já leu** — nenhum deles abriu conexão nova.

A tela lê do banco do painel e nunca toca em servidor. Se precisar de um
dado novo por host, acrescente ao script que já roda; não crie outra ida.

Isso já foi violado uma vez: a faixa de resumo do Monitor chamava
`/api/painel`, que faz `docker ps` por SSH em cada servidor — com o Monitor
virando tela inicial, abrir o painel passaria a bater nas VMs o tempo todo.

## Incidente abre e fecha sozinho

`IncidenteService.registrar_ciclo` compara o que o ciclo viu com o que está
aberto. Regras aprendidas na marra:

- **Host sem comunicação NÃO fecha incidente de serviço dele.** Sem alcançar a
  máquina não se sabe nada dos containers; fechar ali registraria uma
  recuperação que ninguém observou.
- **Reinício em laço é variação, não total.** `RestartCount` é acumulado
  desde a criação do container — 40 reinícios em três meses não é problema.
  A detecção compara com a contagem de até 30 min atrás
  (`JANELA_REINICIO_S`); quando param, a janela desliza e o incidente fecha.
- **Não há reabrir.** Mesmo serviço caindo de novo é incidente novo, com
  início próprio — juntar mentiria sobre o tempo fora.

## Limiares

Padrão global no catálogo (`config_service.py`, chaves `alerta.*`), com
exceção por host e/ou serviço em `limiar_overrides`. Cascata:
`(host+serviço) > (host) > (serviço) > catálogo`. "Restaurar padrão" é
**apagar a linha** — o padrão nunca deixou de existir.

`resolver_lote(host_id)` traz todos os overrides do host numa consulta só;
use isso no ciclo, nunca uma consulta por métrica.
