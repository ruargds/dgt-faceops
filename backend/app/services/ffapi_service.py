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
