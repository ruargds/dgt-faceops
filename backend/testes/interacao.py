# -*- coding: utf-8 -*-
"""
Prova da varredura de última interação, com uma API falsa no lugar do
FindFace.

Rode a partir de `backend/`:

    python testes/interacao.py

O que este teste protege, e por que cada um já foi um risco real:

* a primeira aparição de uma câmera no fluxo é o evento MAIS RECENTE dela
  -- se a ordenação sair, a tela mostra data velha com cara de certa;
* a varredura NÃO faz uma chamada por câmera -- era o caminho óbvio e
  custaria mais de mil requisições na API de produção;
* `camera` vem ora como id, ora como objeto aninhado;
* varredura truncada é declarada como truncada, e câmera sem evento não
  vira "nunca".
"""
import asyncio

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import ffapi_service as F

CHAMADAS = []


class Host:
    id = 1
    name = "vm-appserver"
    ff_api_url = "https://10.50.153.11"
    ff_api_token_enc = "x"
    ff_api_user = "admin"
    ff_api_password_enc = "x"


def fabricar(cameras, eventos_por_tipo, por_pagina=2):
    """Cria um _get falso que pagina como o Django REST."""

    async def _get(self, url, auth, params=None):
        CHAMADAS.append(url.split("?")[0])
        if "/cameras/" in url:
            return {"results": cameras, "next": None}
        for tipo, eventos in eventos_por_tipo.items():
            if f"/events/{tipo}/" in url:
                inicio = 0
                if "offset=" in url:
                    inicio = int(url.split("offset=")[1].split("&")[0])
                fatia = eventos[inicio:inicio + por_pagina]
                proxima = (
                    f"https://x/events/{tipo}/?offset={inicio + por_pagina}"
                    if inicio + por_pagina < len(eventos)
                    else None
                )
                return {"results": fatia, "next": proxima}
        raise F.FFApiError("404")

    return _get


async def rodar(cameras, eventos, **kw):
    CHAMADAS.clear()
    s = F.FFApiService()
    F.FFApiService._get = fabricar(cameras, eventos)
    s._credenciais = lambda host: _coro({"Authorization": "Token x"})
    s._base = lambda host: _coro("https://x/api")
    return await s.ultima_interacao(Host(), **kw)


async def _coro(v):
    return v


CAMERAS = [
    {"id": 1, "name": "Portao Norte", "active": True, "modified_date": "2026-08-20T10:00:00Z"},
    {"id": 2, "name": "Recepcao", "active": True},
    {"id": 3, "name": "Doca", "active": False},
]

falhas = []


def checar(nome, cond, detalhe=""):
    print("%-6s %s%s" % ("ok " if cond else "FALHOU", nome,
                         (" -- " + str(detalhe)) if not cond else ""))
    if not cond:
        falhas.append(nome)


