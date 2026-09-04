"""Cadastro dos servidores e teste de conexão."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rede_segura import DestinoRecusado, validar_url
from app.services.comandos_rapidos import COMANDOS, catalogo
from app.core.permissions import permissions_for
from app.core.deps import get_current_user, client_ip, require_permission
from app.core.vault import encrypt_secret, fingerprint
from app.db.database import get_db
from app.models.host import Host
from app.models.user import User
from app.schemas import ComandoRapidoIn, HostIn, HostOut, HostUpdate, ScanChaveIn, ScanChaveOut
from app.services import audit_service
from app.services.ssh_service import SSHError

router = APIRouter(prefix="/api/hosts", tags=["hosts"])


def _para_out(host: Host) -> HostOut:
    saida = HostOut.model_validate(host)
    saida.tem_credencial = bool(host.ssh_key_enc or host.ssh_password_enc)
    saida.tem_sudo = bool(host.sudo_password_enc)
    saida.tem_api = bool(
        host.ff_api_url and (host.ff_api_pass_enc or host.ff_api_token_enc)
    )
    return saida


@router.post("/scan-chave", response_model=ScanChaveOut)
async def scan_chave(
    dados: ScanChaveIn,
    _: User = Depends(require_permission("hosts.manage")),
):
    """
    Lê a chave pública do servidor SEM autenticar.

    Passo obrigatório antes de cadastrar credenciais: é o que permite
    fixar a identidade do host e recusar um impostor na rede depois.
    """
    from app.services.ssh_service import SSHService

    try:
        pub, fp = await SSHService.scan_host_key(dados.address, dados.ssh_port)
    except SSHError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ScanChaveOut(host_key_pub=pub, fingerprint=fp)


@router.get("", response_model=list[HostOut])
async def listar(
    _: User = Depends(require_permission("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    resultado = await db.execute(select(Host).order_by(Host.name))
    return [_para_out(h) for h in resultado.scalars().all()]


@router.get("/{host_id}", response_model=HostOut)
async def obter(
    host_id: int,
    _: User = Depends(require_permission("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")
    return _para_out(host)


@router.post("", response_model=HostOut, status_code=201)
async def criar(
    dados: HostIn,
    request: Request,
    autor: User = Depends(require_permission("hosts.manage")),
    db: AsyncSession = Depends(get_db),
):
    from app.services.ssh_service import SSHService

    existente = await db.execute(select(Host).where(Host.name == dados.name))
    if existente.scalars().first() is not None:
        raise HTTPException(status_code=409, detail=f"já existe servidor '{dados.name}'")

    if dados.auth_method == "key" and not dados.ssh_key:
        raise HTTPException(status_code=400, detail="chave PEM obrigatória para auth por chave")
    if dados.auth_method == "password" and not dados.ssh_password:
        raise HTTPException(status_code=400, detail="senha obrigatória para auth por senha")

    # Fixa a identidade do servidor antes de guardar qualquer credencial
    try:
        pub, fp = await SSHService.scan_host_key(dados.address, dados.ssh_port)
    except SSHError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"não consegui alcançar {dados.address}:{dados.ssh_port} — {exc}",
        ) from exc

    # A URL da API do Face Detect é endereço escolhido por quem cadastra —
    # o formato clássico de SSRF. A cerca barra link-local (onde vive o
    # IMDS do Azure, que entrega token de identidade sem autenticação),
    # loopback e esquema que não seja http/https.
    if dados.ff_api_url:
        try:
            validar_url(dados.ff_api_url)
        except DestinoRecusado as exc:
            raise HTTPException(
                status_code=400, detail=f"endereço da API recusado: {exc}"
            ) from exc

    host = Host(
        name=dados.name,
        alias=dados.alias.strip(),
        description=dados.description,
        role=dados.role,
        address=dados.address,
        ssh_port=dados.ssh_port,
        ssh_user=dados.ssh_user,
        auth_method=dados.auth_method,
        ssh_key_enc=encrypt_secret(dados.ssh_key or ""),
        ssh_key_passphrase_enc=encrypt_secret(dados.ssh_key_passphrase or ""),
        ssh_password_enc=encrypt_secret(dados.ssh_password or ""),
        sudo_password_enc=encrypt_secret(dados.sudo_password or ""),
        host_key_pub=pub,
        host_key_fingerprint=fp,
        key_fingerprint=fingerprint(dados.ssh_key or dados.ssh_password or ""),
        ffmulti_dir=dados.ffmulti_dir or settings.FFMULTI_DIR,
        compose_file=dados.compose_file or settings.FFMULTI_COMPOSE,
        has_gpu=dados.has_gpu,
        enabled=dados.enabled,
        monitorar=dados.monitorar,
        ff_api_url=dados.ff_api_url,
        ff_api_user=dados.ff_api_user or "",
        ff_api_pass_enc=encrypt_secret(dados.ff_api_pass or ""),
        ff_api_token_enc=encrypt_secret(dados.ff_api_token or ""),
    )
    db.add(host)
    await db.commit()
    # O resumo do Monitor é cacheado por ciclo do coletor. Sem invalidar
    # aqui, esta alteração só apareceria na próxima passada — e "salvei e
    # não mudou nada" vira chamado.
    try:
        request.app.state.monitor.invalidar()
    except Exception:
        pass
    await db.refresh(host)

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="hosts.manage",
        target=host.name,
        ip=client_ip(request),
        detail={
            "acao": "criar",
            "endereco": f"{host.address}:{host.ssh_port}",
            "usuario_ssh": host.ssh_user,
            "host_key": fp,
        },
    )
    return _para_out(host)


@router.patch("/{host_id}", response_model=HostOut)
async def atualizar(
    host_id: int,
    dados: HostUpdate,
    request: Request,
    autor: User = Depends(require_permission("hosts.manage")),
    db: AsyncSession = Depends(get_db),
):
    from app.services.ssh_service import SSHService

    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")

    alterados: list[str] = []

    if dados.ff_api_url:
        try:
            validar_url(dados.ff_api_url)
        except DestinoRecusado as exc:
            raise HTTPException(
                status_code=400, detail=f"endereço da API recusado: {exc}"
            ) from exc

    for campo in (
        "name", "alias", "description", "role", "ssh_user", "auth_method",
        "ffmulti_dir", "compose_file", "has_gpu", "enabled", "monitorar",
        "ff_api_url", "ff_api_user", "ssh_port",
    ):
        valor = getattr(dados, campo)
        if valor is not None and getattr(host, campo) != valor:
            setattr(host, campo, valor)
            alterados.append(campo)

    # Endereço ou porta novos exigem nova varredura — a identidade fixada
    # vale para o par (endereço, porta) antigo, não para o novo.
    if dados.address is not None and dados.address != host.address:
        porta = dados.ssh_port or host.ssh_port
        try:
            pub, fp = await SSHService.scan_host_key(dados.address, porta)
        except SSHError as exc:
            raise HTTPException(
                status_code=400, detail=f"não consegui alcançar o novo endereço — {exc}"
            ) from exc
        host.address = dados.address
        host.host_key_pub = pub
        host.host_key_fingerprint = fp
        alterados += ["address", "host_key"]

    if dados.ssh_key is not None:
        host.ssh_key_enc = encrypt_secret(dados.ssh_key)
        host.key_fingerprint = fingerprint(dados.ssh_key)
        alterados.append("ssh_key")
    if dados.ssh_key_passphrase is not None:
        host.ssh_key_passphrase_enc = encrypt_secret(dados.ssh_key_passphrase)
        alterados.append("ssh_key_passphrase")
    if dados.ssh_password is not None:
        host.ssh_password_enc = encrypt_secret(dados.ssh_password)
        alterados.append("ssh_password")
    if dados.sudo_password is not None:
        host.sudo_password_enc = encrypt_secret(dados.sudo_password)
        alterados.append("sudo_password")
    if dados.ff_api_pass is not None:
        host.ff_api_pass_enc = encrypt_secret(dados.ff_api_pass)
        alterados.append("ff_api_pass")
    if dados.ff_api_token is not None:
        host.ff_api_token_enc = encrypt_secret(dados.ff_api_token)
        alterados.append("ff_api_token")

    await db.commit()
    # O resumo do Monitor é cacheado por ciclo do coletor. Sem invalidar
    # aqui, esta alteração só apareceria na próxima passada — e "salvei e
    # não mudou nada" vira chamado.
    try:
        request.app.state.monitor.invalidar()
    except Exception:
        pass
    await db.refresh(host)

    # Credencial ou endereço mudou: derruba a conexão em cache, senão a
    # próxima operação seguiria usando a sessão aberta com a chave antiga.
    await request.app.state.ssh.disconnect(host_id)

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="hosts.manage",
        target=host.name,
        ip=client_ip(request),
        detail={"acao": "atualizar", "campos": alterados},
    )
    return _para_out(host)


@router.delete("/{host_id}")
async def remover(
    host_id: int,
    request: Request,
    autor: User = Depends(require_permission("hosts.manage")),
    db: AsyncSession = Depends(get_db),
):
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")

    nome = host.name
    await request.app.state.ssh.disconnect(host_id)
    await db.delete(host)
    await db.commit()
    # O resumo do Monitor é cacheado por ciclo do coletor. Sem invalidar
    # aqui, esta alteração só apareceria na próxima passada — e "salvei e
    # não mudou nada" vira chamado.
    try:
        request.app.state.monitor.invalidar()
    except Exception:
        pass

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="hosts.manage",
        target=nome,
        ip=client_ip(request),
        level="critical",
        detail={"acao": "remover"},
    )
    return {"ok": True}


@router.post("/{host_id}/testar")
async def testar(
    host_id: int,
    request: Request,
    autor: User = Depends(require_permission("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    """Testa conexão, sudo e presença do Face Detect. Botão 'Testar conexão'."""
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")

    ssh = request.app.state.ssh
    try:
        info = await ssh.test(host)
        tem_sudo = await ssh.can_sudo(host)

        ff_dir = host.ffmulti_dir or settings.FFMULTI_DIR
        checagem = await ssh.run(
            host,
            f"test -d {ff_dir} && echo sim || echo nao; "
            "command -v docker >/dev/null 2>&1 && echo sim || echo nao; "
            "command -v nvidia-smi >/dev/null 2>&1 && echo sim || echo nao",
            timeout=30,
        )
        linhas = checagem.stdout.strip().splitlines()

        host.last_seen_at = datetime.now(timezone.utc)
        host.last_status = "ok"
        host.last_error = ""

        # Detecta a GPU sozinho — poupa o operador de marcar na mão
        if len(linhas) > 2 and linhas[2] == "sim" and not host.has_gpu:
            host.has_gpu = True

        # Se o caminho cadastrado não existe no servidor, pergunta ao
        # Docker onde o Face Detect realmente está. Instalação distribuída
        # não usa /opt/findface-multi, e o backup falharia procurando
        # configs/ no lugar errado — com mensagem que não ajuda ninguém.
        instalacao: dict = {}
        corrigido = False
        if linhas and linhas[0] != "sim":
            instalacao = await request.app.state.stack.detectar_instalacao(host)
            if instalacao.get("working_dir"):
                host.ffmulti_dir = instalacao["working_dir"]
                if instalacao.get("compose_file"):
                    host.compose_file = instalacao["compose_file"]
                ff_dir = host.ffmulti_dir
                corrigido = True

        await db.commit()

        if corrigido:
            await audit_service.registrar(
                db,
                usuario=autor.username,
                action="hosts.testar",
                target=host.name,
                ip=client_ip(request),
                detail={
                    "acao": "caminho do Face Detect corrigido por deteccao",
                    "novo_dir": host.ffmulti_dir,
                    "novo_compose": host.compose_file,
                },
            )

        return {
            "ok": True,
            **info,
            "sudo": tem_sudo,
            "findface_presente": (linhas[0] == "sim" if linhas else False) or corrigido,
            "docker_presente": linhas[1] == "sim" if len(linhas) > 1 else False,
            "gpu_presente": linhas[2] == "sim" if len(linhas) > 2 else False,
            "ffmulti_dir": ff_dir,
            "caminho_corrigido": corrigido,
            "instalacao": instalacao,
        }
    except Exception as exc:
        # Qualquer falha no teste vira RESULTADO, nunca 500: o operador
        # precisa ver o motivo (chave inválida, sem rota, sudo negado), não
        # "Internal Server Error". SSHError já traz mensagem legível; para
        # o resto, incluímos o tipo para diagnóstico.
        mensagem = str(exc) if isinstance(exc, SSHError) else f"{type(exc).__name__}: {exc}"

        host.last_status = "erro"
        host.last_error = mensagem[:2000]
        host.last_seen_at = datetime.now(timezone.utc)
        try:
            await db.commit()
        except Exception:
            await db.rollback()

        await audit_service.registrar(
            db,
            usuario=autor.username,
            action="hosts.testar",
            target=host.name,
            ip=client_ip(request),
            success=False,
            detail={"erro": mensagem[:500]},
        )
        # Com causa e ação: "Connection refused" e "timed out" parecem o
        # mesmo problema e apontam para lados opostos — um é o SSH parado
        # na máquina, o outro é rede ou VM fora. Devolver só o erro do
        # sistema manda a pessoa procurar no lugar errado metade das vezes.
        explicado = erros_conexao.explicar(mensagem)
        return {
            "ok": False,
            "erro": mensagem,
            "resumo": explicado["resumo"] if explicado["conhecido"] else "",
            "significa": explicado["significa"],
            "acao": explicado["acao"],
        }


class TestarApiIn(BaseModel):
    ff_api_url: str | None = None
    ff_api_user: str | None = None
    ff_api_pass: str | None = None
    ff_api_token: str | None = None


@router.post("/{host_id}/testar-api")
async def testar_api(
    host_id: int,
    dados: TestarApiIn,
    request: Request,
    autor: User = Depends(require_permission("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    """
    Testa a API do Face Detect deste servidor.

    Usa a URL/token enviados (ainda não salvos) quando vierem; senão, os
    já guardados. Assim dá para validar antes de salvar.
    """
    from types import SimpleNamespace

    from app.core.vault import decrypt_secret
    from app.services.ffapi_service import FFApiError

    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")

    url = (dados.ff_api_url or host.ff_api_url or "").strip()
    if url:
        try:
            validar_url(url)
        except DestinoRecusado as exc:
            raise HTTPException(
                status_code=400, detail=f"endereço da API recusado: {exc}"
            ) from exc

    # O que foi digitado na tela tem precedência sobre o que está salvo —
    # é isso que permite validar a credencial ANTES de gravar.
    usuario = dados.ff_api_user if dados.ff_api_user is not None else host.ff_api_user
    if dados.ff_api_pass:
        senha = dados.ff_api_pass
    else:
        senha = decrypt_secret(host.ff_api_pass_enc) if host.ff_api_pass_enc else ""
    if dados.ff_api_token:
        token = dados.ff_api_token
    else:
        token = decrypt_secret(host.ff_api_token_enc) if host.ff_api_token_enc else ""

    if not url or not ((usuario and senha) or token):
        return {
            "ok": False,
            "erro": "informe a URL e o usuário/senha do Face Detect (ou um token)",
        }

    # objeto leve que o FFApiService entende, sem tocar no banco
    from app.core.vault import encrypt_secret
    alvo = SimpleNamespace(
        id=host.id,
        name=host.name,
        ff_api_url=url,
        ff_api_user=usuario or "",
        ff_api_pass_enc=encrypt_secret(senha or ""),
        ff_api_token_enc=encrypt_secret(token or ""),
    )
    # De onde saiu o que foi testado. Sem isto, testar com senha digitada e
    # esquecer de salvar dá "OK" na tela e "credencial recusada" em todas as
    # leituras seguintes — e nada liga uma coisa à outra.
    if dados.ff_api_token:
        origem = "token digitado"
    elif dados.ff_api_pass:
        origem = "digitada"
    elif token:
        origem = "token salvo"
    else:
        origem = "salva"

    try:
        r = await request.app.state.ffapi.testar(alvo)
        return {
            "ok": True,
            "cameras": r.get("cameras"),
            "url": r.get("url"),
            "usuario": r.get("usuario"),
            "origem": origem,
        }
    except FFApiError as exc:
        return {"ok": False, "erro": str(exc), "origem": origem}


@router.get("/{host_id}/comandos")
async def comandos_disponiveis(
    host_id: int,
    autor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """As ações rápidas que ESTE usuário pode disparar neste servidor."""
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")

    return {"itens": catalogo(permissions_for(autor.role, autor.is_super_admin))}


@router.post("/{host_id}/comandos/{chave}")
async def executar_comando(
    host_id: int,
    chave: str,
    dados: ComandoRapidoIn,
    request: Request,
    autor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Dispara uma ação do catálogo fixo.

    A chave vem da URL, mas o COMANDO vem do catálogo em código — nunca
    do corpo da requisição. É a diferença entre um botão e um shell
    remoto: aqui não existe caminho pelo qual um comando arbitrário
    chegue ao servidor. Para comando arbitrário existe o InTerminal, que
    grava a sessão inteira.
    """
    info = COMANDOS.get(chave)
    if info is None:
        raise HTTPException(status_code=404, detail=f"ação desconhecida: {chave}")

    permissoes = permissions_for(autor.role, autor.is_super_admin)
    if info["permissao"] not in permissoes:
        raise HTTPException(
            status_code=403,
            detail=f"esta ação exige a permissão '{info['permissao']}'",
        )

    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="servidor não encontrado")

    # Confirmação digitada nas destrutivas: o risco não é errar a ação, é
    # errar QUAL servidor. Digitar o nome prova que o dedo estava certo.
    if info["confirmar"] and dados.confirmar.strip() != host.name:
        raise HTTPException(
            status_code=400,
            detail=f"confirme digitando o nome do servidor: '{host.name}'",
        )

    ssh = request.app.state.ssh
    try:
        r = await ssh.run(
            host, info["comando"], sudo=info["sudo"], timeout=info["timeout"]
        )
        saida = (r.stdout or r.stderr or "").strip()
        ok = r.ok
    except (SSHError, Exception) as exc:  # noqa: B014
        if info["derruba"]:
            # A conexão cair FAZ PARTE do sucesso quando a ação derruba a
            # máquina. Tratar isso como falha diria que não funcionou algo
            # que funcionou — e alguém clicaria de novo.
            saida = "a conexão caiu, como esperado nesta ação"
            ok = True
        else:
            await audit_service.registrar(
                db, usuario=autor.username, action="hosts.comando",
                target=f"{host.name}/{chave}", ip=client_ip(request),
                success=False, detail={"erro": str(exc)[:400]},
            )
            raise HTTPException(status_code=502, detail=str(exc)[:400]) from exc

    if info["derruba"]:
        # Sessão em cache aponta para uma máquina que vai sumir. Sem
        # descartar, a próxima operação esperaria num socket morto.
        await ssh.disconnect(host_id)

    await audit_service.registrar(
        db,
        usuario=autor.username,
        action="hosts.comando",
        target=f"{host.name}/{chave}",
        ip=client_ip(request),
        success=ok,
        detail={"acao": info["rotulo"], "destrutivo": info["destrutivo"]},
    )

    return {
        "chave": chave,
        "rotulo": info["rotulo"],
        "ok": ok,
        # Teto de saída: isto é resposta de botão, não tela de log. O log
        # inteiro tem lugar próprio.
        "saida": saida[:6000],
        "derruba": info["derruba"],
    }
