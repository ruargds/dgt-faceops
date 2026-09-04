"""
Manutenção de disco e log — pela web, sem linha de comando.

Toda ação que escreve tem modo simulação, e a UI mostra o conteúdo exato
dos arquivos antes de aplicar. Nada aqui reinicia o Face Detect.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_permission
from app.db.database import get_db
from app.models.host import Host
from app.models.user import User
from app.services import audit_service
from app.services.maintenance_service import ManutencaoError
from app.services.ssh_service import SSHError

# Palavra da confirmação por digitação da limpeza pontual. Não é o nome de
# um servidor porque a ação é do PAINEL — não há servidor para nomear.
PALAVRA_LIMPEZA = "LIMPAR"

router = APIRouter(prefix="/api/manutencao", tags=["manutencao"])


class ContencaoIn(BaseModel):
    simular: bool = True
    # Dupla confirmação quando for aplicar de verdade
    confirmar_host: str = ""


class ItemLimpezaIn(BaseModel):
    opcao: str = Field(min_length=4, max_length=64)
    dias: int = Field(ge=0, le=3650)


class LimpezaIn(BaseModel):
    itens: list[ItemLimpezaIn]
    confirmar_host: str = ""


class FaxinaPontualIn(BaseModel):
    """
    Limpeza pontual do painel: o que sai e a partir de quantos dias.

    `simular` (padrão) só conta. Aplicar exige a palavra de confirmação —
    apagar histórico não tem volta, e "tem certeza? [OK]" vira reflexo na
    terceira vez (regra 30).
    """

    categorias: list[str] = Field(default_factory=list, max_length=12)
    dias: int = Field(default=90, ge=1, le=3650)
    simular: bool = True
    confirmar: str = ""


class ArquivarIn(BaseModel):
    destino: str = Field(min_length=1, max_length=255)
    simular: bool = True
    incluir_ativo: bool = False
    confirmar_host: str = ""


async def _host_ou_404(db: AsyncSession, host_id: int) -> Host:
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")
    if not host.enabled:
        raise HTTPException(status_code=400, detail=f"servidor '{host.name}' está desativado")
    return host


@router.get("/{host_id}")
async def diagnostico(
    host_id: int,
    request: Request,
    _: User = Depends(require_permission("maintenance.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Diagnóstico de disco e log. Só leitura.

    Demora ~20s: mede o crescimento do syslog amostrando o tamanho duas
    vezes. É o único número que diz se a contenção vale a pena — e
    depois, se ela funcionou.
    """
    host = await _host_ou_404(db, host_id)
    try:
        return await request.app.state.manutencao.diagnostico(host)
    except SSHError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{host_id}/contencao")