async def principal():
    # ── 1. Primeira aparicao = evento mais recente ─────────────────────
    eventos = {
        "faces": [
            {"id": 90, "camera": 1, "created_date": "2026-08-28T09:00:00Z"},
            {"id": 89, "camera": 2, "created_date": "2026-08-28T08:30:00Z"},
            {"id": 88, "camera": 1, "created_date": "2026-08-27T07:00:00Z"},
        ],
        "bodies": [], "cars": [],
    }
    r = await rodar(CAMERAS, eventos)
    por_id = {c["id"]: c for c in r["cameras"]}
    checar("camera 1 pega o evento mais NOVO",
           por_id["1"]["ultima_interacao"].startswith("2026-08-28T09:00"),
           por_id["1"]["ultima_interacao"])
    checar("camera 2 encontrada", por_id["2"]["ultima_interacao"] is not None)
    checar("camera 3 sem evento vira None", por_id["3"]["ultima_interacao"] is None)
    checar("contagem com evento", r["com_interacao"] == 2, r["com_interacao"])
    checar("contagem sem evento", r["sem_interacao"] == 1)
    checar("nome e id vem juntos",
           por_id["1"]["nome"] == "Portao Norte" and por_id["1"]["id"] == "1")
    checar("data do cadastro lida", por_id["1"]["cadastro_em"] is not None)
    checar("campo do cadastro declarado",
           por_id["1"]["cadastro_campo"] == "modified_date",
           por_id["1"]["cadastro_campo"])
    checar("cadastro ausente nao inventa", por_id["2"]["cadastro_em"] is None)
    checar("mudas aparecem primeiro", r["cameras"][0]["id"] == "3",
           r["cameras"][0]["id"])

    # ── 2. Custo: NAO pode ser uma chamada por camera ──────────────────
    por_camera = sum(1 for c in CHAMADAS if "/cameras/" in c)
    checar("uma chamada por camera NAO acontece",
           len(CHAMADAS) < len(CAMERAS) * 3, "%d chamadas" % len(CHAMADAS))
    checar("lista de cameras lida uma vez", por_camera == 1, por_camera)

    # ── 3. Camera aninhada no evento ───────────────────────────────────
    aninhado = {
        "faces": [{"id": 1, "camera": {"id": 2, "name": "Recepcao"},
                   "created_date": "2026-08-28T11:00:00Z"}],
        "bodies": [], "cars": [],
    }
    r = await rodar(CAMERAS, aninhado)
    checar("camera como objeto aninhado e reconhecida",
           {c["id"]: c for c in r["cameras"]}["2"]["ultima_interacao"] is not None)

    # ── 4. camera_id em vez de camera ──────────────────────────────────
    alt = {"faces": [{"id": 1, "camera_id": 3,
                      "created_date": "2026-08-28T11:00:00Z"}],
           "bodies": [], "cars": []}
    r = await rodar(CAMERAS, alt)
    checar("campo camera_id tambem serve",
           {c["id"]: c for c in r["cameras"]}["3"]["ultima_interacao"] is not None)

    # ── 5. Teto: nao mente dizendo "completa" ──────────────────────────
    muitos = {"faces": [{"id": i, "camera": 1,
                         "created_date": "2026-08-2%dT10:00:00Z" % (i % 9)}
                        for i in range(1, 12)],
              "bodies": [], "cars": []}
    r = await rodar(CAMERAS, muitos, max_eventos=4)
    checar("varredura truncada e declarada", r["varredura"]["completa"] is False)
    checar("teto respeitado", r["varredura"]["eventos_lidos"] <= 6,
           r["varredura"]["eventos_lidos"])
    checar("data limite informada", r["varredura"]["ate"] is not None)

    # ── 6. Tipo de evento inexistente nao derruba o resto ──────────────
    parcial = {"faces": [{"id": 1, "camera": 1,
                          "created_date": "2026-08-28T10:00:00Z"}]}
    r = await rodar(CAMERAS, parcial)
    checar("tipo ausente na instalacao nao derruba",
           {c["id"]: c for c in r["cameras"]}["1"]["ultima_interacao"] is not None)

    # ── 7. Instalacao sem camera nenhuma ───────────────────────────────
    r = await rodar([], {"faces": [], "bodies": [], "cars": []})
    checar("sem cameras responde vazio, sem estourar", r["total_cameras"] == 0)

    # ── 8. Conversao de data ───────────────────────────────────────────
    q = F.FFApiService._quando
    checar("epoch em segundos", q(1819832400) is not None)
    checar("epoch em milissegundos", q(1819832400000) is not None)
    checar("booleano nao vira data", q(True) is None)
    checar("texto solto nao vira data", q("ontem") is None)
    checar("sem fuso assume UTC", q("2026-08-28T10:00:00").tzinfo is not None)


asyncio.run(principal())
print()
if falhas:
    print("FALHAS:", falhas)
    raise SystemExit(1)
print("TUDO PASSOU")
