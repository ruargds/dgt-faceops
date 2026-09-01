---
name: avisos-telegram
description: Configuracao e envio de avisos por Telegram no FaceOps: um bot por cliente mandando para um grupo, regras por servidor/servico, deduplicacao e o token que nunca pode sair. Use sempre que for ler/editar backend/app/services/telegram_service.py, notificacao_service.py, backend/app/api/routes/notificacoes.py, backend/app/models/notificacao.py, frontend/src/components/views/NotificacoesView.js, ou quando o usuario falar de alerta no Telegram, bot, grupo de alertas, chat_id, notificacao externa ou spam de aviso.
---

# Avisos no Telegram

Doc completo: `docs/28_AVISOS_TELEGRAM.md`. Se algo aqui divergir do
código, **o código manda**.

## O token é o segredo mais sensível desta parte

Quem tem o token manda mensagem como o bot. Três regras, todas com teste:

1. Cifrado com Fernet (`core.vault`), como as chaves SSH.
2. **Nunca sai por API** — a tela recebe nome do bot e impressão digital.
3. Removido de log e de erro (`telegram_service._limpar`): a URL do
   Telegram carrega o token no caminho, e traceback vira anexo de chamado.

## Só envio — sem laço de escuta

O bot do InfraCore faz long-polling porque responde comandos. Aqui não: só
há chamada de saída, e só quando há evento. Laço aberto 24h seria custo
permanente para nada.

**Sem dependência nova**: `httpx` se a imagem tiver, senão `urllib` numa
thread — mesma convenção do `ffapi_service`.

## Contra virar spam

Deduplicação por chave de evento, teto de 8 por ciclo, aviso só na
transição (nunca "ainda está fora"), e retorno desligável por regra.

## Regras: silêncio por omissão

Sem regra ligada, **nada** é enviado. Precedência:
`(host+serviço) > (host) > (serviço) > (todos)`. A lista de serviços da
tela vem de `hosts.servicos_conhecidos` (preenchido pelo coletor) — **não**
abra SSH numa tela de configuração.

## O passo manual que não dá para automatizar

O Telegram não deixa um bot entrar sozinho num grupo. Alguém precisa
adicioná-lo — mesmo passo do Zabbix. Só depois disso o `chat_id` funciona.

## Nunca derruba o ciclo

`despachar()` não levanta exceção. O aviso é o último passo: amostra e
incidente já estão gravados quando o Telegram é chamado. Há teste para isso.
