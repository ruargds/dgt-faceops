"""
Destinos de backup — configurados pela web, não pelo `.env`.

Três tipos cobrem o necessário:

* **local** — disco do painel. Rápido de restaurar, não protege contra
  perda do site.
* **azure** — Azure Blob. Barato no tier Cool e o upload não sai da rede
  do provedor quando o painel está no Azure.
* **rclone** — qualquer backend que o rclone fale: Google Drive, S3, B2,
  OneDrive, SFTP, WebDAV, Dropbox. É o que dá alcance externo sem
  escrever um conector por provedor.

Cada destino é independente: se a nuvem falhar, o local continua válido e
a execução vira sucesso com ressalva, não falha total. Perder o backup
por causa de uma credencial de nuvem vencida seria o pior dos dois mundos.

O upload roda em thread separada (`asyncio.to_thread`): o SDK do Azure e o
rclone são bloqueantes, e travar o event loop por 40 minutos derrubaria o
terminal e as coletas junto.
"""
import asyncio
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import settings
from app.core.vault import decrypt_secret

TIPOS_VALIDOS = ("local", "azure", "rclone")


@dataclass
class ResultadoEnvio:
    tipo: str
    nome: str
    ok: bool
    uri: str = ""
    erro: str = ""
    bytes_enviados: int = 0
    duracao_s: float = 0.0

    def as_dict(self) -> dict:
        return {
            "type": self.tipo,
            "nome": self.nome,
            "status": "ok" if self.ok else "erro",
            "uri": self.uri,
            "error": self.erro[:500],
            "bytes": self.bytes_enviados,
            "duracao_s": round(self.duracao_s, 1),
        }


class DestinoError(Exception):
    pass


class _ConfRclone:
    """
    Materializa o bloco de configuração do rclone num arquivo temporário.

    A configuração guarda token e chave de acesso. Escrever num arquivo de
    modo 0600, usar, e apagar no fim é melhor que deixar um rclone.conf
    permanente no container — que sobreviveria a um `docker cp` distraído.
    """

    def __init__(self, conteudo: str) -> None:
        self.conteudo = conteudo
        self.caminho: str | None = None

    def __enter__(self) -> str:
        fd, caminho = tempfile.mkstemp(prefix="faceops-rclone-", suffix=".conf")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(self.conteudo)
        self.caminho = caminho
        return caminho

    def __exit__(self, *_exc) -> None:
        if self.caminho:
            try:
                os.unlink(self.caminho)
            except OSError:
                pass


