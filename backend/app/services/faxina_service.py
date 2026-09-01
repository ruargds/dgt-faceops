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

# ── Limpeza pontual ────────────────────────────────────────────────────
# A faxina diária é boa para o regime, e ruim para o caso pontual: "o disco
# do painel encheu por causa das gravações de terminal, quero só elas, e só
# as de mais de 180 dias". Mexer na retenção configurada para conseguir
# isso muda o comportamento de TODO dia — e alguém esquece de voltar.
#
# Daí esta lista. Cada categoria age em um lugar só, e nenhuma delas toca em
# artefato de backup, cadastro de servidor, usuário, agendamento ou destino:
# o que sai daqui é histórico e sobra de disco, nunca configuração.
CATEGORIAS_PONTUAIS = {
    "gravacoes": "Gravações do InTerminal (.cast)",
    "staging": "Sobras de staging de backup",
    "auditoria": "Registros de auditoria não críticos",
    "sessoes": "Linhas de sessão de terminal já encerradas",
    "logs_execucao": "Texto do log das execuções de backup",
    "amostras": "Amostras do monitor",
    "licenca": "Histórico de consumo de licença",
}

# Piso de idade da limpeza pontual. Existe porque o estrago de um clique
# errado aqui é irreversível: com sete dias de piso, nenhuma investigação
# em curso, backup em execução ou sessão aberta entra na conta.
DIAS_MINIMO_PONTUAL = 7


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
            "amostras_removidas": 0,
            "incidentes_removidos": 0,
            "padroes_removidos": 0,
            "avisos_removidos": 0,
            "licenca_removidas": 0,
            "erros": [],
        }

        for nome, tarefa in (
            ("gravações", self._gravacoes),
            ("staging", self._staging),
            ("banco", self._banco),
            ("amostras", self._amostras),
            ("incidentes", self._incidentes),
            ("padroes de log", self._padroes_log),
            ("avisos enviados", self._notificacoes),
            ("licenca", self._licenca),
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

    async def _amostras(self, r: dict) -> None:
        """Histórico de monitoramento, com retenção própria."""
        dias = int(self._cfg("monitor.retencao_dias", 30))
        if dias <= 0:
            return
        from app.services.monitor_service import MonitorService

        async with AsyncSessionLocal() as db:
            r["amostras_removidas"] = await MonitorService.limpar(db, dias)
            await db.commit()

    async def _incidentes(self, r: dict) -> None:
        """Histórico de indisponibilidade, com retenção própria (padrão 30 dias)."""
        dias = int(self._cfg("incidentes.retencao_dias", 30))
        if dias <= 0:
            return
        from app.services.incidente_service import IncidenteService

        async with AsyncSessionLocal() as db:
            r["incidentes_removidos"] = await IncidenteService.limpar(db, dias)
            await db.commit()

    async def _notificacoes(self, r: dict) -> None:
        """Log de avisos enviados, com retenção própria (padrão 14 dias)."""
        dias = int(self._cfg("notificacao.retencao_dias", 14))
        if dias <= 0:
            return
        from app.services.notificacao_service import NotificacaoService

        async with AsyncSessionLocal() as db:
            r["avisos_removidos"] = await NotificacaoService.limpar(db, dias)
            await db.commit()

    async def _padroes_log(self, r: dict) -> None:
        """Moldes de log analisados, com retenção própria (padrão 30 dias)."""
        dias = int(self._cfg("analise.retencao_dias", 30))
        if dias <= 0:
            return
        from app.services.log_analise_service import LogAnaliseService

        async with AsyncSessionLocal() as db:
            r["padroes_removidos"] = await LogAnaliseService.limpar(db, dias)
            await db.commit()

    async def _licenca(self, r: dict) -> None:
        """
        Histórico de consumo de licença, com retenção própria.

        Nasceu sem retenção — e "pouco para sempre" continua sendo para
        sempre. Um ano sustenta a projeção de "quando acaba" com folga;
        além disso é linha guardada sem pergunta que a responda.
        """
        dias = int(self._cfg("faxina.licenca_dias", 365))
        if dias <= 0:
            return

        from datetime import datetime, timedelta, timezone

        from app.models.licenca_amostra import LicencaAmostra

        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                delete(LicencaAmostra).where(LicencaAmostra.ts < corte)
            )
            r["licenca_removidas"] = res.rowcount or 0
            await db.commit()

    # ── Limpeza pontual, por categoria e período ───────────────────────

    async def pontual(
        self, categorias: list[str], dias: int, aplicar: bool = False
    ) -> dict:
        """
        Conta (e, se `aplicar`, remove) só o que foi escolhido na tela.

        Duas garantias que fazem esta ação ser aceitável ao lado da faxina
        automática:

        * **Piso de idade.** `dias` nunca cai abaixo de
          `DIAS_MINIMO_PONTUAL`, mesmo que a requisição peça zero.
        * **Nada de configuração sai.** Auditoria crítica, execução de
          backup em andamento, artefato de backup, cadastro, agendamento e
          destino ficam fora — sempre, independente da seleção.

        A retenção configurada não é tocada: a faxina de amanhã continua
        exatamente como estava.
        """
        pedidas = [c for c in categorias if c in CATEGORIAS_PONTUAIS]
        dias = max(int(dias), DIAS_MINIMO_PONTUAL)
        corte_ts = time.time() - dias * 86400
        corte_dt = datetime.now(timezone.utc) - timedelta(days=dias)

        r: dict = {
            "aplicado": bool(aplicar),
            "dias": dias,
            "minimo": DIAS_MINIMO_PONTUAL,
            "categorias": pedidas,
            "gravacoes": 0,
            "gravacoes_bytes": 0,
            "staging": 0,
            "staging_bytes": 0,
            "auditoria": 0,
            "sessoes": 0,
            "logs_execucao": 0,
            "amostras": 0,
            "erros": [],
        }
        if not pedidas:
            return r

        if "gravacoes" in pedidas:
            pasta = Path(settings.TERMINAL_SESSION_DIR)
            if pasta.exists():
                for arquivo in pasta.glob("*.cast"):
                    try:
                        st = arquivo.stat()
                        if st.st_mtime >= corte_ts:
                            continue
                        r["gravacoes"] += 1
                        r["gravacoes_bytes"] += st.st_size
                        if aplicar:
                            arquivo.unlink()
                    except OSError as exc:
                        r["erros"].append(f"gravacao {arquivo.name}: {exc}")

        if "staging" in pedidas:
            pasta = Path(settings.LOCAL_BACKUP_DIR) / "_staging"
            if pasta.exists():
                for arquivo in pasta.iterdir():
                    try:
                        if not arquivo.is_file():
                            continue
                        st = arquivo.stat()
                        if st.st_mtime >= corte_ts:
                            continue
                        r["staging"] += 1
                        r["staging_bytes"] += st.st_size
                        if aplicar:
                            arquivo.unlink()
                    except OSError as exc:
                        r["erros"].append(f"staging {arquivo.name}: {exc}")

        precisa_banco = {"auditoria", "sessoes", "logs_execucao", "amostras"} & set(pedidas)
        if precisa_banco:
            from sqlalchemy import func

            async with AsyncSessionLocal() as db:
                if "auditoria" in pedidas:
                    # Crítico nunca sai por aqui: é justamente o registro
                    # que uma investigação vai procurar.
                    onde = (AuditLog.ts < corte_dt, AuditLog.level != "critical")
                    if aplicar:
                        res = await db.execute(delete(AuditLog).where(*onde))
                        r["auditoria"] = res.rowcount or 0
                    else:
                        r["auditoria"] = (await db.execute(
                            select(func.count(AuditLog.id)).where(*onde)
                        )).scalar() or 0

                if "sessoes" in pedidas:
                    # Só sessão encerrada. Uma sessão aberta ainda tem PTY
                    # vivo do outro lado.
                    onde = (
                        TerminalSession.started_at < corte_dt,
                        TerminalSession.ended_at.isnot(None),
                    )
                    if aplicar:
                        res = await db.execute(delete(TerminalSession).where(*onde))
                        r["sessoes"] = res.rowcount or 0
                    else:
                        r["sessoes"] = (await db.execute(
                            select(func.count(TerminalSession.id)).where(*onde)
                        )).scalar() or 0

                if "logs_execucao" in pedidas:
                    # A LINHA do histórico fica; só o texto do log sai. E
                    # execução em curso está fora: o log dela ainda cresce.
                    onde = (
                        BackupRun.started_at < corte_dt,
                        BackupRun.log != "",
                        BackupRun.status.notin_(("executando", "pendente")),
                    )
                    if aplicar:
                        res = await db.execute(
                            update(BackupRun)
                            .where(*onde)
                            .values(log="[log removido pela limpeza pontual]")
                        )
                        r["logs_execucao"] = res.rowcount or 0
                    else:
                        r["logs_execucao"] = (await db.execute(
                            select(func.count(BackupRun.id)).where(*onde)
                        )).scalar() or 0

                if "amostras" in pedidas:
                    from app.services.monitor_service import MonitorService

                    if aplicar:
                        r["amostras"] = await MonitorService.limpar(db, dias)
                    else:
                        r["amostras"] = await MonitorService.contar_antigas(db, dias)

                if aplicar:
                    await db.commit()

        return r

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
