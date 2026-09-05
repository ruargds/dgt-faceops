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
| `backup_runs` (a linha)       | uma por execução       | retenção em dias, |
|                               |                        | só sem artefato   |
| `amostras`                    | uma por host por ciclo | retenção em dias  |
| `incidentes`                  | uma por queda          | retenção em dias  |
| `log_padroes`                 | um molde por erro novo | retenção em dias  |
| `notificacao_envios`          | uma por aviso mandado  | retenção em dias  |
| `licenca_amostras`            | uma por recurso/dia    | retenção em dias  |
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
from app.models.host import Host

log = logging.getLogger("faceops.faxina")

# Órfão de staging: uma execução que falhou no meio deixa o .tar.gz para
# trás. 24h é folga suficiente para o backup mais longo terminar.
STAGING_HORAS = 24

# Os dois formatos de gravação do InTerminal: `.cast` em claro (antes da
# cifragem) e `.cast.enc` (agora, ver `terminal_service.Gravador`). Ficam
# aqui, num lugar só, porque três pontos diferentes varrem esta mesma
# pasta — e um glob esquecido significaria gravação que nunca mais é
# apagada, acumulando no disco da VM do painel em silêncio.
PADROES_GRAVACAO = ("*.cast", "*.cast.enc")


def _gravacoes(pasta):
    """Toda gravação da pasta, nos dois formatos, sem repetir."""
    vistos = set()
    for padrao in PADROES_GRAVACAO:
        for arquivo in pasta.glob(padrao):
            if arquivo in vistos:
                continue
            vistos.add(arquivo)
            yield arquivo