class StorageService:
    # ── Envio ──────────────────────────────────────────────────────────

    async def enviar(self, destino, arquivo: Path, host_nome: str) -> ResultadoEnvio:
        """Envia um artefato para um destino configurado."""
        if destino.tipo == "local":
            return await self._local(destino, arquivo, host_nome)
        if destino.tipo == "azure":
            return await self._azure(destino, arquivo, host_nome)
        if destino.tipo == "rclone":
            return await self._rclone(destino, arquivo, host_nome)
        return ResultadoEnvio(
            tipo=destino.tipo, nome=destino.nome, ok=False,
            erro=f"tipo de destino desconhecido: {destino.tipo}",
        )

    async def _local(self, destino, arquivo: Path, host_nome: str) -> ResultadoEnvio:
        inicio = asyncio.get_event_loop().time()
        base = Path(destino.caminho or settings.LOCAL_BACKUP_DIR) / host_nome
        try:
            def _mover() -> int:
                base.mkdir(parents=True, exist_ok=True)
                alvo = base / arquivo.name
                if alvo.exists():
                    alvo.unlink()
                # Move em vez de copiar: se o staging estiver no mesmo
                # volume, copiar 300 GB à toa é tempo e disco.
                shutil.move(str(arquivo), str(alvo))
                return alvo.stat().st_size

            tamanho = await asyncio.to_thread(_mover)
            return ResultadoEnvio(
                tipo="local", nome=destino.nome, ok=True,
                uri=str(base / arquivo.name), bytes_enviados=tamanho,
                duracao_s=asyncio.get_event_loop().time() - inicio,
            )
        except OSError as exc:
            return ResultadoEnvio(tipo="local", nome=destino.nome, ok=False, erro=str(exc))

    async def _azure(self, destino, arquivo: Path, host_nome: str) -> ResultadoEnvio:
        inicio = asyncio.get_event_loop().time()
        conn = decrypt_secret(destino.azure_conn_enc)
        if not conn:
            return ResultadoEnvio(
                tipo="azure", nome=destino.nome, ok=False,
                erro="connection string não cadastrada neste destino",
            )

        blob_nome = f"{host_nome}/{arquivo.name}"

        def _upload() -> tuple[str, int]:
            from azure.storage.blob import BlobServiceClient, StandardBlobTier

            cliente = BlobServiceClient.from_connection_string(conn)
            container = cliente.get_container_client(
                destino.azure_container or "faceops-backups"
            )
            try:
                container.create_container()
            except Exception:
                pass  # já existe — o caminho normal depois da 1ª execução

            blob = container.get_blob_client(blob_nome)
            tamanho = arquivo.stat().st_size
            tier = getattr(StandardBlobTier, (destino.azure_tier or "Cool").capitalize(), None)
            with arquivo.open("rb") as fh:
                blob.upload_blob(
                    fh, overwrite=True, length=tamanho,
                    # O padrão do SDK usa bloco pequeno e engasga em
                    # arquivo de dezenas de GB.
                    max_concurrency=4,
                    standard_blob_tier=tier,
                )
            return blob.url, tamanho

        try:
            url, tamanho = await asyncio.to_thread(_upload)
            return ResultadoEnvio(
                tipo="azure", nome=destino.nome, ok=True, uri=url,
                bytes_enviados=tamanho,
                duracao_s=asyncio.get_event_loop().time() - inicio,
            )
        except Exception as exc:  # o SDK do Azure levanta uma família ampla
            return ResultadoEnvio(
                tipo="azure", nome=destino.nome, ok=False,
                erro=f"{type(exc).__name__}: {exc}",
            )

    async def _rclone(self, destino, arquivo: Path, host_nome: str) -> ResultadoEnvio:
        inicio = asyncio.get_event_loop().time()
        conf = decrypt_secret(destino.rclone_conf_enc)
        if not conf:
            return ResultadoEnvio(
                tipo="rclone", nome=destino.nome, ok=False,
                erro="configuração do rclone não cadastrada neste destino",
            )

        remoto = (
            f"{destino.rclone_remote}:"
            f"{destino.rclone_caminho.strip('/')}/{host_nome}".rstrip("/")
        )
        extras = (destino.rclone_flags or "").split()

        def _copiar() -> str:
            with _ConfRclone(conf) as conf_path:
                proc = subprocess.run(
                    ["rclone", "--config", conf_path, "copy", str(arquivo), remoto,
                     "--transfers", "4", "--stats-one-line", "--log-level", "NOTICE",
                     *extras],
                    capture_output=True, text=True, timeout=8 * 60 * 60,
                )
                if proc.returncode != 0:
                    raise DestinoError((proc.stderr or proc.stdout)[-800:])
            return f"{remoto}/{arquivo.name}"

        try:
            uri = await asyncio.to_thread(_copiar)
            return ResultadoEnvio(
                tipo="rclone", nome=destino.nome, ok=True, uri=uri,
                bytes_enviados=arquivo.stat().st_size,
                duracao_s=asyncio.get_event_loop().time() - inicio,
            )
        except FileNotFoundError:
            return ResultadoEnvio(
                tipo="rclone", nome=destino.nome, ok=False,
                erro="rclone não encontrado na imagem do painel",
            )
        except subprocess.TimeoutExpired:
            return ResultadoEnvio(
                tipo="rclone", nome=destino.nome, ok=False, erro="rclone excedeu 8h"
            )
        except DestinoError as exc:
            return ResultadoEnvio(
                tipo="rclone", nome=destino.nome, ok=False, erro=str(exc)
            )

    # ── Teste de destino ───────────────────────────────────────────────

    async def testar(self, destino) -> dict:
        """
        Confirma que o destino aceita escrita. Grava um arquivo pequeno,
        confere e apaga.

        Testar de verdade vale mais que validar credencial: permissão de
        escrita, container inexistente e cota estourada só aparecem na
        hora de gravar — e aparecer às 3h da manhã, no meio do backup, é
        a pior hora possível.
        """
        marcador = f"faceops-teste-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"

        if destino.tipo == "local":
            def _t() -> dict:
                base = Path(destino.caminho or settings.LOCAL_BACKUP_DIR)
                base.mkdir(parents=True, exist_ok=True)
                alvo = base / f".{marcador}"
                alvo.write_text("faceops", encoding="utf-8")
                alvo.unlink()
                uso = shutil.disk_usage(base)
                return {
                    "ok": True,
                    "detalhe": f"escrita confirmada em {base}",
                    "livre_bytes": uso.free,
                    "total_bytes": uso.total,
                }
            try:
                return await asyncio.to_thread(_t)
            except OSError as exc:
                return {"ok": False, "detalhe": str(exc)}

        if destino.tipo == "azure":
            conn = decrypt_secret(destino.azure_conn_enc)
            if not conn:
                return {"ok": False, "detalhe": "connection string não cadastrada"}

            def _t() -> dict:
                from azure.storage.blob import BlobServiceClient

                cliente = BlobServiceClient.from_connection_string(conn)
                cont = cliente.get_container_client(
                    destino.azure_container or "faceops-backups"
                )
                try:
                    cont.create_container()
                    criado = True
                except Exception:
                    criado = False
                blob = cont.get_blob_client(marcador)
                blob.upload_blob(b"faceops", overwrite=True)
                blob.delete_blob()
                return {
                    "ok": True,
                    "detalhe": (
                        f"escrita confirmada no container "
                        f"'{destino.azure_container}'"
                        + (" (container criado agora)" if criado else "")
                    ),
                }
            try:
                return await asyncio.to_thread(_t)
            except Exception as exc:
                return {"ok": False, "detalhe": f"{type(exc).__name__}: {exc}"}

        if destino.tipo == "rclone":
            conf = decrypt_secret(destino.rclone_conf_enc)
            if not conf:
                return {"ok": False, "detalhe": "configuração do rclone não cadastrada"}

            remoto = f"{destino.rclone_remote}:{destino.rclone_caminho.strip('/')}"

            def _t() -> dict:
                with _ConfRclone(conf) as conf_path:
                    # `about` mostra a cota; nem todo backend suporta,
                    # então a falha aqui não condena o destino.
                    sobre = subprocess.run(
                        ["rclone", "--config", conf_path, "about", f"{destino.rclone_remote}:"],
                        capture_output=True, text=True, timeout=120,
                    )
                    # O que realmente importa: conseguir escrever
                    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tmp:
                        tmp.write("faceops")
                        origem = tmp.name
                    try:
                        env = subprocess.run(
                            ["rclone", "--config", conf_path, "copy", origem, remoto,
                             "--log-level", "ERROR"],
                            capture_output=True, text=True, timeout=300,
                        )
                        if env.returncode != 0:
                            raise DestinoError((env.stderr or env.stdout)[-600:])
                        subprocess.run(
                            ["rclone", "--config", conf_path, "delete",
                             f"{remoto}/{Path(origem).name}"],
                            capture_output=True, text=True, timeout=120,
                        )
                    finally:
                        os.unlink(origem)

                    return {
                        "ok": True,
                        "detalhe": f"escrita confirmada em {remoto}",
                        "sobre": (sobre.stdout or "").strip()[:500],
                    }
            try:
                return await asyncio.to_thread(_t)
            except FileNotFoundError:
                return {"ok": False, "detalhe": "rclone não encontrado na imagem do painel"}
            except (DestinoError, subprocess.TimeoutExpired, OSError) as exc:
                return {"ok": False, "detalhe": str(exc)}

        return {"ok": False, "detalhe": f"tipo desconhecido: {destino.tipo}"}

    # ── Orquestração ───────────────────────────────────────────────────

    async def apagar(self, destino, host_nome: str, arquivo: str) -> dict:
        """
        Apaga um artefato NO destino informado.

        Existe porque apagar pela tela removia só a cópia local: o mesmo
        arquivo seguia no Azure e no rclone, ocupando o lugar mais caro de
        guardar, sem ninguém sabendo. "Apaguei" tem de significar apagado
        em todo lugar onde o painel colocou.

        Nunca levanta: devolve o que aconteceu em cada destino. Falha em um
        não pode impedir a remoção nos outros, e o operador precisa ver
        onde sobrou.
        """
        nome = Path(arquivo).name
        tipo = getattr(destino, "tipo", "?")

        try:
            if tipo == "local":
                base = Path(destino.caminho or settings.LOCAL_BACKUP_DIR) / host_nome
                alvo = base / nome
                if alvo.is_file():
                    await asyncio.to_thread(alvo.unlink)
                    return {"destino": destino.nome, "tipo": tipo, "ok": True}
                return {
                    "destino": destino.nome,
                    "tipo": tipo,
                    "ok": True,
                    "detalhe": "já não estava lá",
                }

            if tipo == "azure":
                def _apagar_azure() -> str:
                    from azure.storage.blob import BlobServiceClient

                    cliente = BlobServiceClient.from_connection_string(
                        decrypt_secret(destino.azure_conn_enc)
                    )
                    cont = cliente.get_container_client(
                        destino.azure_container or "faceops-backups"
                    )
                    blob = cont.get_blob_client(f"{host_nome}/{nome}")
                    blob.delete_blob()
                    return "removido do container"

                detalhe = await asyncio.to_thread(_apagar_azure)
                return {
                    "destino": destino.nome,
                    "tipo": tipo,
                    "ok": True,
                    "detalhe": detalhe,
                }

            if tipo == "rclone":
                remoto = f"{destino.rclone_remote}:{(destino.caminho or '').strip('/')}"
                alvo = f"{remoto}/{host_nome}/{nome}".replace("//", "/").replace(
                    ":/", ":"
                )
                proc = await asyncio.create_subprocess_exec(
                    "rclone",
                    "deletefile",
                    alvo,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, err = await proc.communicate()
                if proc.returncode == 0:
                    return {"destino": destino.nome, "tipo": tipo, "ok": True}
                return {
                    "destino": destino.nome,
                    "tipo": tipo,
                    "ok": False,
                    "erro": (err or b"").decode("utf-8", "replace")[-300:],
                }

        except Exception as exc:
            return {
                "destino": getattr(destino, "nome", "?"),
                "tipo": tipo,
                "ok": False,
                "erro": f"{type(exc).__name__}: {exc}"[:300],
            }

        return {
            "destino": getattr(destino, "nome", "?"),
            "tipo": tipo,
            "ok": False,
            "erro": f"não sei apagar em destino do tipo {tipo}",
        }

    async def distribuir(
        self, arquivo: Path, host_nome: str, destinos: list
    ) -> list[ResultadoEnvio]:
        """
        Envia para todos os destinos pedidos.

        Ordem importa: remoto primeiro, local por último. O envio local
        **move** o arquivo do staging, então precisa ser o último a
        tocá-lo.
        """
        if not destinos:
            return []

        remotos = [d for d in destinos if d.tipo != "local"]
        locais = [d for d in destinos if d.tipo == "local"]

        resultados: list[ResultadoEnvio] = []
        for d in remotos:
            resultados.append(await self.enviar(d, arquivo, host_nome))

        if locais:
            # Só o primeiro local move; um segundo não encontraria o
            # arquivo. Copiar para vários discos locais é caso raro o
            # bastante para não valer a complexidade.
            resultados.append(await self.enviar(locais[0], arquivo, host_nome))
            for d in locais[1:]:
                resultados.append(ResultadoEnvio(
                    tipo="local", nome=d.nome, ok=False,
                    erro="ignorado: já existe outro destino local nesta execução",
                ))
        else:
            # Nenhum destino local: o staging não pode ficar acumulando
            try:
                arquivo.unlink(missing_ok=True)
            except OSError:
                pass

        return resultados

    # ── Retenção ───────────────────────────────────────────────────────

    async def aplicar_retencao(self, destino, host_nome: str, dias: int) -> list[str]:
        """
        Apaga artefatos mais velhos que `dias` num destino local.

        Só age em destino local. Nuvem tem política própria de ciclo de
        vida — apagar de lá por conta própria arriscaria remover o único
        arquivo que sobrou de um incidente.
        """
        if dias <= 0 or destino.tipo != "local":
            return []

        pasta = Path(destino.caminho or settings.LOCAL_BACKUP_DIR) / host_nome
        if not pasta.exists():
            return []

        corte = datetime.now(timezone.utc) - timedelta(days=dias)

        def _limpar() -> list[str]:
            saida: list[str] = []
            for item in pasta.glob("faceops_*.tar.gz"):
                try:
                    mtime = datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc)
                    if mtime < corte:
                        item.unlink()
                        saida.append(item.name)
                except OSError:
                    continue
            return saida

        return await asyncio.to_thread(_limpar)

    # ── Utilidades ─────────────────────────────────────────────────────

    @staticmethod
    def espaco_local(caminho: str | None = None) -> dict:
        alvo = Path(caminho or settings.LOCAL_BACKUP_DIR)
        alvo.mkdir(parents=True, exist_ok=True)
        uso = shutil.disk_usage(alvo)
        return {
            "caminho": str(alvo),
            "total_bytes": uso.total,
            "usado_bytes": uso.used,
            "livre_bytes": uso.free,
            "percentual": round(uso.used / uso.total * 100, 1) if uso.total else 0.0,
        }

    @staticmethod
    def caminho_artefato(host_nome: str, nome_arquivo: str, base: str | None = None) -> Path | None:
        """
        Resolve o caminho de um artefato para download, barrando travessia
        de diretório — o nome vem da URL e não pode virar `../../etc/shadow`.
        """
        if "/" in nome_arquivo or "\\" in nome_arquivo or nome_arquivo.startswith("."):
            return None
        raiz = (Path(base or settings.LOCAL_BACKUP_DIR) / host_nome).resolve()
        alvo = (raiz / nome_arquivo).resolve()
        if not str(alvo).startswith(str(raiz) + os.sep):
            return None
        return alvo if alvo.is_file() else None
