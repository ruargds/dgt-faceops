"""
Identidade visual — logo e cores trocáveis pela web.

Sem isto, atender outro cliente exigiria substituir arquivo no
`frontend/public/logos/` e **reconstruir a imagem**. Numa instalação por
cliente isso é trabalho repetido e uma imagem diferente para manter por
cliente — o oposto de reaproveitar.

Aqui o logo é arquivo em disco (volume que já existe) e as cores são
configuração. A aplicação continua sendo **a mesma imagem** para todos.
"""
import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import client_ip, require_permission
from app.db.database import get_db
from app.models.user import User
from app.services import audit_service

router = APIRouter(prefix="/api/marca", tags=["marca"])

# Onde o logo enviado é guardado. Fica no volume de dados, então
# sobrevive a atualização e a reconstrução da imagem.
PASTA = Path(settings.LOCAL_BACKUP_DIR).parent / "marca"

# Onde estão os arquivos padrão, embutidos na imagem do frontend. O
# backend não os serve — se não houver logo enviado, a rota devolve 404
# e a tela usa o caminho estático, que é o comportamento certo.
TIPOS = {"login", "sidebar", "favicon"}

MAX_BYTES = 2 * 1024 * 1024

# Assinatura no início do arquivo. Confiar no `content-type` enviado pelo
# navegador seria confiar em quem faz o upload; ler os primeiros bytes é
# barato e não engana.
ASSINATURAS = {
    b"\x89PNG\r\n\x1a\n": ("png", "image/png"),
    b"\xff\xd8\xff": ("jpg", "image/jpeg"),
    b"GIF87a": ("gif", "image/gif"),
    b"GIF89a": ("gif", "image/gif"),
}


def _detectar(dados: bytes) -> tuple[str, str] | None:
    for assinatura, info in ASSINATURAS.items():
        if dados.startswith(assinatura):
            return info
    # SVG é texto; procurar a tag no começo evita aceitar qualquer XML
    inicio = dados[:512].lstrip().lower()
    if inicio.startswith(b"<?xml") or inicio.startswith(b"<svg"):
        if b"<svg" in dados[:2048].lower():
            return ("svg", "image/svg+xml")
    return None


def _caminho(tipo: str) -> Path | None:
    if tipo not in TIPOS:
        return None
    for ext in ("png", "jpg", "gif", "svg"):
        alvo = PASTA / f"{tipo}.{ext}"
        if alvo.is_file():
            return alvo
    return None


@router.get("/{tipo}")
async def obter(tipo: str):
    """
    Serve o logo enviado. Sem autenticação — a tela de login precisa dele
    antes de existir sessão, e logo não é segredo.

    404 quando não há logo próprio: a tela cai no arquivo padrão embutido.
    """
    if tipo not in TIPOS:
        raise HTTPException(status_code=404, detail="tipo inválido")

    alvo = _caminho(tipo)
    if alvo is None:
        return Response(status_code=404)

    tipos_mime = {
        "png": "image/png", "jpg": "image/jpeg",
        "gif": "image/gif", "svg": "image/svg+xml",
    }
    return FileResponse(
        alvo,
        media_type=tipos_mime.get(alvo.suffix.lstrip("."), "application/octet-stream"),
        # Curto de propósito: trocar o logo tem que aparecer na hora, e o
        # arquivo é pequeno.
        headers={"Cache-Control": "public, max-age=60"},
    )


@router.post("/{tipo}")
async def enviar(
    tipo: str,
    request: Request,
    arquivo: UploadFile = File(...),
    autor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Envia o logo do cliente. Substitui o anterior, se houver."""
    if tipo not in TIPOS:
        raise HTTPException(
            status_code=400, detail=f"tipo deve ser um de {sorted(TIPOS)}"
        )

    dados = await arquivo.read(MAX_BYTES + 1)
    if len(dados) > MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"arquivo acima de {MAX_BYTES // 1024 // 1024} MB",
        )
    if not dados:
        raise HTTPException(status_code=400, detail="arquivo vazio")

    detectado = _detectar(dados)
    if detectado is None:
        raise HTTPException(
            status_code=400,
            detail="formato não reconhecido. Use PNG, JPG, GIF ou SVG.",
        )
    ext, _mime = detectado

    PASTA.mkdir(parents=True, exist_ok=True)

    # Remove versão anterior em outro formato, senão duas ficariam no
    # disco e a ordem de busca decidiria qual vale.
    for antiga in ("png", "jpg", "gif", "svg"):
        alvo = PASTA / f"{tipo}.{antiga}"
        if alvo.is_file():
            alvo.unlink()

    (PASTA / f"{tipo}.{ext}").write_bytes(dados)

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="config.alterar",
        target=f"marca/{tipo}",
        ip=client_ip(request),
        detail={
            "acao": "logo enviado",
            "formato": ext,
            "bytes": len(dados),
            "sha256": hashlib.sha256(dados).hexdigest()[:16],
        },
    )
    return {"ok": True, "tipo": tipo, "formato": ext, "bytes": len(dados)}


@router.delete("/{tipo}")
async def remover(
    tipo: str,
    request: Request,
    autor: User = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Volta ao logo padrão."""
    if tipo not in TIPOS:
        raise HTTPException(status_code=400, detail="tipo inválido")

    alvo = _caminho(tipo)
    if alvo is not None:
        alvo.unlink()

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="config.alterar",
        target=f"marca/{tipo}",
        ip=client_ip(request),
        detail={"acao": "logo removido — voltou ao padrão"},
    )
    return {"ok": True}


@router.get("")
async def situacao():
    """Quais logos foram personalizados. Público, para a tela decidir."""
    return {tipo: _caminho(tipo) is not None for tipo in sorted(TIPOS)}
