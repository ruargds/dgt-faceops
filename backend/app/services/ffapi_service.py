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
import time
from datetime import datetime, timedelta, timezone

import asyncssh  # noqa: F401 — garante o mesmo event loop; import defensivo

from app.core.vault import decrypt_secret

log = logging.getLogger("faceops.ffapi")

# Tipos de evento que o FindFace expõe com /count/
TIPOS_EVENTO = ("faces", "bodies", "cars")

# Quanto uma sessão autenticada é reaproveitada antes de refazer o login.
# 20 minutos é curto o bastante para uma senha trocada valer rápido e longo
# o bastante para não encher o log de autenticação do FindFace.
SESSAO_TTL = 20 * 60


def _sem_verificar_tls():
    """
    Handler de HTTPS que não valida o certificado.

    O FindFace da instalação atende em HTTPS com certificado autoassinado —
    é rede interna, e exigir cadeia válida aqui só impediria o painel de
    ler a própria plataforma que ele opera. Vale para a API do FindFace, e
    para mais nada.
    """
    import ssl
    import urllib.request

    contexto = ssl.create_default_context()
    contexto.check_hostname = False
    contexto.verify_mode = ssl.CERT_NONE
    return urllib.request.HTTPSHandler(context=contexto)


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
# `value` entra porque a tela de licenças do FindFace mostra duas colunas,
# "Used" e "Limits", e o limite chega ora como `limit`, ora como `value`.
CHAVES_LIMITE = ("limit", "limits", "max", "maximum", "allowed", "quota", "total", "value")
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


def _cabecalho_licenca(dados) -> dict:
    """
    Os campos do topo da tela de licenças: identificador, validade, tipo,
    arquivo e se está válida.

    Cada versão nomeia isso de um jeito, então cada campo tem uma lista de
    nomes possíveis e vence o primeiro que existir. O que não for
    encontrado volta vazio — a tela mostra "—" em vez de sumir com a linha.
    """
    if not isinstance(dados, dict):
        return {}

    def primeiro(*nomes):
        for nome in nomes:
            valor = dados.get(nome)
            if isinstance(valor, (str, int, float)) and str(valor).strip():
                return str(valor)
            if isinstance(valor, dict):
                for interno in ("value", "id", "name", "date"):
                    if valor.get(interno):
                        return str(valor[interno])
        return ""

    valido = dados.get("valid")
    if isinstance(valido, dict):
        valido = valido.get("value", valido.get("valid"))
    if isinstance(valido, (int, float)) and not isinstance(valido, bool):
        valido = valido > 0

    return {
        "id": primeiro("id", "license_id", "licenseId", "uuid"),
        "validade": primeiro(
            "expiry_date", "expiryDate", "valid_until", "expire_date", "expires"
        ),
        "tipo": primeiro("type", "type_of_license", "license_type", "source"),
        "arquivo": primeiro("file", "path", "filename"),
        "valido": bool(valido) if valido is not None else None,
    }


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
            virou_linha = limite is not None or usado is not None
            if virou_linha:
                nome = no.get("name") or no.get("title") or caminho or "licença"
                registrar(str(nome), limite, usado)

            ultimo = caminho.split(".")[-1].lower()
            for chave, valor in no.items():
                if isinstance(valor, (dict, list)):
                    visitar(valor, f"{caminho}.{chave}" if caminho else chave)
                elif ultimo in CHAVES_MAPA and not virou_linha:
                    # So quando o no NAO virou linha. Sem esta condicao, um
                    # item {"name": "Extraction API", "used": 1, "value": 128}
                    # dentro de "limits" viraria tres linhas: a certa, mais
                    # uma chamada "used" e outra chamada "value".
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


# Caminhos de login observados nas versões 2.x. Mesma lógica dos caminhos
# de licença: tenta em ordem, o primeiro que autenticar ganha, e o que foi
# tentado volta na mensagem de erro.
CAMINHOS_LOGIN = ("/login/", "/auth/login/", "/v1/login/")


