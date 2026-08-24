"""
Orquestração do backup: dispara no servidor, acompanha, traz o artefato
e distribui para os destinos.

O script (`scripts/ffmulti-backup.sh`) vai pela entrada padrão do bash
remoto, não por arquivo — assim o servidor de produção não acumula script
nosso e uma versão nova do painel já roda a versão nova, sem sincronizar
nada.
"""
import asyncio
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.backup import PROFILES, BackupRun
from app.models.destino import Destino
from app.models.host import Host
from app.services.ssh_service import SSHError, SSHService
from app.services.storage_service import StorageService

SCRIPT_PATH = Path("/opt/faceops-scripts/ffmulti-backup.sh")

# Fallback para rodar fora do container (desenvolvimento na máquina local)
SCRIPT_PATH_DEV = Path(__file__).resolve().parents[3] / "scripts" / "ffmulti-backup.sh"

EMIT = re.compile(r"^FACEOPS:([a-z_]+)=(.*)$")

# Etapas conhecidas -> progresso aproximado, para a barra da UI.
ETAPAS: list[tuple[str, str, int]] = [
    ("Copiando configs", "configs", 10),
    ("Dump do PostgreSQL", "postgres", 30),
    ("Snapshot do Tarantool", "tarantool", 55),
    ("Parando o stack", "stack_stop", 20),
    ("Arquivando data/", "data", 60),
    ("Subindo o stack", "stack_up", 75),
    ("Empacotando artefato", "empacotando", 80),
    ("Calculando checksum", "checksum", 88),
]


class BackupError(Exception):
    pass


def _ler_script() -> str:
    for caminho in (SCRIPT_PATH, SCRIPT_PATH_DEV):
        if caminho.is_file():
            return caminho.read_text(encoding="utf-8")
    raise BackupError(
        f"script de backup nao encontrado em {SCRIPT_PATH} nem {SCRIPT_PATH_DEV}"
    )


def _detectar_etapa(linha: str) -> tuple[str, int] | None:
    baixa = linha.lower()
    for rotulo, chave, pct in ETAPAS:
        if rotulo.lower() in baixa:
            return rotulo, pct
        if chave in baixa and "###" not in linha:
            return rotulo, pct
    return None


