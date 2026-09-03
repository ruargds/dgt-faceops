# Busca — a mesma régua em todo lugar

Quem usa o FaceOps e o InfraCore digita do mesmo jeito nos dois. O
contrato aqui é o de lá.

---

## Como digitar

| Você digita | Acha |
|---|---|
| `video` | o que **começa uma palavra** com "video" — acha `findface-video-worker` |
| `%video` | em qualquer parte, inclusive no meio de palavra |
| `"video"` | só a palavra inteira |
| `^video` | igual ao padrão, para quem quiser ser explícito |

Começo de **palavra**, não do campo: `worker` precisa achar
`findface-video-worker`. Se fosse começo de campo, seria preciso digitar
o nome inteiro.

**Vírgula, ponto-e-vírgula e quebra de linha** separam vários termos, com
OU entre eles. **Espaço não separa** — "Escola Central" continua sendo um
termo só. Colar uma coluna de planilha funciona.

**Acento não atrapalha:** `camera` acha "câmera".

O que casa no meio da palavra **não é escondido** — aparece depois na
lista. Esconder resolveria um incômodo criando outro pior: quem vê o item
na tela e recebe "nenhum resultado" conclui que a busca quebrou, sem ter
como descobrir por quê.

### Número é identificador

Nas listas de câmera, termo só de dígitos casa o número **inteiro**. Com
200 câmeras, procurar `12` por trecho traria 112, 120 e 212 — e conferir
à mão qual é a certa é inviável. Termo com letra segue por trecho, e
misturar funciona: `12, portaria`.

---

## Onde tem busca

| Tela | Procura em | Por que precisa |
|---|---|---|
| **Serviços** | serviço, container, estado, saúde | 33 containers não cabem na tela |
| **Configurações** | rótulo, chave e explicação | 60 parâmetros em 7 categorias |
| **Câmeras** | nome e id | centenas de dispositivos |
| **Processos** | comando, usuário, container, PID | dezenas por servidor |
| **Auditoria** | usuário, ação, alvo e o **detalhe** | é onde está o motivo da falha |

Nas telas, a lista é reordenada por qualidade do casamento. Em
**Processos** não: ali a ordenação que a pessoa escolheu clicando numa
coluna manda, e trocá-la seria tirar dela o controle que acabou de
exercer.

---

## Duas implementações, um contrato

| Onde | Arquivo |
|---|---|
| Tela | `frontend/src/utils/buscaInteligente.js` |
| Servidor | `backend/app/core/busca.py` |

Precisam concordar: a mesma busca tem de achar a mesma coisa na lista
filtrada e na Auditoria. Há teste comparando os dois contratos.

### Por que não é o mesmo código do InfraCore

Lá a busca do servidor usa `unaccent()` e o operador de regex `~*` com
`\m`/`\M`, que são do Postgres. Aqui a suíte roda em **SQLite**
justamente para não exigir banco — copiar aquilo deixaria a busca do
servidor sem teste nenhum.

Então: `LIKE`, que funciona nos dois, com a lista de separadores
enumerada. Ela precisa cobrir **pontuação de JSON**: o detalhe da
auditoria é JSON, e ali o texto vem entre aspas — sem `"` na lista,
procurar `timeout` não acharia `{"erro": "timeout"}`.

### Os dois limites, ditos com todas as letras

**1. Acento no servidor.** O termo é procurado com e sem acento, então
digitar como está escrito sempre acha. Digitar **sem** acento e achar
**com** depende da extensão `unaccent`, que a subida tenta habilitar uma
vez; falhar não impede a subida. Sem ela, essa combinação não casa — e o
teste afirma isso, em vez de fingir que casa.

**2. `"exato"` no servidor.** É montado como "começa palavra" E "termina
palavra", dois conjuntos combinados. Em teoria um campo poderia
satisfazer as duas metades em ocorrências diferentes. A versão exata
cruzaria cada separador com cada separador — cerca de novecentas
cláusulas por coluna, mais caro que o erro que evita. Na **tela** é
exato, porque ali dá para varrer a string.

---

## Verificação

`cenario_busca_entende_acento_e_parte_da_palavra` cobre os operadores, a
separação por vírgula, a pontuação de JSON, o comportamento de ponta a
ponta no banco, e que as quatro telas usam a régua comum em vez de
`includes` cru.

Verificado por injeção duas vezes. A segunda foi instrutiva: a lista de
telas da trava nasceu sem `ProcessosView`, e deixou passar a volta ao
`includes` ali. Trava que não cobre o que diz cobrir não guarda nada.
