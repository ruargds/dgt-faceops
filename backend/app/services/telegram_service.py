"""
Envio pelo Telegram — só envio, nada de bot que escuta.

Decisão que vale registrar: este painel **não faz long-polling**. O bot do
InfraCore escuta comandos e por isso precisa de um laço permanente
segurando conexão com o Telegram. Aqui a necessidade é outra — mandar
aviso quando algo cai — e um laço de escuta seria processo aberto 24h
para nada. Só há chamada de saída, e só quando há evento.

Dependência: **nenhuma nova**. Usa `httpx` se a imagem já tiver, senão
`urllib` numa thread — a mesma convenção do `ffapi_service`. Para um POST
pequeno num endereço fixo, trazer biblioteca nova seria peso e superfície
de ataque sem retorno.
"""
import asyncio
import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger("faceops.telegram")

API = "https://api.telegram.org"
TIMEOUT = 12


class TelegramError(Exception):
    """Falha ao falar com o Telegram, já com o token removido da mensagem."""


def _limpar(texto: str, token: str) -> str:
    """
    O token não pode vazar em log nem em mensagem de erro.

    A URL do Telegram carrega o token no caminho, então qualquer traceback
    ou `str(exc)` de urllib o traria junto — e log de painel vai parar em
    print, em chamado, em anexo de e-mail.
    """
    if token and token in texto:
        texto = texto.replace(token, "***")
    return texto[:300]


def _post(metodo: str, token: str, payload: dict) -> dict:
    url = f"{API}/bot{token}/{metodo}"
    dados = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=dados, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        corpo = ""
        try:
            corpo = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        # 401/404 aqui é quase sempre token errado; 400 com "chat not
        # found" é chat_id errado. Dizer isso poupa meia hora.
        raise TelegramError(_limpar(f"HTTP {exc.code}: {corpo}", token)) from None
    except Exception as exc:
        raise TelegramError(_limpar(f"{type(exc).__name__}: {exc}", token)) from None


async def _chamar(metodo: str, token: str, payload: dict) -> dict:
    """
    httpx quando existe (não bloqueia o loop), urllib numa thread quando
    não — em nenhum dos dois casos o event loop fica parado esperando a
    rede.
    """
    try:
        import httpx
    except Exception:
        httpx = None

    if httpx is not None:
        url = f"{API}/bot{token}/{metodo}"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as cliente:
                r = await cliente.post(url, json=payload)
                if r.status_code >= 400:
                    raise TelegramError(_limpar(f"HTTP {r.status_code}: {r.text[:300]}", token))
                return r.json()
        except TelegramError:
            raise
        except Exception as exc:
            raise TelegramError(_limpar(f"{type(exc).__name__}: {exc}", token)) from None

    return await asyncio.to_thread(_post, metodo, token, payload)


def _resultado(resposta: dict, token: str) -> dict:
    if not resposta.get("ok"):
        raise TelegramError(_limpar(str(resposta.get("description", "resposta sem ok")), token))
    return resposta.get("result") or {}


async def quem_sou(token: str) -> dict:
    """`getMe` — confirma que o token vale e diz de qual bot ele é."""
    return _resultado(await _chamar("getMe", token, {}), token)


async def descobrir_chats(token: str) -> list[dict]:
    """
    Quais chats já falaram com o bot — grupos e pessoas.

    Resolve o único passo chato da configuração: descobrir o `chat_id`. O
    `getUpdates` devolve as mensagens recentes, e delas sai o id de cada
    grupo e de cada pessoa que mandou algo.

    Duas condições da plataforma, não do painel:

    * **Grupo** — alguém precisa ter adicionado o bot e mandado uma
      mensagem lá. O Telegram não deixa o bot entrar sozinho.
    * **Pessoa** — ela precisa ter mandado `/start` para o bot. Antes
      disso, o Telegram recusa qualquer envio com "bot can't initiate
      conversation with a user".

    E o `getUpdates` só guarda o histórico recente (~24h): quem falou com o
    bot na semana passada e não voltou a falar não aparece aqui.
    """
    resultado = _resultado(await _chamar("getUpdates", token, {"limit": 100}), token)
    vistos: dict[str, dict] = {}
    for atualizacao in resultado if isinstance(resultado, list) else []:
        msg = (
            atualizacao.get("message")
            or atualizacao.get("channel_post")
            or atualizacao.get("my_chat_member")
            or {}
        )
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None:
            continue
        bruto = chat.get("type", "")
        nome = (
            chat.get("title")
            or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
            or chat.get("username")
            or str(cid)
        )
        vistos[str(cid)] = {
            "chat_id": str(cid),
            "nome": nome,
            # "private" é conversa com uma pessoa; o resto é grupo/canal.
            "tipo": "individual" if bruto == "private" else "grupo",
            "tipo_telegram": bruto,
        }
    return sorted(vistos.values(), key=lambda c: (c["tipo"], c["nome"]))


async def enviar(token: str, chat_id: str, texto: str) -> dict:
    """
    Manda a mensagem. Texto puro, sem Markdown/HTML de propósito: nome de
    container e caminho vêm com `_`, `-` e `.`, que quebram o parser do
    Telegram e fariam a mensagem falhar justamente no meio de um
    incidente.
    """
    return _resultado(
        await _chamar("sendMessage", token, {
            "chat_id": chat_id,
            "text": texto,
            "disable_web_page_preview": True,
        }),
        token,
    )
