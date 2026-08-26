"""
Cliente da API HTTP do FindFace Multi.

Descoberto observando a aplicação real: API REST estilo Django REST
Framework, autenticação `Authorization: Token <hash>`, contagens em
`/cameras/count/` e `/events/{tipo}/count/` com filtro de data.

É a via **preferida** para consultar câmeras quando o host tem URL e
token cadastrados: mais limpa que ler o Postgres via SSH, e é o caminho
que o fabricante oferece. O SSH+psql continua como alternativa para quem
não quer expor a API ou não tem credencial dela.

O token é lido do cofre (cifrado com Fernet) no momento do uso, nunca
logado nem devolvido pela API do painel.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import asyncssh  # noqa: F401 — garante o mesmo event loop; import defensivo

from app.core.vault import decrypt_secret

log = logging.getLogger("faceops.ffapi")

# Tipos de evento que o FindFace expõe com /count/
TIPOS_EVENTO = ("faces", "bodies", "cars")

PERIODOS = {
    "hora": timedelta(hours=1),
    "dia": timedelta(days=1),
    "semana": timedelta(days=7),
    "mes": timedelta(days=30),
}


# Caminhos de licença observados nas instalações 2.x. A versão muda o
# caminho, e um caminho chutado devolveria 404 sem explicar nada — então
# tenta-se em ordem e o primeiro que responder ganha. O que foi tentado
# volta na resposta: quando nenhum responde, a tela diz exatamente onde
# procurou em vez de dizer só "não encontrei".
CAMINHOS_LICENCA = (
    "/licenses/ffsecurity/",
    "/license/",
    "/licenses/",
    "/licenses/ffsecurity/current/",
)

# Nomes de campo que carregam limite e uso. Não existe contrato público
# para o corpo da licença, então em vez de fixar um formato o serviço
# percorre o JSON e reconhece estes nomes onde eles estiverem.
CHAVES_LIMITE = ("limit", "max", "maximum", "allowed", "quota", "total")
CHAVES_USADO = ("used", "current", "in_use", "usage", "actual", "count")

# Sob estas chaves, cada número folha é um recurso licenciado
# ("limits": {"cameras": 100, "faces": 500000}).
CHAVES_MAPA = ("limits", "features", "modules", "products", "counts", "quotas")


def _num(valor):
    """Número inteiro, ou None. `True` não é 1 aqui."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return int(valor)
    if isinstance(valor, str):
        texto = valor.strip()
        if texto.lstrip("-").isdigit():
            return int(texto)
    return None


def _itens_licenca(dados) -> list[dict]:
    """
    Achata o corpo da licença em linhas (recurso, limite, usado).

    Escrito para não depender do formato: qualquer nó que tenha um campo de
    limite ou de uso vira uma linha, e mapa de número puro vira uma linha
    por chave. O corpo bruto vai junto na resposta, para o caso de a
    instalação trazer algo que este achatamento não reconheça.
    """
    itens: list[dict] = []
    vistos: set[str] = set()

    def registrar(nome: str, limite, usado) -> None:
        chave = f"{nome}|{limite}|{usado}"
        if chave in vistos:
            return
        vistos.add(chave)
        ilimitado = limite is not None and limite < 0
        restante = None
        if limite is not None and usado is not None and not ilimitado:
            restante = limite - usado
        itens.append({
            "recurso": nome,
            "limite": limite,
            "usado": usado,
            "restante": restante,
            "ilimitado": ilimitado,
        })

    def visitar(no, caminho: str) -> None:
        if isinstance(no, dict):
            limite = usado = None
            for chave, valor in no.items():
                n = _num(valor)
                if n is None:
                    continue
                baixa = chave.lower()
                if baixa in CHAVES_LIMITE and limite is None:
                    limite = n
                elif baixa in CHAVES_USADO and usado is None:
                    usado = n
            if limite is not None or usado is not None:
                nome = no.get("name") or no.get("title") or caminho or "licença"
                registrar(str(nome), limite, usado)

            ultimo = caminho.split(".")[-1].lower()
            for chave, valor in no.items():
                if isinstance(valor, (dict, list)):
                    visitar(valor, f"{caminho}.{chave}" if caminho else chave)
                elif ultimo in CHAVES_MAPA:
                    n = _num(valor)
                    if n is not None:
                        registrar(str(chave), n, None)
        elif isinstance(no, list):
            for i, valor in enumerate(no):
                visitar(valor, caminho or f"item {i + 1}")

    visitar(dados, "")
    return itens


