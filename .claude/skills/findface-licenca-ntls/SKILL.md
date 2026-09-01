---
name: findface-licenca-ntls
description: Licenciamento do FindFace Multi e o findface-ntls: a licenca e da INSTALACAO e ha uma unica instancia de NTLS, no appserver - os demais servidores nao tem nada na 3185, e isso nao e defeito. Inclui os limiares documentados pelo fabricante. Use sempre que for ler/editar backend/app/services/licenca_service.py, internos_service.py, rastreio_service.py, ffapi_service.py, ou quando o usuario falar de licenca, NTLS, limite estourado, 'nao consegui ler a licenca', porta 3185, ou servico aparecendo como travado.
---

# Licença e findface-ntls

Docs: `docs/23_MONITOR_E_CAMERAS.md` (licenciamento) e
`docs/24_RASTREIO.md`. Se algo aqui divergir do código, **o código manda**.

## Uma instância por instalação — do manual

A NtechLab é explícita: *"a single instance of findface-ntls should be
enough. If your system requires more license servers, contact NtechLab
support service beforehand to prevent your system from being blocked."*

Consequência prática numa instalação distribuída: o NTLS mora no
**appserver**. `vm-dbserver`, `vm-extraction` e `vm-ftpserver` **não devem**
ter nada escutando na 3185 — arquitetura documentada, não falha.

O painel só cobra licença de host que hospeda o container `findface-ntls`
(via `hosts.servicos_conhecidos`). Sem a lista coletada ainda, não afirma
nada. Isso já rendeu três falsos críticos por rastreio.

## Diagnóstico do fabricante

```bash
curl http://localhost:3185/v1/licenses.json -s | jq
```

Faixas documentadas de `.last_updated`:

| Valor | Significa |
|---|---|
| até 5s | normal |
| 5–30s | problema de rede ou de disco |
| 30–120s | "algo ruim aconteceu" — ver log do ntls |
| acima de 120s | a fonte de licença deu timeout |

`.licenses[].valid.valid == false` = a conexão nunca foi estabelecida; o
motivo está em `.valid.description`. Licenciamento online precisa alcançar
`license.ntechlab.com` na 443.

## 404/405 não é serviço travado

A sonda de componentes tenta `/health`, `/status`, `/` e **para na primeira
que responde qualquer coisa**. O código é sinal de vida, não veredito: o
`findface-ntls` responde 404 em `/health` porque o caminho dele é
`/v1/licenses.json`, e o `extraction-api` responde 405 porque espera POST.
Só `000` (curl não conectou) é ausência de resposta.

## GPU demora a ficar útil

O fabricante documenta que, na primeira subida,
`findface-extraction-api-gpu` e `findface-video-worker-gpu` levam **até 45
minutos** por causa do cache. GPU em 0% logo após restart ou troca de
perfil vGPU pode ser normal — não empurre reinício.
