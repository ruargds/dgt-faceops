---
name: diagnostico-e-log
description: Analise de log do FaceOps: molde/fingerprint que agrupa erros, catalogo de erros conhecidos (sintoma-causa-acao), reincidencia, e os limites que impedem o painel de varrer log de producao. Use sempre que for ler/editar backend/app/services/log_analise_service.py, catalogo_erros.py, backend/app/api/routes/diagnostico.py, frontend/src/components/views/DiagnosticoView.js, ao acrescentar um erro conhecido, ou quando o usuario falar de analise de log, erro que se repete, IA/Ollama para log, ou pedir sugestao automatica de reparo.
---

# Diagnóstico — log agrupado e erros conhecidos

Doc completo: `docs/27_DIAGNOSTICO.md`. Se algo aqui divergir do código,
**o código manda**.

## Por que NÃO tem modelo de linguagem

Decisão registrada, não falta de tempo:

1. **Precisão.** Modelo pequeno o bastante para caber na VM do painel não
   conhece `findface-video-worker`, Tarantool nem o procedimento de limpeza
   da NtechLab — produz comando com cara de certo, num painel que reinicia
   container de produção.
2. **A máquina.** VM de 2–4 GB, containers já ocupam 1,6 GB, e o build do
   `atualizar.sh` precisa de 1,5–2 GB. Ollama residente quebraria a
   atualização — não deixaria o painel lento, deixaria sem deploy.
3. **A maior parte do pedido não pede modelo.** Agrupar erro é fingerprint;
   contar reincidência é `GROUP BY`; sugerir reparo em domínio estreito é
   catálogo curado — que ainda é auditável.

Se a conversa voltar: o único ponto onde um modelo ganha é **explicar erro
desconhecido em português**, sob demanda, fora da VM do painel, e sem
permissão para gerar comando executável.

## Coleta limitada de propósito

O painel **não varre log sozinho**. Lê só de serviço com incidente aberto,
200 linhas, no máximo 3 serviços por ciclo por host, no máximo uma vez a
cada 5 min por serviço. Fora disso, só no clique.

Motivo concreto: o appserver gera ~8 GB/dia de syslog.

## Acrescentar um erro conhecido

Duas pontas, sempre as duas:

1. `docs/10_ERROS_CONHECIDOS.md` — sintoma, causa, solução, em prosa.
2. `app/services/catalogo_erros.py` — entrada com `regex`, `titulo`,
   `causa`, `acao`, `onde` (id da aba, vira atalho) e `fonte`
   (`campo` | `manual` | `campo+manual`).

Toda entrada precisa de ação e de origem declarada: sugestão sem
procedência é o que este catálogo existe para evitar. Há cenário de teste
que falha se faltar qualquer uma das duas.