async def contencao(
    host_id: int,
    dados: ContencaoIn,
    request: Request,
    autor: User = Depends(require_permission("maintenance.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Aplica (ou simula) a contenção de log.

    Simular exige só `maintenance.view` — ver o que mudaria não muda nada.
    Aplicar exige `maintenance.apply` e confirmação por digitação.
    """
    host = await _host_ou_404(db, host_id)

    if not dados.simular:
        from app.core.permissions import permissions_for

        if "maintenance.apply" not in permissions_for(autor.role, autor.is_super_admin):
            raise HTTPException(
                status_code=403,
                detail=f"Seu perfil ({autor.role}) pode simular, mas não aplicar. "
                       "Necessária a permissão 'maintenance.apply'.",
            )
        if dados.confirmar_host.strip() != host.name:
            raise HTTPException(
                status_code=400,
                detail=f"confirmação necessária: digite exatamente '{host.name}'. "
                       "Esta ação escreve configuração de sistema e reinicia o "
                       "rsyslog e o journald (o Face Detect não é afetado).",
            )

    try:
        resultado = await request.app.state.manutencao.aplicar_contencao(
            host, simular=dados.simular
        )
    except (SSHError, ManutencaoError) as exc:
        if not dados.simular:
            await audit_service.registrar(
                db,
                usuario=autor.username,
                action="maintenance.apply",
                target=host.name,
                ip=client_ip(request),
                success=False,
                level="critical",
                detail={"acao": "contencao", "erro": str(exc)[:600]},
            )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not dados.simular:
        await audit_service.registrar(
            db,
            usuario=autor.username,
            action="maintenance.apply",
            target=host.name,
            ip=client_ip(request),
            level="critical",
            detail={
                "acao": "contencao de log",
                "arquivos": [a["caminho"] for a in resultado["alteracoes"]],
            },
        )
    return resultado


@router.get("/{host_id}/limpeza/opcoes")
async def limpeza_opcoes(
    host_id: int,
    request: Request,
    _: User = Depends(require_permission("maintenance.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    O que o `manage.py cleanup` daquele servidor aceita.

    A lista vem do próprio `--help` do servidor, não de uma tabela minha:
    ela muda entre versões, e uma opção inventada faria o comando falhar
    inteiro — depois de o operador já ter confirmado.
    """
    from app.services.limpeza_service import LimpezaError

    host = await _host_ou_404(db, host_id)
    try:
        dados = await request.app.state.limpeza.opcoes(host, request.app.state.stack)
    except (SSHError, LimpezaError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    dados["em_andamento"] = request.app.state.limpeza.em_andamento(host_id)
    dados["ultimo"] = request.app.state.limpeza.ultimo(host_id)
    return dados


@router.post("/{host_id}/limpeza")
async def limpeza_executar(
    host_id: int,
    dados: LimpezaIn,
    request: Request,
    autor: User = Depends(require_permission("cleanup.run")),
    db: AsyncSession = Depends(get_db),
):
    """
    Apaga eventos antigos. **Irreversível — não há lixeira.**

    Exige digitar o nome do servidor. É a ação mais destrutiva do painel:
    apaga dado de produção que nenhum backup essencial recupera.
    """
    from app.services.limpeza_service import LimpezaError

    host = await _host_ou_404(db, host_id)

    if dados.confirmar_host.strip() != host.name:
        raise HTTPException(
            status_code=400,
            detail=(
                f"confirmação necessária: digite exatamente '{host.name}'. "
                "Esta ação APAGA eventos de produção e não tem volta."
            ),
        )

    itens = [i.model_dump() for i in dados.itens]

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="cleanup.run",
        target=host.name,
        ip=client_ip(request),
        level="critical",
        detail={"acao": "iniciada", "itens": itens},
    )

    try:
        resultado = await request.app.state.limpeza.executar(
            host, request.app.state.stack, itens
        )
    except (SSHError, LimpezaError) as exc:
        await audit_service.registrar(
            db,
            usuario=autor.username,
            action="cleanup.run",
            target=host.name,
            ip=client_ip(request),
            success=False,
            level="critical",
            detail={"acao": "falhou", "erro": str(exc)[:800]},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="cleanup.run",
        target=host.name,
        ip=client_ip(request),
        level="critical",
        detail={
            "acao": "concluida",
            "itens": itens,
            "duracao_ms": resultado.get("duracao_ms"),
        },
    )
    return resultado


@router.get("/faxina/previa")
async def faxina_previa(
    request: Request,
    _: User = Depends(require_permission("maintenance.view")),
):
    """
    O que a faxina removeria agora. Só leitura.

    Não recebe host_id: a faxina age no PAINEL, não nos servidores.
    """
    return await request.app.state.faxina.previa()


@router.post("/faxina/executar")
async def faxina_executar(
    request: Request,
    autor: User = Depends(require_permission("maintenance.apply")),
    db: AsyncSession = Depends(get_db),
):
    """Roda a faxina fora de hora."""
    resultado = await request.app.state.faxina.executar()
    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="maintenance.apply",
        target="painel",
        ip=client_ip(request),
        detail={"acao": "faxina manual", **{
            k: v for k, v in resultado.items() if isinstance(v, (int, float))
        }},
    )
    return resultado


@router.post("/faxina/pontual")
async def faxina_pontual(
    dados: FaxinaPontualIn,
    request: Request,
    autor: User = Depends(require_permission("maintenance.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Limpeza pontual do painel — só o que foi marcado, só acima da idade dada.

    Serve ao caso que a faxina diária não resolve sem efeito colateral:
    liberar disco AGORA de uma categoria específica sem mexer na retenção
    configurada, que vale para todo dia.

    `simular` (padrão) apenas conta. Aplicar exige `maintenance.apply` e a
    palavra de confirmação; o piso de idade do serviço vale nos dois casos.
    """
    from app.services.faxina_service import CATEGORIAS_PONTUAIS

    desconhecidas = [c for c in dados.categorias if c not in CATEGORIAS_PONTUAIS]
    if desconhecidas:
        raise HTTPException(
            status_code=400,
            detail=(
                "categoria desconhecida: " + ", ".join(desconhecidas) + ". "
                "Aceitas: " + ", ".join(CATEGORIAS_PONTUAIS)
            ),
        )
    if not dados.categorias:
        raise HTTPException(status_code=400, detail="selecione ao menos uma categoria")

    if dados.simular:
        return await request.app.state.faxina.pontual(dados.categorias, dados.dias)

    from app.core.permissions import permissions_for

    if "maintenance.apply" not in permissions_for(autor.role, autor.is_super_admin):
        raise HTTPException(
            status_code=403,
            detail=f"Seu perfil ({autor.role}) pode simular, mas não aplicar.",
        )
    if dados.confirmar.strip() != PALAVRA_LIMPEZA:
        raise HTTPException(
            status_code=400,
            detail=(
                f"confirmação necessária: digite exatamente '{PALAVRA_LIMPEZA}'. "
                "Esta ação apaga histórico do painel e não tem volta."
            ),
        )

    resultado = await request.app.state.faxina.pontual(
        dados.categorias, dados.dias, aplicar=True
    )
    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="maintenance.apply",
        target="painel",
        ip=client_ip(request),
        level="critical",
        detail={
            "acao": "limpeza pontual",
            "categorias": resultado["categorias"],
            "dias": resultado["dias"],
            **{
                k: v
                for k, v in resultado.items()
                if isinstance(v, int) and k not in ("dias", "minimo")
            },
        },
    )
    return resultado


@router.post("/{host_id}/arquivar")
async def arquivar(
    host_id: int,
    dados: ArquivarIn,
    request: Request,
    autor: User = Depends(require_permission("maintenance.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Move log rotacionado para um disco com folga. **Não apaga nada.**

    O `syslog` ativo só entra se `incluir_ativo` — e mesmo assim é
    copiado antes de ser zerado.
    """
    host = await _host_ou_404(db, host_id)

    if not dados.simular:
        from app.core.permissions import permissions_for

        if "maintenance.apply" not in permissions_for(autor.role, autor.is_super_admin):
            raise HTTPException(
                status_code=403,
                detail=f"Seu perfil ({autor.role}) pode simular, mas não aplicar.",
            )
        if dados.confirmar_host.strip() != host.name:
            raise HTTPException(
                status_code=400,
                detail=f"confirmação necessária: digite exatamente '{host.name}'.",
            )

    try:
        resultado = await request.app.state.manutencao.arquivar_logs(
            host,
            dados.destino,
            simular=dados.simular,
            incluir_ativo=dados.incluir_ativo,
        )
    except (SSHError, ManutencaoError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not dados.simular:
        await audit_service.registrar(
            db,
            usuario=autor.username,
            action="maintenance.apply",
            target=host.name,
            ip=client_ip(request),
            level="critical",
            detail={
                "acao": "arquivar log",
                "destino": dados.destino,
                "arquivos": len(resultado["candidatos"]),
                "bytes": resultado["total_bytes"],
                "incluiu_ativo": dados.incluir_ativo,
            },
        )
    return resultado
