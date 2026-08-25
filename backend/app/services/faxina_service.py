"""
Faxina — impede o painel de crescer sem fim.

Todo sistema que grava alguma coisa acaba enchendo o disco de quem o
hospeda. Aqui a lista do que cresce e como cada um é contido:

| O que                         | Como cresce            | Contenção         |
|-------------------------------|------------------------|-------------------|
| Gravações do InTerminal       | um .cast por sessão    | retenção em dias  |
| Staging de backup             | órfão de execução que  | remove > 24h      |
|                               | falhou no meio         |                   |
| `audit_logs`                  | uma linha por ação     | retenção em dias  |
| `terminal_sessions`           | uma linha por sessão   | retenção em dias  |
| `backup_runs.log`             | log inteiro por        | esvazia o texto,  |
|                               | execução, KB a MB      | mantém a linha    |
| Log dos containers do painel  | contínuo               | limite no compose |

Roda **uma vez por dia**, no horário configurado, pelo mesmo APScheduler
dos backups. Nada de laço contínuo: uma tarefa por dia custa nada e
resolve tudo.

O artefato de backup NÃO é tocado aqui — ele tem retenção própria, por
destino, aplicada ao fim de cada execução.
"""
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select, update

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.models.audit import AuditLog, TerminalSession
from app.models.backup import BackupRun

log = logging.getLogger("faceops.faxina")

# Órfão de staging: uma execução que falhou no meio deixa o .tar.gz para
# trás. 24h é folga suficiente para o backup mais longo terminar.
STAGING_HORAS = 24


