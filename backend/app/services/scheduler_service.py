"""
Recorrência programada — o que falta na plataforma web da NtechLab.

Decisão de arquitetura: o APScheduler roda com jobstore **em memória**, e
a tabela `schedules` é a única fonte de verdade. Na subida (e a cada
mudança) o agendador é remontado a partir do banco.

O jobstore persistente do APScheduler serializa a referência da função e
quebra a cada refatoração de módulo — problema clássico e chato de achar.
Remontar da tabela custa milissegundos e nunca diverge.
"""
import logging
from datetime import datetime

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.models.backup import Schedule
from app.models.host import Host

log = logging.getLogger("faceops.scheduler")

DIAS = {
    "0": "dom", "1": "seg", "2": "ter", "3": "qua",
    "4": "qui", "5": "sex", "6": "sáb", "7": "dom",
    "sun": "dom", "mon": "seg", "tue": "ter", "wed": "qua",
    "thu": "qui", "fri": "sex", "sat": "sáb",
}


def validar_cron(expressao: str) -> CronTrigger:
    """
    Valida uma expressão cron de 5 campos e devolve o gatilho.

    Levanta ValueError com mensagem legível — a UI mostra direto ao
    usuário, então "campo de minuto inválido" vale mais que um traceback.
    """
    partes = (expressao or "").split()
    if len(partes) != 5:
        raise ValueError(
            "cron deve ter 5 campos: minuto hora dia mês dia-da-semana. "
            f"Recebido: {len(partes)} campo(s)."
        )
    minuto, hora, dia, mes, dia_semana = partes
    try:
        return CronTrigger(
            minute=minuto,
            hour=hora,
            day=dia,
            month=mes,
            day_of_week=dia_semana,
            timezone=settings.TZ,
        )
    except ValueError as exc:
        raise ValueError(f"expressão cron inválida: {exc}") from exc


def cron_legivel(expressao: str) -> str:
    """Traduz o cron para uma frase — a UI mostra ao lado do campo."""
    partes = (expressao or "").split()
    if len(partes) != 5:
        return expressao
    minuto, hora, dia, mes, dia_semana = partes

    if minuto.startswith("*/"):
        return f"a cada {minuto[2:]} minutos"
    if hora.startswith("*/"):
        return f"a cada {hora[2:]} horas, no minuto {minuto}"

    try:
        horario = f"{int(hora):02d}:{int(minuto):02d}"
    except ValueError:
        return expressao

    if dia_semana != "*":
        nomes = [DIAS.get(d.strip().lower(), d) for d in dia_semana.split(",")]
        return f"toda(o) {', '.join(nomes)} às {horario}"
    if dia != "*":
        return f"todo dia {dia} do mês às {horario}"
    if mes != "*":
        return f"nos meses {mes}, dia {dia}, às {horario}"
    return f"todo dia às {horario}"


class SchedulerService:
    def __init__(self, backup_service) -> None:
        self.backups = backup_service
        self.scheduler = AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            timezone=settings.TZ,
            job_defaults={
                # Se o painel ficou fora do ar na hora marcada, roda ao
                # voltar (até 1h depois). Perder o backup da noite porque
                # a VM reiniciou seria o pior comportamento possível.
                "misfire_grace_time": 3600,
                "coalesce": True,      # 3 execuções perdidas viram 1
                "max_instances": 1,    # nunca dois do mesmo agendamento
            },
        )

    async def start(self) -> None:
        self.scheduler.start()
        await self.sincronizar()
        log.info("agendador iniciado com %d job(s)", len(self.scheduler.get_jobs()))

    async def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    # ── Sincronização com o banco ──────────────────────────────────────

    async def sincronizar(self) -> int:
        """Remonta o agendador a partir da tabela `schedules`."""
        self.scheduler.remove_all_jobs()

        async with AsyncSessionLocal() as db:
            resultado = await db.execute(
                select(Schedule).where(Schedule.enabled.is_(True))
            )
            agendamentos = list(resultado.scalars().all())

            for agendamento in agendamentos:
                try:
                    gatilho = validar_cron(agendamento.cron)
                except ValueError as exc:
                    log.error(
                        "agendamento %s ('%s') tem cron inválido e foi ignorado: %s",
                        agendamento.id, agendamento.name, exc,
                    )
                    agendamento.last_status = f"cron inválido: {exc}"
                    continue

                job = self.scheduler.add_job(
                    self._disparar,
                    trigger=gatilho,
                    args=[agendamento.id],
                    id=f"schedule:{agendamento.id}",
                    name=agendamento.name,
                    replace_existing=True,
                )
                agendamento.next_run_at = job.next_run_time

            await db.commit()

        return len(self.scheduler.get_jobs())

    def proxima_execucao(self, schedule_id: int) -> datetime | None:
        job = self.scheduler.get_job(f"schedule:{schedule_id}")
        return job.next_run_time if job else None

    # ── Disparo ────────────────────────────────────────────────────────

    async def _disparar(self, schedule_id: int) -> None:
        """
        Executa um agendamento. Roda fora de qualquer requisição HTTP, com
        sessão própria de banco.
        """
        async with AsyncSessionLocal() as db:
            agendamento = await db.get(Schedule, schedule_id)
            if agendamento is None or not agendamento.enabled:
                log.info("agendamento %s sumiu ou foi desligado — pulando", schedule_id)
                return

            host = await db.get(Host, agendamento.host_id)
            if host is None or not host.enabled:
                agendamento.last_status = "host indisponível"
                await db.commit()
                log.warning("agendamento %s: host indisponível", schedule_id)
                return

            # Trava de segurança: perfil completo para o stack. Se o
            # agendamento não tem aceite de janela, não roda — melhor
            # falhar visível do que derrubar o reconhecimento às 3h.
            if agendamento.profile == "completo" and not agendamento.allow_downtime:
                agendamento.last_status = "bloqueado: perfil completo sem aceite de janela"
                await db.commit()
                log.warning("agendamento %s bloqueado por falta de aceite", schedule_id)
                return

            log.info(
                "disparando agendamento %s ('%s') — perfil %s em %s",
                schedule_id, agendamento.name, agendamento.profile, host.name,
            )

            try:
                run = await self.backups.executar(
                    db,
                    host,
                    agendamento.profile,
                    list(agendamento.destinations or ["local"]),
                    disparado_por=f"agendamento:{agendamento.name}",
                    schedule_id=agendamento.id,
                    retencao_dias=agendamento.retention_days,
                )
                agendamento.last_status = run.status
            except Exception as exc:
                agendamento.last_status = f"erro: {type(exc).__name__}: {exc}"[:200]
                log.exception("agendamento %s falhou", schedule_id)

            agendamento.last_run_at = datetime.now()
            agendamento.next_run_at = self.proxima_execucao(schedule_id)
            await db.commit()

    async def rodar_agora(self, schedule_id: int) -> None:
        """Dispara um agendamento fora de hora (botão 'Executar agora')."""
        await self._disparar(schedule_id)
