# Regras de desenvolvimento

## Regras de ouro

### Segredos

1. **Nenhum schema de saída expõe coluna `*_enc`.** Quando adicionar campo
   secreto, adicione a coluna cifrada no model e **não** adicione ao
   `HostOut`. A UI confirma pelo fingerprint.

2. **Segredo entra por corpo JSON, nunca por query param.** Query string vai
   para o log de acesso do nginx.

3. **No `PATCH`, campo de segredo `None` significa "manter".** String vazia
   apagaria. A UI só envia o que foi digitado — mantenha essa distinção.

4. **Antes de gravar em auditoria, passe pelo `_limpar()`.** Se adicionar um
   nome de campo secreto novo, inclua em `CHAVES_PROIBIDAS`.

### SSH

5. **Nunca conecte com `known_hosts=None`.** A pinagem existe porque a senha
   de sudo viaja no handshake. Se precisar de conexão nova, use
   `SSHService._build_options()`.

6. **Senha de sudo sempre por stdin (`sudo -S`).** Nunca na linha de comando
   — apareceria no `ps` do servidor.

7. **Toda entrada que vai para a linha de comando passa por
   `shlex.quote()`** — e, se for nome de container ou serviço, também por
   allowlist antes.

8. **Comando novo que age no Docker: valide o escopo do projeto.** Chame
   `_garantir_do_projeto()`. Sem isso o endpoint vira controle remoto
   irrestrito do Docker.

### Backend

9. **Um worker, sempre.** APScheduler e sessões de terminal vivem em
   memória. Dois workers duplicariam agendamento e quebrariam o WebSocket.
   Está fixado no `CMD` do Dockerfile.

10. **Todo campo booleano tem `nullable=False` no model.** Sem isso, um
    `UPDATE` parcial gera NULL e a serialização do Pydantic quebra.

11. **Operação longa não bloqueia a requisição.** Backup responde 202 e
    segue em `asyncio.create_task`. Requisição que passa de 60s é problema
    de desenho.

12. **Trabalho bloqueante vai para `asyncio.to_thread`.** SDK do Azure e
    `rclone` são bloqueantes; travar o event loop por 40 minutos derrubaria
    o terminal e as coletas junto.

13. **Tarefa de segundo plano abre sessão de banco própria.** A da
    requisição fecha quando a resposta sai. Use `AsyncSessionLocal()`.

14. **`except Exception` de rede de segurança em execução longa.** Uma
    execução de backup nunca pode ficar presa em `executando` para sempre —
    o bloco final marca `falha` com o tipo da exceção.

15. **Commit de progresso no máximo a cada 2s.** Commit por linha de log
    afogaria o Postgres do painel num backup verboso.

### Parsers

16. **Não confie no formato de ferramenta de linha de comando.** `free` muda
    entre versões do `procps` — leia `/proc/meminfo`. Use `df -P` para
    garantir uma linha por sistema de arquivos.

17. **`nvidia-smi` devolve `[N/A]`.** Em GPU virtualizada do Azure vários
    campos vêm sem valor. Nenhum parser pode assumir número — use
    `_to_float`, que devolve `None`.

18. **Memória "usada" é `total - MemAvailable`.** Contar buffers e cache
    como uso é o erro que faz alguém achar que a máquina está estourando.

19. **Não gere JSON no bash.** Quebra no primeiro valor com aspas ou acento.
    Use as seções `###FACEOPS:` e parseie no Python.

### Frontend

20. **Ícone sempre de `Icons.js`.** Heroicons outline, `strokeWidth 1.5`.
    Nunca SVG solto, nunca emoji em botão ou rótulo.

21. **Botão sem permissão é OMITIDO, não desabilitado.** Use
    `has("codigo")` do `usePermissions()`.

22. **Cuidado com aninhamento de guarda.** `{canA && (<div>{canB && …}</div>)}`
    nunca avalia `canB` se `canA` é falso. Correto: `{(canA || canB) && …}`.

23. **Caminho de import depende do diretório.**
    `components/Comuns.js` → `from "./Icons"`.
    `components/views/X.js` → `from "../Comuns"`, `from "../../api"`.

24. **Sem polling eterno.** A tela de Backups só liga o intervalo enquanto
    houver execução em andamento. Recursos só coleta no clique. Cada leitura
    é um SSH em servidor de produção.

