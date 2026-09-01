---
name: deploy-faceops
description: Como atualizar o DGT FaceOps no servidor e o que ja quebrou fazendo isso. O caminho da instalacao e /opt/.faceops (nao ~/dgt-faceops), o atualizar.sh reverte sozinho se o painel nao responder em 80s, e 'compila' NAO garante que sobe. Use sempre que for fazer deploy, atualizar o servidor, investigar deploy revertido, mexer em atualizar.sh, deploy.sh, docker-compose.yml, main.py (startup/COLUNAS_NOVAS), ou quando o usuario disser que o painel nao subiu, voltou a versao anterior, ou pedir o comando de atualizacao.
---

# Deploy do FaceOps

Doc completo: `docs/17_ATUALIZACAO.md`. Se algo aqui divergir do código,
**o código manda**.

## O comando, e o caminho certo

```bash
cd /opt/.faceops && bash atualizar.sh
```

`/opt/.faceops` é onde a instalação real vive. `~/dgt-faceops` é o exemplo
genérico do doc e **não existe** no servidor — já custou um "No such file
or directory".

| Variação | Para quê |
|---|---|
| `--verificar` | só diz se há versão nova; não altera nada |
| `--sem-build` | só código Python; não serve quando o frontend mudou |
| `--forcar` | passa por cima de backup/terminal em andamento |

## A armadilha que já derrubou o painel (01/09/2026)

O `atualizar.sh` espera 80s pelo `/api/saude` e, se não responder,
**reverte sozinho** para o commit anterior. Foi o que aconteceu quando o
modelo `Incidente` declarou dois índices com o mesmo nome: o `create_all`
da subida falhou, o startup do FastAPI morreu junto, e nada respondeu.

O ponto que importa: **`npm run build` passou e `import app.main` passou.**
Nenhum dos dois executa o evento de startup. Antes de qualquer deploy:

```bash
cd backend && python tests/verificar.py     # 26 cenários, sem Postgres
```

Se o deploy falhar de novo, o comando que dá a resposta em uma linha é o
que o próprio script sugere: `sudo docker compose logs --tail 80 backend`.

## Coluna nova em tabela existente

`create_all` cria tabela que falta mas **não** adiciona coluna em tabela
que já existe. Coluna nova entra em `COLUNAS_NOVAS` (`app/main.py`), que
roda `ADD COLUMN IF NOT EXISTS` — idempotente. Esquecer disso dá "column
does not exist" só no servidor atualizado, nunca no seu.
