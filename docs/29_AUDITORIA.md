# Auditoria: busca e filtros

Quem fez o quê nos servidores. Aba **Auditoria**, com `audit.view`.

---

## O problema que os filtros resolvem

A trilha só tinha um seletor de nível. Com um seletor de nível, as
perguntas reais não se respondem:

* "quem mexeu no vm-dbserver ontem?"
* "por que aquele restore falhou?"
* "o que o joao andou fazendo?"

Nenhuma delas se responde rolando uma lista. Auditoria que não se
consulta é arquivo morto — existe para a conformidade e não para a
operação.

## O que existe agora

| Filtro | Para quê |
|---|---|
| **Busca livre** | usuário, ação, alvo **e o detalhe** |
| **Período** | 24h, 7, 30 ou 90 dias |
| **Nível** | crítico, atenção, info |
| **Usuário** | quem existe de fato no log |
| **Ação** | o que existe de fato no log |
| **Só o que falhou** | a pergunta "o que deu errado" |

Os filtros **somam**. Há "Limpar filtros" quando algum está ativo, e a
contagem de resultados aparece embaixo — com aviso quando o teto de 300
linhas é atingido, porque lista que para sem dizer isso faz a pessoa
concluir que não há mais nada.

### A busca varre o detalhe

É a parte que importa. O `detail` é onde está o parâmetro que explica a
ação — o perfil do backup, o erro do restore, o serviço reiniciado. Sem
buscar ali, para achar "por que o restore falhou" seria preciso saber de
cor qual ação registrou o erro.

Custo: o `detail` é JSONB e entra por conversão para texto, o que é
varredura. É aceitável **por construção** — a faxina apaga além da
retenção, então a tabela é pequena, e a consulta tem teto de linhas. A
digitação também espera 350 ms antes de consultar: sem isso, cada tecla
seria uma consulta ao banco.

### As opções vêm do log, não do catálogo

`/api/auditoria/filtros` lista os usuários e as ações que **realmente
aparecem** no período. Não vem da tabela de usuários nem do catálogo de
permissões: usuário apagado continua tendo agido, e oferecer ação que
nunca aconteceu só dá resultado vazio para quem escolhe. As três colunas
(`usuario`, `action`, `ts`) são indexadas, então isto é varredura de
índice.

## Exportar traz o que está na tela

O botão exporta **com os filtros aplicados**. Os dois caminhos — listar e
exportar — chamam a mesma `audit_service.aplicar_filtros`; cada um com a
sua cópia divergiria no primeiro filtro novo, e **CSV que discorda da
tela é pior que CSV nenhum**, porque ninguém desconfia de um arquivo.

Há teste para essa unicidade: ele falha se a exportação voltar a montar
filtro por conta.

Que filtro gerou o arquivo entra no próprio registro de auditoria da
exportação — sem isso, dois CSVs com contagens diferentes ficam sem
explicação. Exportar auditoria é, ele mesmo, um ato auditável.

## Verificação

`python tests/verificar.py` → `auditoria busca acha e filtra`. Cobre
busca por alvo, por ação parcial, por usuário e **dentro do detalhe**;
maiúscula/minúscula; só-falhas; filtros somando; termo inexistente
devolvendo vazio (filtro que "falha aberto" faria a pessoa concluir o
contrário do certo); e a unicidade do filtro entre tela e exportação.

Para o modelo ser testável, `audit_logs.detail` passou a
`JSON().with_variant(JSONB, "postgresql")` — JSONB continua em produção,
e o SQLite dos testes passa a compilar a tabela. Mesma decisão de
`hosts.servicos_conhecidos`.