25. **Confirmação destrutiva é por digitação, não por diálogo.** Use
    `ConfirmarDigitando`. "Tem certeza? [OK]" vira reflexo na terceira vez.

### Permissões

26. **O catálogo é hardcoded.** Não existe tabela `permissions`. Registre em
    `PERMISSION_CATALOG` antes de usar — `require_permission` levanta erro na
    subida se o código não existir.

### Deploy

27. **`deploy.sh --build` para aplicar qualquer mudança de código.**
    Backend, frontend, `requirements.txt`, `Dockerfile` — tudo. O código é
    copiado para dentro da imagem (`COPY backend/app ./app`) e não há
    volume de fonte.

28. **`deploy.sh` sem `--build` NÃO aplica código novo.** Ele recria os
    containers com a imagem que já existe. Serve para trocar valor do
    `.env`, recriar container derrubado ou testar a subida — e o mesmo
    vale para `atualizar.sh --sem-build`. Esta regra já esteve escrita ao
    contrário aqui, e a consequência é sutil: o painel sobe igual e passa
    a anunciar uma revisão que não é a que está rodando.

29. **Container fantasma:** se der "container name already in use", rode
    `docker rm -f faceops_backend faceops_frontend`. O `deploy.sh` já faz.

30. **Script `.sh` tem que estar em LF.** O `.gitattributes` garante no
    clone; ZIP baixado não. O instalador Windows converte. Sem isso o bash
    falha com `$'\r': command not found`, que não diz nada sobre a causa.

## Checklist antes de commitar

```
[ ] python -c "import ast; ast.parse(open('arquivo.py',encoding='utf-8').read())"
    para cada .py alterado
[ ] bash -n script.sh  para cada .sh alterado
[ ] Frontend: npm run build passa? (é o único jeito de validar JSX)
[ ] Chaves { } e parênteses ( ) balanceados nos .js alterados
[ ] Ícone importado de Icons.js, com caminho relativo certo
[ ] Permissão nova está em PERMISSION_CATALOG e em ROLE_PERMISSIONS?
[ ] Campo booleano novo tem nullable=False no model?
[ ] Campo secreto novo: ficou FORA do schema de saída?
[ ] Campo secreto novo: entrou em CHAVES_PROIBIDAS da auditoria?
[ ] Rota nova: tem Depends(require_permission(...))?
[ ] Rota que age no Docker: chama _garantir_do_projeto()?
[ ] Ação destrutiva: exige confirmação e gera auditoria critical?
[ ] git config user.email "dev@dgt.com.br" e user.name "DGT Dev"
```

## Verificar a API inteira sem banco

A geração do OpenAPI exercita todos os `response_model` — é o smoke test
mais barato que existe:

```bash
cd backend
SECRET_KEY=x python -c "
import sys; sys.path.insert(0,'.')
from app.main import app
e = app.openapi()
print(len(e['paths']), 'caminhos')
"
```

Se um schema estiver inconsistente, quebra aqui, sem precisar de Postgres.

> Atenção ao enumerar rotas: em FastAPI recente, `app.routes` mostra
> `_IncludedRouter` sem `.path` para routers incluídos. Use `app.openapi()`,
> não `app.routes`.

## Estilo

- **Comentário explica *por que*, não *o quê*.** `# incrementa i` não serve.
  `# 60% é a margem que evita encher o disco de produção às 3h` serve.
- **Português nos nomes de domínio** (`perfil`, `destinos`, `agendamento`),
  inglês onde a biblioteca impõe (`host_id`, `status`, `cron`).
- **Mensagem de erro é para o operador ler na tela.** "cron deve ter 5
  campos: minuto hora dia mês dia-da-semana. Recebido: 4 campo(s)." em vez
  de "invalid cron".
- Backend: 4 espaços, linha até ~95 colunas.
- Frontend: 2 espaços.

## Onde não mexer sem pensar duas vezes

| Arquivo | Por quê |
|---|---|
| `core/vault.py` | mudar a derivação da chave torna ilegível tudo já gravado |
| `ssh_service._build_options` | é onde a pinagem de host acontece |
| `stack_service._garantir_do_projeto` | é a cerca que impede controle remoto irrestrito do Docker |
| `backend/Dockerfile` (`--workers 1`) | dois workers duplicam agendamento |
| `scripts/ffmulti-backup.sh` (`trap`) | é o que garante o stack subir se o script morrer |
