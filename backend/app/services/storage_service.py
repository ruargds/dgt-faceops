"""
Destinos de backup: disco local do painel, Azure Blob e Google Drive.

Cada destino é independente — se o Azure falhar, o local continua válido
e a execução é marcada como sucesso parcial, não como falha total. Perder
o backup por causa de uma credencial de nuvem vencida seria o pior dos
dois mundos.

O upload roda em thread separada (`asyncio.to_thread`): tanto o SDK do
Azure quanto o rclone são bloqueantes, e travar o event loop por 40
minutos derrubaria o terminal e as coletas de métrica junto.
"""
import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import settings

DESTINOS_VALIDOS = ("local", "azure", "gdrive")


@dataclass
class ResultadoEnvio:
    tipo: str
    ok: bool
    uri: str = ""
    erro: str = ""
    bytes_enviados: int = 0
    duracao_s: float = 0.0

    def as_dict(self) -> dict:
        return {
            "type": self.tipo,
            "status": "ok" if self.ok else "erro",
            "uri": self.uri,
            "error": self.erro[:500],
            "bytes": self.bytes_enviados,
            "duracao_s": round(self.duracao_s, 1),
        }


class StorageService:
    # ── Local ──────────────────────────────────────────────────────────

    @staticmethod
    def _pasta_local(host_nome: str) -> Path:
        destino = Path(settings.LOCAL_BACKUP_DIR) / host_nome
        destino.mkdir(parents=True, exist_ok=True)
        return destino

    async def enviar_local(self, arquivo: Path, host_nome: str) -> ResultadoEnvio:
        inicio = asyncio.get_event_loop().time()
        try:
            destino = self._pasta_local(host_nome) / arquivo.name

            # Se o staging já está no mesmo volume do destino final, mover
            # em vez de copiar — copiar 300 GB à toa é tempo e disco.
            def _mover() -> int:
                if destino.exists():
                    destino.unlink()
                shutil.move(str(arquivo), str(destino))
                return destino.stat().st_size

            tamanho = await asyncio.to_thread(_mover)
            return ResultadoEnvio(
                tipo="local",
                ok=True,
                uri=str(destino),
                bytes_enviados=tamanho,
                duracao_s=asyncio.get_event_loop().time() - inicio,
            )
        except OSError as exc:
            return ResultadoEnvio(tipo="local", ok=False, erro=str(exc))

    # ── Azure Blob ─────────────────────────────────────────────────────

    async def enviar_azure(self, arquivo: Path, host_nome: str) -> ResultadoEnvio:
        inicio = asyncio.get_event_loop().time()

        if not settings.AZURE_STORAGE_CONNECTION_STRING:
            return ResultadoEnvio(
                tipo="azure",
                ok=False,
                erro="AZURE_STORAGE_CONNECTION_STRING não configurada no .env",
            )

        blob_nome = f"{host_nome}/{arquivo.name}"

        def _upload() -> tuple[str, int]:
            from azure.storage.blob import BlobServiceClient, StandardBlobTier

            cliente = BlobServiceClient.from_connection_string(
                settings.AZURE_STORAGE_CONNECTION_STRING
            )
            container = cliente.get_container_client(settings.AZURE_BLOB_CONTAINER)
            try:
                container.create_container()
            except Exception:
                # Já existe — o caminho normal depois da primeira execução
                pass

            blob = container.get_blob_client(blob_nome)
            tamanho = arquivo.stat().st_size
            with arquivo.open("rb") as fh:
                blob.upload_blob(
                    fh,
                    overwrite=True,
                    # Blocos de 8 MB com 4 conexões: o padrão do SDK usa
                    # bloco pequeno e engasga em arquivo de dezenas de GB.
                    max_concurrency=4,
                    length=tamanho,
                    standard_blob_tier=StandardBlobTier.COOL,
                )
            return blob.url, tamanho

        try:
            url, tamanho = await asyncio.to_thread(_upload)
            return ResultadoEnvio(
                tipo="azure",
                ok=True,
                uri=url,
                bytes_enviados=tamanho,
                duracao_s=asyncio.get_event_loop().time() - inicio,
            )
        except Exception as exc:  # SDK do Azure levanta uma família ampla
            return ResultadoEnvio(tipo="azure", ok=False, erro=f"{type(exc).__name__}: {exc}")

    # ── Google Drive (rclone) ──────────────────────────────────────────

    async def enviar_gdrive(self, arquivo: Path, host_nome: str) -> ResultadoEnvio:
        inicio = asyncio.get_event_loop().time()
        remoto = f"{settings.RCLONE_REMOTE}:{settings.RCLONE_PATH}/{host_nome}"

        def _copiar() -> str:
            proc = subprocess.run(
                [
                    "rclone", "copy", str(arquivo), remoto,
                    "--transfers", "4",
                    "--drive-chunk-size", "64M",
                    "--stats-one-line",
                    "--log-level", "NOTICE",
                ],
                capture_output=True,
                text=True,
                timeout=6 * 60 * 60,
            )
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or proc.stdout)[-600:])
            return f"{remoto}/{arquivo.name}"

        try:
            uri = await asyncio.to_thread(_copiar)
            return ResultadoEnvio(
                tipo="gdrive",
                ok=True,
                uri=uri,
                bytes_enviados=arquivo.stat().st_size,
                duracao_s=asyncio.get_event_loop().time() - inicio,
            )
        except FileNotFoundError:
            return ResultadoEnvio(
                tipo="gdrive", ok=False, erro="rclone não instalado na imagem"
            )
        except subprocess.TimeoutExpired:
            return ResultadoEnvio(tipo="gdrive", ok=False, erro="rclone excedeu 6h")
        except RuntimeError as exc:
            return ResultadoEnvio(tipo="gdrive", ok=False, erro=str(exc))

    # ── Orquestração ───────────────────────────────────────────────────

    async def distribuir(
        self, arquivo: Path, host_nome: str, destinos: list[str]
    ) -> list[ResultadoEnvio]:
        """
        Envia para todos os destinos pedidos.

        Ordem importa: nuvem primeiro, local por último. `enviar_local`
        MOVE o arquivo do staging, então precisa ser o último a tocá-lo.
        """
        pedidos = [d for d in destinos if d in DESTINOS_VALIDOS]
        if not pedidos:
            pedidos = ["local"]

        resultados: list[ResultadoEnvio] = []

        for destino in ("azure", "gdrive"):
            if destino in pedidos:
                metodo = self.enviar_azure if destino == "azure" else self.enviar_gdrive
                resultados.append(await metodo(arquivo, host_nome))

        if "local" in pedidos:
            resultados.append(await self.enviar_local(arquivo, host_nome))
        else:
            # Nenhum destino local pedido: o staging não pode ficar cheio
            try:
                arquivo.unlink(missing_ok=True)
            except OSError:
                pass

        return resultados

    # ── Retenção ───────────────────────────────────────────────────────

    async def aplicar_retencao(self, host_nome: str, dias: int) -> list[str]:
        """
        Apaga artefatos locais mais velhos que `dias`.

        Só mexe no disco local. Azure e Drive têm política própria de
        ciclo de vida — apagar de lá por conta própria arriscaria remover
        o único arquivo que sobrou de um incidente.
        """
        if dias <= 0:
            return []

        pasta = Path(settings.LOCAL_BACKUP_DIR) / host_nome
        if not pasta.exists():
            return []

        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        removidos: list[str] = []

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

        removidos = await asyncio.to_thread(_limpar)
        return removidos

    @staticmethod
    def espaco_local() -> dict:
        """Espaço no volume de backup do painel — mostrado na UI."""
        caminho = Path(settings.LOCAL_BACKUP_DIR)
        caminho.mkdir(parents=True, exist_ok=True)
        uso = shutil.disk_usage(caminho)
        return {
            "caminho": str(caminho),
            "total_bytes": uso.total,
            "usado_bytes": uso.used,
            "livre_bytes": uso.free,
            "percentual": round(uso.used / uso.total * 100, 1) if uso.total else 0.0,
        }

    @staticmethod
    def caminho_artefato(host_nome: str, nome_arquivo: str) -> Path | None:
        """
        Resolve o caminho de um artefato para download, barrando travessia
        de diretório — o nome vem da URL e não pode virar `../../etc/shadow`.
        """
        if "/" in nome_arquivo or "\\" in nome_arquivo or nome_arquivo.startswith("."):
            return None
        base = (Path(settings.LOCAL_BACKUP_DIR) / host_nome).resolve()
        alvo = (base / nome_arquivo).resolve()
        if not str(alvo).startswith(str(base) + os.sep):
            return None
        return alvo if alvo.is_file() else None