# Teto de linhas de execução avaliadas por passada. A verificação bate no
# disco uma vez por candidato; com teto, um atraso grande é resolvido em
# vários dias em vez de um pico num só.
TETO_EXECUCOES = 500

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
            "gravacoes_desapontadas": 0,
            "staging_removido": 0,
            "staging_bytes": 0,
            "auditoria_removida": 0,
            "sessoes_removidas": 0,
            "logs_esvaziados": 0,
            "amostras_removidas": 0,
            "containers_removidos": 0,
            "discos_removidos": 0,
            "incidentes_removidos": 0,
            "crescimentos_removidos": 0,
            "padroes_removidos": 0,
            "avisos_removidos": 0,
            "licenca_removidas": 0,
            "execucoes_removidas": 0,
            "erros": [],
        }

        for nome, tarefa in (
            ("gravações", self._gravacoes),
            ("staging", self._staging),
            ("banco", self._banco),
            ("amostras", self._amostras),
            ("containers", self._containers),
            ("discos", self._discos),
            ("incidentes", self._incidentes),
            ("crescimentos", self._crescimentos),
            ("padroes de log", self._padroes_log),
            ("avisos enviados", self._notificacoes),
            ("licenca", self._licenca),
            ("execucoes", self._execucoes),
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
        removidos: list[str] = []
        for arquivo in _gravacoes(pasta):
            try:
                st = arquivo.stat()
                if st.st_mtime < corte:
                    r["gravacoes_bytes"] += st.st_size
                    arquivo.unlink()
                    r["gravacoes_removidas"] += 1
                    removidos.append(str(arquivo))
            except OSError:
                continue

        # A linha da sessão vive mais que o arquivo (365 dias contra 90), e
        # continuava apontando para a gravação apagada: a tela oferecia o
        # download e o clique voltava 404 sem dizer por quê. Limpar o
        # caminho é o que faz a linha contar a verdade — a sessão
        # aconteceu, a gravação não existe mais.
        if removidos:
            async with AsyncSessionLocal() as db:
                res = await db.execute(
                    update(TerminalSession)
                    .where(TerminalSession.recording_path.in_(removidos))
                    .values(recording_path="")
                )
                r["gravacoes_desapontadas"] = res.rowcount or 0
                await db.commit()

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

    async def _containers(self, r: dict) -> None:
        """
        Memória por container, com retenção própria (padrão 7 dias).

        Prazo curto e separado do das amostras de propósito: são muitas
        linhas por hora, e a pergunta que elas respondem é do presente
        ("quem está com a memória"), não do histórico de capacidade.
        """
        dias = int(self._cfg("containers.retencao_dias", 7))
        if dias <= 0:
            return
        from app.services.crescimento_service import CrescimentoService

        async with AsyncSessionLocal() as db:
            r["containers_removidos"] = await CrescimentoService.limpar_containers(
                db, dias
            )
            await db.commit()

    async def _discos(self, r: dict) -> None:
        """E/S por dispositivo de disco, com retenção própria (padrão 7 dias)."""
        dias = int(self._cfg("discos.retencao_dias", 7))
        if dias <= 0:
            return
        from app.services.crescimento_service import CrescimentoService

        async with AsyncSessionLocal() as db:
            r["discos_removidos"] = await CrescimentoService.limpar_discos(db, dias)
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

    async def _crescimentos(self, r: dict) -> None:
        """
        Vigilâncias de consumo já encerradas, com retenção própria.

        Prazo maior que o dos incidentes (padrão 90 dias) porque a
        pergunta que elas respondem é de outra escala: "este disco já
        encheu antes, e por causa de quê?" se faz olhando meses, não dias.

        Só as ENCERRADAS, pela mesma razão do incidente aberto: apagar uma
        vigilância em curso faria a tela achar que o problema nunca
        existiu enquanto ele ainda está acontecendo.
        """
        dias = int(self._cfg("crescimento.retencao_dias", 90))
        if dias <= 0:
            return
        from app.services.crescimento_service import CrescimentoService

        async with AsyncSessionLocal() as db:
            r["crescimentos_removidos"] = await CrescimentoService.limpar(db, dias)
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

    async def _execucoes(self, r: dict) -> None:
        """
        A LINHA da execução de backup — não o texto do log, que já era
        esvaziado antes.

        Faltava: o texto saía em 60 dias e a linha ficava para sempre. Uma
        por execução, por host, por dia — alguns milhares por ano. Pouco
        para sempre continua sendo para sempre.

        A regra que faz isto ser seguro: **só sai execução cujo artefato
        já não existe**. Enquanto houver arquivo para restaurar, a linha
        que o descreve fica — apagar a linha e deixar o .tar.gz no disco
        produziria um arquivo que ninguém sabe de onde veio, que é pior do
        que gastar a linha.

        O candidato é buscado com teto: se houver anos de atraso, a faxina
        de amanhã continua o serviço, em vez de varrer a tabela inteira
        num dia.
        """
        dias = int(self._cfg("faxina.execucoes_dias", 730))
        if dias <= 0:
            return

        from app.services.storage_service import StorageService

        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        async with AsyncSessionLocal() as db:
            nomes_res = await db.execute(select(Host.id, Host.name))
            nomes = {i: n for i, n in nomes_res.all()}

            candidatos = await db.execute(
                select(BackupRun)
                .where(
                    BackupRun.started_at < corte,
                    BackupRun.status.notin_(("executando", "pendente")),
                )
                .order_by(BackupRun.started_at)
                .limit(TETO_EXECUCOES)
            )
            apagar = []
            for run in candidatos.scalars().all():
                if not run.artifact_name:
                    # Execução que não gerou artefato (falha, cancelada):
                    # não há arquivo para proteger.
                    apagar.append(run.id)
                    continue
                caminho = StorageService.caminho_artefato(
                    nomes.get(run.host_id, "painel"), run.artifact_name
                )
                if caminho is None:
                    # `caminho_artefato` devolve None quando o arquivo não
                    # existe (ou o nome não passa na cerca) — nos dois
                    # casos não há artefato local a preservar.
                    apagar.append(run.id)

            if apagar:
                res = await db.execute(
                    delete(BackupRun).where(BackupRun.id.in_(apagar))
                )
                r["execucoes_removidas"] = res.rowcount or 0
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
            "licenca": 0,
            "erros": [],
        }
        if not pedidas:
            return r

        if "gravacoes" in pedidas:
            pasta = Path(settings.TERMINAL_SESSION_DIR)
            if pasta.exists():
                for arquivo in _gravacoes(pasta):
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

        precisa_banco = {
            "auditoria", "sessoes", "logs_execucao", "amostras", "licenca",
        } & set(pedidas)
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

                if "licenca" in pedidas:
                    # Estava no catálogo de categorias — logo, aceita pela
                    # rota — e não era tratada aqui: a limpeza respondia
                    # "ok, 0 removidos" e não removia nada. Categoria
                    # oferecida que não age é pior que categoria ausente,
                    # porque quem pediu acredita que foi feito.
                    from app.models.licenca_amostra import LicencaAmostra

                    onde = (LicencaAmostra.ts < corte_dt,)
                    if aplicar:
                        res = await db.execute(
                            delete(LicencaAmostra).where(*onde)
                        )
                        r["licenca"] = res.rowcount or 0
                    else:
                        r["licenca"] = (await db.execute(
                            select(func.count(LicencaAmostra.id)).where(*onde)
                        )).scalar() or 0

                if aplicar:
                    await db.commit()

        return r

    # ── Prévia, sem alterar nada ───────────────────────────────────────

    async def previa(self) -> dict:
        """
        O que a faxina removeria agora. Só leitura.

        Devolve **uma linha por categoria**, e não um punhado de campos
        soltos. O motivo é concreto: esta prévia mostrava quatro
        categorias enquanto a faxina apagava onze — quem olhava e via
        zero concluía que nada seria removido, no mesmo dia em que
        milhares de amostras iam embora. Painel que apresenta um
        subconjunto como se fosse o todo é a mesma falha de "serviço
        travado" e "câmera sem evento", só em outro lugar.

        Com a lista, retenção nova aparece aqui sozinha — e há teste que
        falha se alguém acrescentar um contador ao `executar()` sem
        acrescentar a linha correspondente.
        """
        from sqlalchemy import func

        from app.models.amostra import Amostra
        from app.models.amostra_container import AmostraContainer
        from app.models.amostra_disco import AmostraDisco
        from app.models.crescimento import Crescimento
        from app.models.incidente import Incidente
        from app.models.licenca_amostra import LicencaAmostra
        from app.models.log_padrao import LogPadrao
        from app.models.notificacao import NotificacaoEnvio

        agora = datetime.now(timezone.utc)

        def dias_de(chave, padrao):
            return int(self._cfg(chave, padrao))

        d_grav = dias_de("faxina.gravacoes_dias", 90)
        d_audit = dias_de("faxina.auditoria_dias", 365)
        d_log = dias_de("faxina.log_execucao_dias", 60)
        d_exec = dias_de("faxina.execucoes_dias", 730)
        d_amostra = dias_de("monitor.retencao_dias", 30)
        d_ct = dias_de("containers.retencao_dias", 7)
        d_disco = dias_de("discos.retencao_dias", 7)
        d_inc = dias_de("incidentes.retencao_dias", 30)
        d_cresc = dias_de("crescimento.retencao_dias", 90)
        d_padrao = dias_de("analise.retencao_dias", 30)
        d_aviso = dias_de("notificacao.retencao_dias", 14)
        d_lic = dias_de("faxina.licenca_dias", 365)

        # ── Disco ──────────────────────────────────────────────────────
        def varrer(pasta: Path, padrao: str, segundos: float):
            qtd = tam = 0
            if not pasta.exists() or segundos <= 0:
                return qtd, tam
            corte = time.time() - segundos
            for arquivo in pasta.glob(padrao):
                try:
                    st = arquivo.stat()
                    if arquivo.is_file() and st.st_mtime < corte:
                        qtd += 1
                        tam += st.st_size
                except OSError:
                    continue
            return qtd, tam

        # Os dois formatos: `.cast` de antes da cifragem e `.cast.enc`
        # de agora. Somar os dois é o que impede a prévia de dizer zero
        # enquanto a faxina apaga.
        gravacoes = gravacoes_bytes = 0
        for _padrao in PADROES_GRAVACAO:
            _q, _t = varrer(Path(settings.TERMINAL_SESSION_DIR), _padrao, d_grav * 86400)
            gravacoes += _q
            gravacoes_bytes += _t
        staging, staging_bytes = varrer(
            Path(settings.LOCAL_BACKUP_DIR) / "_staging", "*", STAGING_HORAS * 3600
        )

        # ── Banco ──────────────────────────────────────────────────────
        async with AsyncSessionLocal() as db:

            async def contar(modelo, coluna, dias, *extra):
                if dias <= 0:
                    return 0
                onde = [coluna < agora - timedelta(days=dias), *extra]
                return (await db.execute(
                    select(func.count(modelo.id)).where(*onde)
                )).scalar() or 0

            auditoria = await contar(
                AuditLog, AuditLog.ts, d_audit, AuditLog.level != "critical"
            )
            auditoria_total = (await db.execute(
                select(func.count(AuditLog.id))
            )).scalar() or 0
            sessoes = await contar(
                TerminalSession, TerminalSession.started_at, d_audit,
                TerminalSession.ended_at.isnot(None),
            )
            logs = await contar(
                BackupRun, BackupRun.started_at, d_log, BackupRun.log != ""
            )
            # Candidatas: a decisão final depende de o artefato existir no
            # disco, então aqui é teto, não promessa. Dizer "até N" é o
            # que a prévia sabe de fato.
            execucoes = await contar(
                BackupRun, BackupRun.started_at, d_exec,
                BackupRun.status.notin_(("executando", "pendente")),
            )
            amostras = await contar(Amostra, Amostra.ts, d_amostra)
            containers = await contar(
                AmostraContainer, AmostraContainer.ts, d_ct
            )
            discos = await contar(AmostraDisco, AmostraDisco.ts, d_disco)
            incidentes = await contar(
                Incidente, Incidente.fim, d_inc, Incidente.fim.isnot(None)
            )
            crescimentos = await contar(
                Crescimento, Crescimento.fim, d_cresc, Crescimento.fim.isnot(None)
            )
            padroes = await contar(LogPadrao, LogPadrao.ultima_vez, d_padrao)
            avisos = await contar(NotificacaoEnvio, NotificacaoEnvio.ts, d_aviso)
            licenca = await contar(LicencaAmostra, LicencaAmostra.ts, d_lic)

        linhas = [
            {"chave": "gravacoes", "rotulo": "Gravações do terminal",
             "nota": "arquivos .cast no disco do painel",
             "quantidade": gravacoes, "bytes": gravacoes_bytes,
             "retencao": f"{d_grav} dias"},
            {"chave": "staging", "rotulo": "Staging órfão",
             "nota": "sobra de execução que falhou no meio",
             "quantidade": staging, "bytes": staging_bytes,
             "retencao": "24 horas"},
            {"chave": "auditoria", "rotulo": "Registros de auditoria",
             "nota": "nível crítico fica o triplo do prazo",
             "quantidade": auditoria, "total": auditoria_total,
             "retencao": f"{d_audit} dias"},
            {"chave": "sessoes", "rotulo": "Sessões de terminal encerradas",
             "nota": "a linha do histórico; sessão aberta fica de fora",
             "quantidade": sessoes, "retencao": f"{d_audit} dias"},
            {"chave": "logs_execucao", "rotulo": "Log das execuções de backup",
             "nota": "esvazia o texto, mantém a linha do histórico",
             "quantidade": logs, "retencao": f"{d_log} dias"},
            {"chave": "execucoes", "rotulo": "Linha das execuções de backup",
             "nota": "só a execução cujo artefato já não existe",
             "quantidade": execucoes, "aproximado": True,
             "retencao": f"{d_exec} dias"},
            {"chave": "amostras", "rotulo": "Amostras do monitor",
             "nota": "os pontos dos gráficos da aba Monitor",
             "quantidade": amostras, "retencao": f"{d_amostra} dias"},
            {"chave": "containers", "rotulo": "Memória por container",
             "nota": "a série que desenha o gráfico de quem consome",
             "quantidade": containers, "retencao": f"{d_ct} dias"},
            {"chave": "discos", "rotulo": "E/S por dispositivo de disco",
             "nota": "a série que aponta qual disco está saturado",
             "quantidade": discos, "retencao": f"{d_disco} dias"},
            {"chave": "incidentes", "rotulo": "Incidentes encerrados",
             "nota": "janelas de indisponibilidade já fechadas",
             "quantidade": incidentes, "retencao": f"{d_inc} dias"},
            {"chave": "crescimentos", "rotulo": "Vigilâncias de consumo encerradas",
             "nota": "episódios de consumo crescente que já estabilizaram",
             "quantidade": crescimentos, "retencao": f"{d_cresc} dias"},
            {"chave": "padroes", "rotulo": "Moldes de log analisados",
             "nota": "as impressões digitais dos erros agrupados",
             "quantidade": padroes, "retencao": f"{d_padrao} dias"},
            {"chave": "avisos", "rotulo": "Avisos enviados",
             "nota": "o histórico de envios do Telegram",
             "quantidade": avisos, "retencao": f"{d_aviso} dias"},
            {"chave": "licenca", "rotulo": "Histórico de licença",
             "nota": "uma linha por recurso por dia",
             "quantidade": licenca, "retencao": f"{d_lic} dias"},
        ]

        return {
            "linhas": linhas,
            "total_itens": sum(l["quantidade"] for l in linhas),
            "total_bytes": gravacoes_bytes + staging_bytes,
            "retencoes": {
                "gravacoes_dias": d_grav,
                "auditoria_dias": d_audit,
                "log_execucao_dias": d_log,
                "execucoes_dias": d_exec,
            },
            "ultima": self.ultima,
        }
