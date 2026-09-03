"""
Busca inteligente no servidor — gêmea de `frontend/src/utils/buscaInteligente.js`.

Mesmo contrato dos dois lados: quem digita `%erro` na Auditoria espera o
mesmo resultado que `%erro` daria numa lista filtrada na tela.

| digitou | acha |
|---|---|
| `restore` | o que COMEÇA uma palavra com "restore" |
| `%restore` | em qualquer parte, inclusive no meio |
| `"restore"` | só a palavra inteira |
| `^restore` | igual ao padrão, explícito |

Vírgula, ponto-e-vírgula e quebra de linha separam termos, com OU entre
eles. Espaço não separa.

**Por que não é a mesma implementação do InfraCore.** Lá isto usa
`unaccent()` e o operador de regex `~*`, que são do Postgres. Aqui a
suíte roda em SQLite justamente para não exigir banco — copiar aquilo
deixaria a busca do servidor sem teste nenhum. Então: LIKE, que funciona
nos dois, e `unaccent` só quando a extensão existe.

**Acento.** O termo é procurado em duas formas, com e sem acento: digitar
`conexão` acha `conexão`, e digitar `conexao` acha `conexao`. Digitar SEM
acento e achar COM depende da extensão `unaccent`, que a subida tenta
habilitar uma vez. Sem ela essa combinação não casa — e dizer isso é
melhor do que fingir que casa.
"""
import logging
import re
import unicodedata

from sqlalchemy import String, and_, cast, func, or_

log = logging.getLogger("faceops.busca")

# Tudo o que separa palavras na prática: espaço, pontuação e a sintaxe de
# JSON. O sublinhado fica de fora de propósito — em `disco_pct` ele liga,
# não separa.
#
# A lista é enumerada, e não expressa como "não é caractere de palavra",
# porque LIKE não tem essa negação. Precisa cobrir PONTUAÇÃO DE JSON: o
# detalhe da auditoria é JSON, e ali o texto vem entre aspas. Sem `"` na
# lista, procurar `timeout` não acharia `{"erro": "timeout"}`.
SEPARADORES = (
    " ", "\t", "\n", "\r",
    ".", ",", ";", ":", "!", "?",
    "-", "/", "|", "=", "+", "*", "&", "#", "@", "~",
    "(", ")", "[", "]", "{", "}", "<", ">",
    '"', "'",
)

# Ligado pela subida quando a extensão existe. Vive no módulo, e não na
# configuração, porque é capacidade do banco — não é escolha de operação.
_UNACCENT = False


async def habilitar_unaccent(conexao) -> bool:
    """
    Tenta habilitar `unaccent`. Chamada uma vez, na subida.

    Falhar é normal e não é erro: em banco gerenciado a extensão pode não
    estar disponível, e o painel segue com a busca um pouco menos
    tolerante. O que não pode é a subida cair por causa disto.
    """
    global _UNACCENT
    try:
        from sqlalchemy import text as _text

        await conexao.execute(_text("CREATE EXTENSION IF NOT EXISTS unaccent"))
        await conexao.execute(_text("SELECT unaccent('teste')"))
        _UNACCENT = True
        log.info("busca: extensão unaccent ativa")
    except Exception as exc:
        _UNACCENT = False
        log.info(
            "busca: sem unaccent (%s) — procurar sem acento não acha com acento",
            type(exc).__name__,
        )
    return _UNACCENT


def usa_unaccent() -> bool:
    return _UNACCENT


def normalizar(valor) -> str:
    """Minúsculas, sem acento, sem espaço nas pontas."""
    texto = unicodedata.normalize("NFD", str(valor or ""))
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto.lower().strip()


def ler_termo(termo: str):
    """`(modo, texto)` com modo em `contem` | `inicio` | `exato`."""
    t = str(termo or "").strip()
    if not t:
        return (None, "")
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        return ("exato", t[1:-1].strip())
    if t[0] == "^":
        return ("inicio", t[1:].strip())
    if t[0] == "%":
        return ("contem", t[1:].strip())
    return ("inicio", t)


def separar_termos(busca: str) -> list[str]:
    """
    Termos como foram digitados, só em minúsculas.

    O acento é preservado aqui de propósito: quem tira é
    `condicao_de_termo`, que procura as DUAS formas. Normalizar cedo
    demais faria `conexão` deixar de achar `conexão`.
    """
    partes = re.split(r"[,;\n]", str(busca or ""))
    return [p for p in (x.strip().lower() for x in partes) if p]


def _escapar_like(texto: str) -> str:
    """`%` e `_` são curingas do LIKE; vindos de quem digita, são literais."""
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _formas(texto: str) -> list[str]:
    """O texto como veio e sem acento, sem repetir quando são iguais."""
    cru = _escapar_like(texto)
    sem = _escapar_like(normalizar(texto))
    return [cru] if cru == sem else [cru, sem]


def condicao_de_termo(colunas, termo: str):
    """Condição SQL de UM termo sobre as colunas dadas."""
    modo, texto = ler_termo(termo)
    if not texto:
        return None

    partes = []
    for coluna in colunas:
        col = func.lower(cast(coluna, String))
        if _UNACCENT:
            col = func.unaccent(col)

        for alvo in _formas(texto):
            if modo == "contem":
                partes.append(col.like(f"%{alvo}%", escape="\\"))
                continue

            # Começa uma palavra: no início do campo, ou após separador.
            comeca = [col.like(f"{alvo}%", escape="\\")]
            for sep in SEPARADORES:
                comeca.append(col.like(f"%{sep}{alvo}%", escape="\\"))

            if modo != "exato":
                partes.extend(comeca)
                continue

            # `exato` também exige TERMINAR palavra.
            #
            # Limite honesto: são dois conjuntos combinados com E, então
            # em teoria um campo poderia satisfazer o começo numa
            # ocorrência e o fim em outra. A alternativa exata cruzaria
            # cada separador com cada separador — cerca de novecentas
            # cláusulas por coluna, mais caro que o erro que evita. A
            # régua da TELA é exata, porque lá dá para varrer a string.
            termina = [col.like(f"%{alvo}", escape="\\")]
            for sep in SEPARADORES:
                termina.append(col.like(f"%{alvo}{sep}%", escape="\\"))

            partes.append(and_(or_(*comeca), or_(*termina)))

    return or_(*partes) if partes else None


def condicao_de_busca(colunas, busca: str):
    """
    Condição de todos os termos, com OU entre eles.

    Devolve `None` para busca vazia — e quem chama trata isso como "não
    filtra", nunca como "não casa nada".
    """
    condicoes = [
        c
        for c in (condicao_de_termo(colunas, t) for t in separar_termos(busca))
        if c is not None
    ]
    return or_(*condicoes) if condicoes else None