class BackupService:
    def __init__(self, ssh: SSHService, storage: StorageService) -> None:
        self.ssh = ssh
        self.storage = storage
        # Uma execução por host de cada vez: dois backups concorrentes no
        # mesmo servidor competem por disco e podem corromper o staging.
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock(self, host_id: int) -> asyncio.Lock:
        if host_id not in self._locks:
            self._locks[host_id] = asyncio.Lock()
        return self._locks[host_id]

    def ocupado(self, host_id: int) -> bool:
        return self._lock(host_id).locked()

    # ── Execução ───────────────────────────────────────────────────────

    async def executar(
        self,
        db: AsyncSession,
        host: Host,
        perfil: str,
        destinos: list[int],
        *,
        disparado_por: str,
        schedule_id: int | None = None,
        retencao_dias: int | None = None,
        run: BackupRun | None = None,
    ) -> BackupRun:
        """
        `run` opcional: a rota HTTP já criou o registro para devolver o id
        à UI na hora. Reaproveitar em vez de criar outro mantém estável o
        id que a tela está acompanhando.
        """
        if perfil not in PROFILES:
            raise BackupError(f"perfil invalido: {perfil}")

        if self.ocupado(host.id):
            raise BackupError(
                f"ja existe um backup em andamento em '{host.name}'. "
                "Aguarde o termino."
            )

        if run is None:
            run = BackupRun(
                host_id=host.id,
                schedule_id=schedule_id,
                profile=perfil,
                triggered_by=disparado_por,
                destinations=[],
                caused_downtime=(perfil == "completo"),
            )
            db.add(run)

        run.status = "executando"
        run.stage = "Iniciando"
        run.progress = 1
        await db.commit()
        await db.refresh(run)

        async with self._lock(host.id):
            try:
                await self._rodar(db, run, host, perfil, destinos, retencao_dias)
            except (SSHError, BackupError) as exc:
                run.status = "falha"
                run.error = str(exc)[:4000]
                run.finished_at = datetime.now(timezone.utc)
                run.progress = 100
                run.stage = "Falhou"
                await db.commit()
            except Exception as exc:  # rede de segurança — nunca deixar "executando" preso
                run.status = "falha"
                run.error = f"erro inesperado: {type(exc).__name__}: {exc}"[:4000]
                run.finished_at = datetime.now(timezone.utc)
                run.progress = 100
                run.stage = "Falhou"
                await db.commit()

        await db.refresh(run)
        return run

    async def _rodar(
        self,
        db: AsyncSession,
        run: BackupRun,
        host: Host,
        perfil: str,
        destinos: list[int],
        retencao_dias: int | None,
    ) -> None:
        ff_dir = host.ffmulti_dir or settings.FFMULTI_DIR
        compose = host.compose_file or f"{ff_dir}/docker-compose.yaml"
        staging = settings.REMOTE_STAGING_DIR
        rotulo = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")

        preambulo = "\n".join([
            f"export FF_DIR={shlex.quote(ff_dir)}",
            f"export COMPOSE_FILE={shlex.quote(compose)}",
            f"export PROFILE={shlex.quote(perfil)}",
            f"export STAGING={shlex.quote(staging)}",
            f"export LABEL={shlex.quote(rotulo)}",
            "",
        ])
        script = preambulo + _ler_script()

        emitidos: dict[str, str] = {}
        linhas_log: list[str] = []
        ultimo_commit = 0.0

        async def on_line(linha: str) -> None:
            nonlocal ultimo_commit
            linhas_log.append(linha)

            m = EMIT.match(linha)
            if m:
                emitidos[m.group(1)] = m.group(2)
                return

            etapa = _detectar_etapa(linha)
            if etapa:
                run.stage, run.progress = etapa
            # Grava o andamento no máximo a cada 2s — commit por linha
            # afogaria o Postgres do painel num backup verboso.
            agora = asyncio.get_event_loop().time()
            if agora - ultimo_commit > 2.0:
                ultimo_commit = agora
                run.log = "\n".join(linhas_log[-1500:])
                await db.commit()

        # Perfil completo pode levar horas; os outros, minutos.
        limite = 8 * 60 * 60 if perfil == "completo" else 2 * 60 * 60

        resultado = await self.ssh.run_script_stream(
            host, script, on_line, sudo=True, timeout=limite
        )

        run.log = "\n".join(linhas_log[-3000:])
        await db.commit()

        if emitidos.get("status") != "sucesso" or resultado.exit_status != 0:
            erro = emitidos.get("erro") or "\n".join(linhas_log[-25:])
            raise BackupError(f"script de backup falhou: {erro}"[:3000])

        artefato_remoto = emitidos.get("artefato", "")
        if not artefato_remoto:
            raise BackupError("script nao informou o caminho do artefato")

        run.artifact_name = Path(artefato_remoto).name
        run.checksum_sha256 = emitidos.get("checksum", "")
        run.size_bytes = int(emitidos.get("tamanho") or 0)
        run.downtime_seconds = int(emitidos.get("downtime") or 0)
        run.caused_downtime = run.downtime_seconds > 0
        run.stage = "Transferindo do servidor"
        run.progress = 90
        await db.commit()

        # ── Traz o artefato ────────────────────────────────────────────
        staging_local = Path(settings.LOCAL_BACKUP_DIR) / "_staging"
        staging_local.mkdir(parents=True, exist_ok=True)
        destino_local = staging_local / run.artifact_name

        try:
            baixado = await self.ssh.download(host, artefato_remoto, str(destino_local))
        except SSHError as exc:
            raise BackupError(f"falha ao trazer o artefato: {exc}") from exc

        if run.size_bytes and baixado and baixado != run.size_bytes:
            raise BackupError(
                f"tamanho divergente: servidor informou {run.size_bytes} bytes, "
                f"chegaram {baixado}. Transferencia incompleta."
            )

        # Confere o checksum — pega corrupção de transferência antes de o
        # arquivo virar "backup bom" no histórico.
        if run.checksum_sha256:
            run.stage = "Conferindo integridade"
            run.progress = 93
            await db.commit()
            local_sum = await self._sha256(destino_local)
            if local_sum != run.checksum_sha256:
                raise BackupError(
                    "checksum nao confere apos a transferencia — artefato corrompido. "
                    f"servidor={run.checksum_sha256[:16]}... painel={local_sum[:16]}..."
                )

        # ── Limpa o servidor ───────────────────────────────────────────
        # Antes de distribuir: o disco do servidor de produção não pode
        # ficar segurando dezenas de GB enquanto o upload roda.
        await self.ssh.run(
            host, f"rm -f {shlex.quote(artefato_remoto)}", sudo=True, timeout=120
        )

        # ── Distribui ──────────────────────────────────────────────────
        run.stage = "Enviando aos destinos"
        run.progress = 95
        await db.commit()

        resultados = await self.storage.distribuir(destino_local, host.name, destinos)
        run.destinations = [r.as_dict() for r in resultados]

        sucessos = [r for r in resultados if r.ok]
        if not sucessos:
            erros = "; ".join(f"{r.tipo}: {r.erro}" for r in resultados)
            raise BackupError(f"nenhum destino aceitou o artefato — {erros}")

        # ── Retenção ───────────────────────────────────────────────────
        dias = retencao_dias if retencao_dias is not None else _retencao_padrao(perfil)
        removidos = await self.storage.aplicar_retencao(host.name, dias)
        if removidos:
            run.log += f"\n[retencao] removidos {len(removidos)} artefatos com mais de {dias} dias"

        falhas = [r for r in resultados if not r.ok]
        run.status = "sucesso"
        run.stage = "Concluido" if not falhas else "Concluido com ressalvas"
        run.progress = 100
        run.finished_at = datetime.now(timezone.utc)
        if falhas:
            run.error = "destinos com falha: " + "; ".join(
                f"{r.tipo}: {r.erro[:200]}" for r in falhas
            )
        await db.commit()

    async def processar_em_segundo_plano(
        self,
        run_id: int,
        host_id: int,
        perfil: str,
        destinos: list[int],
        disparado_por: str,
        retencao_dias: int | None = None,
    ) -> None:
        """
        Continua uma execução já registrada, fora do ciclo da requisição.

        Sessão de banco própria: a da requisição HTTP fecha assim que a
        resposta 202 sai, e um backup completo roda por horas depois disso.
        """
        from app.db.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            run = await db.get(BackupRun, run_id)
            host = await db.get(Host, host_id)
            if run is None or host is None:
                return
            try:
                await self.executar(
                    db,
                    host,
                    perfil,
                    destinos,
                    disparado_por=disparado_por,
                    retencao_dias=retencao_dias,
                    run=run,
                )
            except BackupError as exc:
                run.status = "falha"
                run.error = str(exc)[:4000]
                run.stage = "Falhou"
                run.progress = 100
                run.finished_at = datetime.now(timezone.utc)
                await db.commit()

    @staticmethod
    async def _sha256(caminho: Path) -> str:
        import hashlib

        def _calcular() -> str:
            h = hashlib.sha256()
            with caminho.open("rb") as fh:
                for bloco in iter(lambda: fh.read(4 * 1024 * 1024), b""):
                    h.update(bloco)
            return h.hexdigest()

        return await asyncio.to_thread(_calcular)

    # ── Consultas ──────────────────────────────────────────────────────

    @staticmethod
    async def ultimo_por_host(db: AsyncSession) -> dict[int, BackupRun]:
        """Último backup de cada host — alimenta os cartões da tela inicial."""
        resultado = await db.execute(
            select(BackupRun).order_by(BackupRun.started_at.desc()).limit(400)
        )
        vistos: dict[int, BackupRun] = {}
        for run in resultado.scalars().all():
            if run.host_id not in vistos:
                vistos[run.host_id] = run
        return vistos


def _retencao_padrao(perfil: str) -> int:
    return {
        "config": settings.RETENTION_CONFIG_DAYS,
        "essencial": settings.RETENTION_ESSENCIAL_DAYS,
        "completo": settings.RETENTION_COMPLETO_DAYS,
    }.get(perfil, 30)
