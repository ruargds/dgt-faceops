---
name: dev-checklist
description: Checklist e regras de ouro antes de commitar no FaceOps, incluindo a regra que mais custou nesta base: a tela nunca pode afirmar sobre ausencia de dado. Use sempre antes de commitar, ao revisar um diff proprio, ao criar rota/model/tela nova, ou quando o usuario pedir revisao, pedir para 'deixar perfeito', reclamar de alarme falso, ou reportar que a tela diz algo que nao confere com a realidade do servidor.
---

# Antes de commitar

Checklist completo: `docs/09_REGRAS_DESENVOLVIMENTO.md`. Se algo aqui
divergir do código, **o código manda**.

## O mínimo, sempre

```bash
cd backend  && python tests/verificar.py      # 26 cenários, sem Postgres
cd backend  && python -c "import app.main"    # o build NÃO cobre isto
cd frontend && CI=true npm run build          # único jeito de validar JSX
```

## A regra que mais custou: não afirmar sobre ausência de dado

Quatro defeitos diferentes, a mesma raiz — o painel apresentava falha de
leitura, ou arquitetura normal, como fato observado:

| A tela dizia | Era |
|---|---|
| "Serviço travado" (8x) | 404/405 é resposta; a sonda só aceitava 2xx/3xx/401/403 |
| "200 câmeras sem evento" | toda chamada falhou e o erro foi engolido |
| "Não consegui ler a licença" (3x) | esses hosts não hospedam NTLS — arquitetura do fabricante |
| "acaba em 77 dias" | a projeção ignorava a retenção que devolve espaço |

Antes de escrever afirmação na interface, pergunte:

1. É fato observado ou ausência de fato?
2. Se a leitura falhar, a tela sabe diferenciar? (campos `falhou`, `erros`)
3. É defeito, ou é como o fabricante manda montar?

Sem conseguir distinguir, a resposta honesta é **"não verificado"** — e ela
vale mais que um número errado com cara de certo.

## Armadilhas já pagas

- **Índice duplicado**: coluna com `index=True` já gera
  `ix_<tabela>_<coluna>`. Declarar o mesmo nome em `__table_args__` faz o
  `create_all` emitir dois `CREATE INDEX` iguais e o startup morre. Há
  cenário que varre todo o metadata atrás disso.
- **Variável fora de escopo em JSX**: derruba a árvore inteira do React —
  tela preta, sem menu. O `no-undef` do CRA não pega. Existe fronteira de
  erro (`components/LimiteDeErro.js`), mas ela é rede, não desculpa.
- **Serviço != container**: `docker logs` quer
  `findface-multi-findface-video-worker-1`; o incidente guarda
  `findface-video-worker`. Trocar os dois falha em silêncio.