def configurado(host) -> bool:
    """
    Tem como falar com a API deste servidor?

    Usuário e senha é o caminho normal — é assim que se entra no FindFace.
    Token continua valendo para quem gerou um: dispensa o login e não
    expira junto com a sessão.
    """
    if not host.ff_api_url:
        return False
    tem_usuario = bool(getattr(host, "ff_api_user", "") and getattr(host, "ff_api_pass_enc", ""))
    return bool(tem_usuario or host.ff_api_token_enc)


class FFApiService:
    """Usa httpx se disponível; senão, urllib numa thread. Sem dependência nova obrigatória."""

    def __init__(self) -> None:
        self._httpx = None
        try:
            import httpx  # noqa: F401
            self._httpx = httpx
        except Exception:
            self._httpx = None

        # Sessão por host: o login é uma requisição a mais, e repeti-lo a
        # cada leitura encheria o log de autenticação do FindFace de linhas
        # do painel. Guardado só em memória — reiniciou, loga de novo.
        # host_id -> {"token": str, "cookie": str, "em": monotonic}
        self._sessoes: dict[int, dict] = {}

    # ── Autenticação ───────────────────────────────────────────────────

    async def _credenciais(self, host) -> dict:
        """
        Cabeçalhos que autenticam neste host.

        Token, quando houver — é o mais simples e não expira com a sessão.
        Senão, login com usuário e senha, guardando o resultado em memória.
        """
        if host.ff_api_token_enc:
            token = decrypt_secret(host.ff_api_token_enc)
            if token:
                return {"Authorization": f"Token {token}"}

        usuario = getattr(host, "ff_api_user", "") or ""
        senha = decrypt_secret(getattr(host, "ff_api_pass_enc", "") or "")
        if not usuario or not senha:
            raise FFApiError(
                "sem credencial da API: informe usuário e senha do FindFace "
                "no cadastro do servidor"
            )

        chave = getattr(host, "id", None) or host.ff_api_url
        guardada = self._sessoes.get(chave)
        if guardada and (time.monotonic() - guardada["em"]) < SESSAO_TTL:
            return dict(guardada["cabecalhos"])

        cabecalhos = await self._login(host, usuario, senha)
        self._sessoes[chave] = {"cabecalhos": cabecalhos, "em": time.monotonic()}
        return dict(cabecalhos)

    async def _login(self, host, usuario: str, senha: str) -> dict:
        """
        Entra na API e devolve o que autentica as próximas chamadas.

        Duas formas de resposta convivem nas instalações 2.x: JSON com
        token, ou cookie de sessão. O cliente aceita as duas em vez de
        exigir uma — quem opera não escolheu qual a instalação usa.
        """
        base = host.ff_api_url.rstrip("/")
        corpo = {"login": usuario, "password": senha, "username": usuario}
        tentativas = []

        for caminho in CAMINHOS_LOGIN:
            try:
                dados, cookies = await self._post(f"{base}{caminho}", corpo)
            except FFApiError as exc:
                tentativas.append(f"{caminho} → {exc}")
                continue

            token = ""
            if isinstance(dados, dict):
                for campo in ("token", "key", "access_token", "auth_token"):
                    if dados.get(campo):
                        token = str(dados[campo])
                        break
            if token:
                return {"Authorization": f"Token {token}"}
            if cookies:
                return {"Cookie": cookies}
            tentativas.append(f"{caminho} → respondeu sem token nem cookie")

        raise FFApiError(
            "login na API do FindFace falhou. Tentativas: " + "; ".join(tentativas)
        )

    async def _post(self, url: str, corpo: dict) -> tuple:
        """POST de login. Devolve (json, cookies) — cookies como string."""
        cabecalhos = {"Content-Type": "application/json", "Accept": "application/json"}

        if self._httpx is not None:
            try:
                async with self._httpx.AsyncClient(
                    timeout=30, verify=False, follow_redirects=True
                ) as cli:
                    r = await cli.post(url, headers=cabecalhos, json=corpo)
                    if r.status_code in (401, 403):
                        raise FFApiError("usuário ou senha da API recusados")
                    if r.status_code >= 400:
                        raise FFApiError(f"respondeu {r.status_code}")
                    try:
                        dados = r.json()
                    except Exception:
                        dados = {}
                    galheta = "; ".join(f"{k}={v}" for k, v in r.cookies.items())
                    return dados, galheta
            except FFApiError:
                raise
            except Exception as exc:
                raise FFApiError(f"falha ao falar com a API: {exc}") from exc

        import json as _json
        import urllib.error
        import urllib.request
        from http.cookiejar import CookieJar

        def _fetch():
            jar = CookieJar()
            abridor = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(jar), _sem_verificar_tls()
            )
            req = urllib.request.Request(
                url, data=_json.dumps(corpo).encode("utf-8"), headers=cabecalhos
            )
            try:
                with abridor.open(req, timeout=30) as resp:
                    try:
                        dados = _json.loads(resp.read().decode("utf-8"))
                    except Exception:
                        dados = {}
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise FFApiError("usuário ou senha da API recusados")
                raise FFApiError(f"respondeu {exc.code}") from exc
            except Exception as exc:
                raise FFApiError(f"falha ao falar com a API: {exc}") from exc
            galheta = "; ".join(f"{c.name}={c.value}" for c in jar)
            return dados, galheta

        return await asyncio.to_thread(_fetch)

    async def _get(self, url: str, autenticacao, params: dict | None = None) -> dict:
        """
        GET autenticado. `autenticacao` é o dicionário de cabeçalhos vindo
        de `_credenciais()`; string ainda é aceita e vale como token, para
        não quebrar chamada antiga.
        """
        if isinstance(autenticacao, str):
            autenticacao = {"Authorization": f"Token {autenticacao}"}
        headers = {"Accept": "application/json", **(autenticacao or {})}

        if self._httpx is not None:
            try:
                # O FindFace da instalação atende em HTTPS com certificado
                # autoassinado, em rede interna. Exigir cadeia válida aqui
                # só impediria o painel de ler a plataforma que ele opera.
                async with self._httpx.AsyncClient(
                    timeout=30, verify=False, follow_redirects=True
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
            abridor = urllib.request.build_opener(_sem_verificar_tls())
            try:
                with abridor.open(req, timeout=30) as resp:
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
        auth = await self._credenciais(host)
        base = host.ff_api_url.rstrip("/")

        dados = await self._get(f"{base}/cameras/count/", auth)
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

        auth = await self._credenciais(host)
        base = host.ff_api_url.rstrip("/")

        bruto = None
        caminho_ok = ""
        tentativas: list[dict] = []
        for caminho in CAMINHOS_LICENCA:
            try:
                bruto = await self._get(f"{base}{caminho}", auth)
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
            dados = await self._get(f"{base}/cameras/count/", auth)
            cameras = _num(dados.get("count", dados.get("total"))) if isinstance(dados, dict) else None
        except FFApiError:
            cameras = None

        itens = _itens_licenca(bruto)
        cabecalho = _cabecalho_licenca(bruto)

        # Estourado é o que interessa numa tela de licença: na instalação
        # real havia recurso em 2.400.054 de 2.400.000, e esse é o número
        # que trava operação sem avisar ninguém.
        for item in itens:
            item["estourado"] = bool(
                item["limite"] is not None
                and item["usado"] is not None
                and not item["ilimitado"]
                and item["usado"] > item["limite"]
            )

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
            "cabecalho": cabecalho,
            "estourados": sum(1 for i in itens if i.get("estourado")),
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

        auth = await self._credenciais(host)
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
            dados = await self._get(proxima, auth, {"limit": 200} if paginas == 0 else None)
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