class FaxinaService:
    def __init__(self, config=None) -> None:
        self.config = config
        self.ultima: dict | None = None

    def _cfg(self, chave: str, padrao):
        if self.config is None:
            return padrao
        try:
            return self.config.get(chave)
        except (KeyError, ValueError, TypeError):
            return padrao

    async def executar(self) -> dict:
        """Roda a faxina inteira. Nenhuma etapa derruba as outras."""
        inicio = time.monotonic()
        resultado = {
            "em": datetime.now(timezone.utc).isoformat(),
            "gravacoes_removidas": 0,
            "gravacoes_bytes": 0,
            "staging_removido": 0,
            "staging_bytes": 0,
            "auditoria_removida": 0,
            "sessoes_removidas": 0,
            "logs_esvaziados": 0,
            "erros": [],
        }

        for nome, tarefa in (
            ("gravações", self._gravacoes),
            ("staging", self._staging),
            ("banco", self._banco),
        ):
            try:
                await tarefa(resultado)
            except Exception as exc:
                # Uma etapa que falha não pode impedir as outras: se o
                # disco estiver com problema, ainda queremos limpar o banco.
                resultado["erros"].append(f"{nome}: {type(exc).__name__}: {exc}")
                log.exception("faxina — etapa '%s' falhou", nome)

        resultado["duracao_s"] = round(time.monotonic() - inicio, 1)
        self.ultima = resultado

        log.info(
            "faxina: %d gravacao(oes), %d staging, %d auditoria, %d sessao(oes), "
            "%d log(s) esvaziado(s) em %.1fs",
            resultado["gravacoes_removidas"], resultado["staging_removido"],
            resultado["auditoria_removida"], resultado["sessoes_removidas"],
            resultado["logs_esvaziados"], resultado["duracao_s"],
        )
        return resultado

    # ── Gravações do terminal ──────────────────────────────────────────

    async def _gravacoes(self, r: dict) -> None:
        dias = int(self._cfg("faxina.gravacoes_dias", 90))
        if dias <= 0:
            return

        pasta = Path(settings.TERMINAL_SESSION_DIR)
        if not pasta.exists():
            return

        corte = time.time() - dias * 86400
        for arquivo in pasta.glob("*.cast"):
            try:
                st = arquivo.stat()
                if st.st_mtime < corte:
                    r["gravacoes_bytes"] += st.st_size
                    arquivo.unlink()
                    r["gravacoes_removidas"] += 1
            except OSError:
                continue

    # ── Staging órfão ──────────────────────────────────────────────────

    async def _staging(self, r: dict) -> None:
        pasta = Path(settings.LOCAL_BACKUP_DIR) / "_staging"
        if not pasta.exists():
            return

        corte = time.time() - STAGING_HORAS * 3600
        for arquivo in pasta.iterdir():
            try:
                if not arquivo.is_file():
                    continue
                st = arquivo.stat()
                if st.st_mtime < corte:
                    r["staging_bytes"] += st.st_size
                    arquivo.unlink()
                    r["staging_removido"] += 1
                    log.info("staging orfao removido: %s", arquivo.name)
            except OSError:
                continue

    # ── Banco ──────────────────────────────────────────────────────────

    async def _banco(self, r: dict) -> None:
        dias_audit = int(self._cfg("faxina.auditoria_dias", 365))
        dias_log = int(self._cfg("faxina.log_execucao_dias", 60))

        async with AsyncSessionLocal() as db:
            if dias_audit > 0:
                corte = datetime.now(timezone.utc) - timedelta(days=dias_audit)

                # Registro crítico fica mais tempo: é o que interessa numa
                # investigação, e é uma fração do volume.
                res = await db.execute(
                    delete(AuditLog).where(
                        AuditLog.ts < corte, AuditLog.level != "critical"
                    )
                )
                r["auditoria_removida"] = res.rowcount or 0

                corte_critico = datetime.now(timezone.utc) - timedelta(
                    days=dias_audit * 3
                )
                res = await db.execute(
                    delete(AuditLog).where(AuditLog.ts < corte_critico)
                )
                r["auditoria_removida"] += res.rowcount or 0

                # Sessão de terminal já encerrada, junto com a auditoria
                res = await db.execute(
                    delete(TerminalSession).where(
                        TerminalSession.started_at < corte,
                        TerminalSession.ended_at.isnot(None),
                    )
                )
                r["sessoes_removidas"] = res.rowcount or 0

            if dias_log > 0:
                # O texto do log de execução é o que pesa; a linha em si
                # é pequena e vale manter para o histórico.
                corte = datetime.now(timezone.utc) - timedelta(days=dias_log)
                res = await db.execute(
                    update(BackupRun)
                    .where(
                        BackupRun.started_at < corte,
                        BackupRun.log != "",
                    )
                    .values(log="[log removido pela faxina]")
                )
                r["logs_esvaziados"] = res.rowcount or 0

            await db.commit()

    # ── Prévia, sem alterar nada ───────────────────────────────────────

    async def previa(self) -> dict:
        """O que a faxina removeria agora. Só leitura."""
        dias_grav = int(self._cfg("faxina.gravacoes_dias", 90))
        dias_audit = int(self._cfg("faxina.auditoria_dias", 365))
        dias_log = int(self._cfg("faxina.log_execucao_dias", 60))

        gravacoes, gravacoes_bytes = 0, 0
        pasta = Path(settings.TERMINAL_SESSION_DIR)
        if pasta.exists() and dias_grav > 0:
            corte = time.time() - dias_grav * 86400
            for arquivo in pasta.glob("*.cast"):
                try:
                    st = arquivo.stat()
                    if st.st_mtime < corte:
                        gravacoes += 1
                        gravacoes_bytes += st.st_size
                except OSError:
                    continue

        staging, staging_bytes = 0, 0
        pasta = Path(settings.LOCAL_BACKUP_DIR) / "_staging"
        if pasta.exists():
            corte = time.time() - STAGING_HORAS * 3600
            for arquivo in pasta.iterdir():
                try:
                    if arquivo.is_file() and arquivo.stat().st_mtime < corte:
                        staging += 1
                        staging_bytes += arquivo.stat().st_size
                except OSError:
                    continue

        async with AsyncSessionLocal() as db:
            from sqlalchemy import func

            corte = datetime.now(timezone.utc) - timedelta(days=dias_audit)
            auditoria = (await db.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.ts < corte, AuditLog.level != "critical"
                )
            )).scalar() or 0

            corte_log = datetime.now(timezone.utc) - timedelta(days=dias_log)
            logs = (await db.execute(
                select(func.count(BackupRun.id)).where(
                    BackupRun.started_at < corte_log, BackupRun.log != ""
                )
            )).scalar() or 0

            total_audit = (await db.execute(
                select(func.count(AuditLog.id))
            )).scalar() or 0

        return {
            "gravacoes": gravacoes,
            "gravacoes_bytes": gravacoes_bytes,
            "staging": staging,
            "staging_bytes": staging_bytes,
            "auditoria": auditoria,
            "auditoria_total": total_audit,
            "logs_execucao": logs,
            "retencoes": {
                "gravacoes_dias": dias_grav,
                "auditoria_dias": dias_audit,
                "log_execucao_dias": dias_log,
            },
            "ultima": self.ultima,
        }
