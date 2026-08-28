# -*- coding: utf-8 -*-
"""
Checagens que o Python não faz sozinho, e que já custaram tela quebrada.

Rode antes de commitar:

    python revisar.py

Cada checagem aqui nasceu de um defeito real que chegou em produção
calado — nenhuma é hipotética. Sai com código 1 se achar algo, para
poder virar passo de CI.

IMPORTANTE: rode no MESMO FastAPI da produção (o de `requirements.txt`).
A checagem de rota encoberta depende da versão: da 0.116 em diante o
FastAPI reordena as rotas sozinho e o defeito não reproduz — mas a
produção roda 0.115.6, onde ele é real.
"""
import ast
import io
import pathlib
import sys

ACHADOS: list[str] = []


def achado(texto: str) -> None:
    ACHADOS.append(texto)
    print("  " + texto)


# ── 1. Rota estática encoberta por rota dinâmica ───────────────────────
def rotas_encobertas() -> None:
    """
    O FastAPI casa rotas na ORDEM em que foram declaradas.

    Com `/backups/{run_id}` declarada antes, uma chamada a
    `/backups/download` entrava por ela com run_id="download". O sintoma
    não é 404: é o erro da OUTRA rota (401 de autenticação), que manda
    quem investiga para o lugar errado.
    """
    print("rotas encobertas por rota com parâmetro")
    import app.main as m
    from fastapi.routing import APIRoute

    rotas = [(r.path, set(r.methods)) for r in _todas(m.app) if isinstance(r, APIRoute)]
    for i, (caminho, metodos) in enumerate(rotas):
        segs = caminho.strip("/").split("/")
        if any("{" in s for s in segs):
            continue
        for anterior, pmetodos in rotas[:i]:
            psegs = anterior.strip("/").split("/")
            if len(psegs) != len(segs) or not any("{" in s for s in psegs):
                continue
            casa = all(("{" in p) or p == s for p, s in zip(psegs, segs))
            if casa and (metodos & pmetodos):
                achado(f"{caminho} nunca é alcançada — {anterior} casa antes")
                break


def _todas(no, visto=None):
    """Anda a árvore de rotas, incluindo routers incluídos e sub-apps."""
    visto = visto if visto is not None else set()
    if id(no) in visto:
        return []
    visto.add(id(no))
    saida = []
    for r in getattr(no, "routes", []):
        saida.append(r)
        for attr in ("routes", "router", "app", "original_router"):
            filho = getattr(r, attr, None)
            if filho is not None and filho is not r:
                saida.extend(_todas(filho if attr != "routes" else r, visto))
                break
    return saida


# ── 2. Nome usado antes de existir, no mesmo escopo ────────────────────
def usados_antes(raiz: pathlib.Path) -> None:
    """
    `nonlocal x` num escopo que nunca atribui `x` é resto de lógica que
    saiu — inofensivo, mas indica que alguém mexeu e não terminou.
    """
    print("nonlocal que nunca atribui")
    for p in sorted(raiz.rglob("*.py")):
        try:
            arv = ast.parse(io.open(p, encoding="utf-8").read())
        except SyntaxError:
            continue
        for no in ast.walk(arv):
            if not isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            declarados = {
                n for f in ast.walk(no) if isinstance(f, ast.Nonlocal)
                for n in f.names
            }
            if not declarados:
                continue
            atribuidos = set()
            for f in ast.walk(no):
                if isinstance(f, ast.Name) and isinstance(f.ctx, ast.Store):
                    atribuidos.add(f.id)
                elif isinstance(f, ast.AugAssign) and isinstance(f.target, ast.Name):
                    atribuidos.add(f.target.id)
            for nome in sorted(declarados - atribuidos):
                achado(f"{p.as_posix()}:{no.lineno} nonlocal '{nome}' nunca atribuído")


# ── 3. Tabela que cresce sozinha e não tem varredura ───────────────────
def retencao(raiz: pathlib.Path) -> None:
    """
    Regra da casa: nada é salvo sem prazo. Toda tabela com carimbo de
    tempo cresce para sempre se ninguém apagar — e disco cheio no painel
    é o painel virando o incidente que ele existe para evitar.
    """
    print("tabela com carimbo de tempo sem varredura por prazo")
    fonte = "\n".join(
        io.open(p, encoding="utf-8").read()
        for p in raiz.rglob("*.py")
        if "services" in p.as_posix() or "routes" in p.as_posix()
    )
    # Cadastro: cresce por ação de pessoa, some por ação de pessoa.
    cadastro = {"User", "Host", "Destino", "Schedule", "Configuracao", "VisaoLog"}
    for p in sorted((raiz / "models").rglob("*.py")):
        arv = ast.parse(io.open(p, encoding="utf-8").read())
        for no in ast.walk(arv):
            if not isinstance(no, ast.ClassDef) or no.name in cadastro:
                continue
            corpo = ast.dump(no)
            if "DateTime" not in corpo:
                continue
            if f"delete({no.name})" not in fonte:
                achado(f"{no.name} guarda data e ninguém apaga por prazo")


def main() -> int:
    raiz = pathlib.Path(__file__).parent / "app"
    for checagem in (rotas_encobertas, usados_antes, retencao):
        antes = len(ACHADOS)
        checagem(raiz) if checagem is not rotas_encobertas else checagem()
        if len(ACHADOS) == antes:
            print("  nada")
        print()
    if ACHADOS:
        print(f"{len(ACHADOS)} achado(s)")
        return 1
    print("limpo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