class FFApiError(Exception):
    pass


def configurado(host) -> bool:
    return bool(host.ff_api_url and host.ff_api_token_enc)


class FFApiService:
    """Usa httpx se disponível; senão, urllib numa thread. Sem dependência nova obrigatória."""

    def __init__(self) -> None:
        self._httpx = None
        try:
            import httpx  # noqa: F401
            self._httpx = httpx
        except Exception:
            self._httpx = None

    async def _get(self, url: str, token: str, params: dict | None = None) -> dict:
        headers = {"Authorization": f"Token {token}", "Accept": "application/json"}

        if self._httpx is not None:
            try:
                async with self._httpx.AsyncClient(
                    timeout=30, verify=True, follow_redirects=True
                ) as cli:
                    r = await cli.get(url, headers=headers, params=params)
                    if r.status_code == 401:
                        raise FFApiError("token da API do FindFace inválido ou expirado")
                    if r.status_code >= 400:
                        raise FFApiError(f"API respondeu {r.status_code}: {r.text[:200]}")
                    return r.json()
            except FFApiError:
                raise
            except Exception as exc:
                raise FFApiError(f"falha ao falar com a API: {exc}") from exc

        # Fallback sem httpx — urllib numa thread para não travar o loop
        import json
        import urllib.parse
        import urllib.request

        alvo = url
        if params:
            alvo = f"{url}?{urllib.parse.urlencode(params)}"

        def _fetch() -> dict:
            req = urllib.request.Request(alvo, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    raise FFApiError("token da API do FindFace inválido ou expirado")
                raise FFApiError(f"API respondeu {exc.code}") from exc
            except Exception as exc:
                raise FFApiError(f"falha ao falar com a API: {exc}") from exc

        return await asyncio.to_thread(_fetch)

    async def testar(self, host) -> dict:
        """Confere URL e token com uma consulta barata."""
        if not configurado(host):
            raise FFApiError("URL ou token da API não cadastrados neste servidor")
        token = decrypt_secret(host.ff_api_token_enc)
        base = host.ff_api_url.rstrip("/")

        dados = await self._get(f"{base}/cameras/count/", token)
        total = dados.get("count", dados.get("total", dados))
        return {"ok": True, "cameras": total, "url": base}

    async def licenca(self, host) -> dict:
        """
        Licenciamento: o que está liberado, o que está em uso, o que sobra.

        A pergunta que abre qualquer conversa de expansão — "cabem quantas
        câmeras ainda?" — só tinha resposta entrando na interface da
        NtechLab. Aqui ela vem pela API, junto com a contagem real de
        câmeras cadastradas: limite sem uso não diz nada.

        Só leitura, uma chamada por caminho tentado. Não mexe em nada.
        """
        if not configurado(host):
            raise FFApiError(
                "URL ou token da API do FindFace não cadastrados neste servidor"
            )

        token = decrypt_secret(host.ff_api_token_enc)
        base = host.ff_api_url.rstrip("/")

        bruto = None
        caminho_ok = ""
        tentativas: list[dict] = []
        for caminho in CAMINHOS_LICENCA:
            try:
                bruto = await self._get(f"{base}{caminho}", token)
                caminho_ok = caminho
                break
            except FFApiError as exc:
                tentativas.append({"caminho": caminho, "erro": str(exc)[:200]})

        if bruto is None:
            raise FFApiError(
                "nenhum caminho de licença respondeu. Tentados: "
                + "; ".join(f"{t['caminho']} → {t['erro']}" for t in tentativas)
            )

        # Câmeras cadastradas de verdade. É o número que confronta o limite
        # licenciado — e é barato: /count/ devolve só o total.
        cameras = None
        try:
            dados = await self._get(f"{base}/cameras/count/", token)
            cameras = _num(dados.get("count", dados.get("total"))) if isinstance(dados, dict) else None
        except FFApiError:
            cameras = None

        itens = _itens_licenca(bruto)

        # Fecha a conta da câmera quando a licença dá o limite e não diz o
        # uso: quem olha a tela quer "42 de 100", não "limite 100".
        if cameras is not None:
            for item in itens:
                if "camera" in item["recurso"].lower() and item["usado"] is None:
                    item["usado"] = cameras
                    if item["limite"] is not None and not item["ilimitado"]:
                        item["restante"] = item["limite"] - cameras

        return {
            "via": "api",
            "url": base,
            "caminho": caminho_ok,
            "tentativas": tentativas,
            "cameras_cadastradas": cameras,
            "itens": itens,
            "bruto": bruto,
        }

    async def listar(self, host, periodo: str = "dia") -> dict:
        """
        Câmeras + contagem de eventos por período, via API.

        Uma chamada lista as câmeras; para cada tipo de evento, uma
        contagem com filtro de data. É barato: `/count/` devolve só o
        número, não os eventos.
        """
        if periodo not in PERIODOS:
            raise FFApiError(f"período inválido: {periodo}")
        if not configurado(host):
            raise FFApiError("URL ou token da API não cadastrados")

        token = decrypt_secret(host.ff_api_token_enc)
        base = host.ff_api_url.rstrip("/")
        desde = (datetime.now(timezone.utc) - PERIODOS[periodo]).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        rotulo = {
            "hora": "última hora", "dia": "últimas 24 horas",
            "semana": "últimos 7 dias", "mes": "últimos 30 dias",
        }[periodo]

        # ── Câmeras ────────────────────────────────────────────────────
        cameras: dict[str, dict] = {}
        proxima = f"{base}/cameras/"
        paginas = 0
        while proxima and paginas < 50:  # trava contra paginação infinita
            dados = await self._get(proxima, token, {"limit": 200} if paginas == 0 else None)
            itens = dados.get("results", dados if isinstance(dados, list) else [])
            for c in itens:
                cid = str(c.get("id", ""))
                if not cid:
                    continue
                cameras[cid] = {
                    "id": cid,
                    "nome": c.get("name") or c.get("comment") or f"camera {cid}",
                    "ativo": bool(c.get("active", c.get("enabled", True))),
                    "ativo_conhecido": "active" in c or "enabled" in c,
                    "grupo": str(c.get("group", c.get("camera_group", ""))),
                    "eventos": 0,
                    "por_tipo": {},
                    "ultimo_evento": None,
                }
            proxima = dados.get("next") if isinstance(dados, dict) else None
            paginas += 1

        # ── Contagem de eventos, por câmera e tipo ─────────────────────
        total_eventos = 0
        for tipo in TIPOS_EVENTO:
            try:
                for cid, cam in cameras.items():
                    dados = await self._get(
                        f"{base}/events/{tipo}/count/",
                        token,
                        {"camera": cid, "created_date_gte": desde},
                    )
                    n = int(dados.get("count", dados.get("total", 0)) or 0)
                    if n:
                        cam["eventos"] += n
                        cam["por_tipo"][tipo] = n
                        total_eventos += n
            except FFApiError:
                # Um tipo pode não existir nesta instalação; segue nos outros
                continue

        lista = sorted(cameras.values(), key=lambda c: (-c["eventos"], c["nome"]))
        for c in lista:
            c["fatia_pct"] = (
                round(c["eventos"] / total_eventos * 100, 1) if total_eventos else 0.0
            )
            c["bytes_estimados"] = 0  # a API não expõe volume por câmera

        return {
            "host": host.name,
            "periodo": periodo,
            "periodo_rotulo": rotulo,
            "total_cameras": len(lista),
            "cameras_com_evento": sum(1 for c in lista if c["eventos"]),
            "cameras_mudas": sum(1 for c in lista if not c["eventos"]),
            "total_eventos": total_eventos,
            "cameras": lista,
            "tabelas": [],
            "esquema": {"banco": "API HTTP", "tabela_cameras": "cameras",
                        "tabelas_eventos": list(TIPOS_EVENTO)},
            "estimativa": False,
            "via": "api",
        }
