"""
Backup do próprio painel.

O painel protege quatro servidores e, até aqui, nada protegia o painel.
Se a máquina que o hospeda morresse, perderia-se:

* o cadastro dos servidores e as **credenciais cifradas** (chave PEM,
  senha de sudo, connection string, configuração do rclone)
* os destinos de backup
* todos os agendamentos
* o histórico de execuções — a prova de que os backups rodaram
* a auditoria inteira
* as visões de log e a configuração

Nada disso é grande: são alguns MB. O custo de salvar é irrisório perto
do custo de recadastrar quatro servidores e perder a trilha de auditoria.

**Sobre a SECRET_KEY:** ela NÃO entra no artefato, de propósito. Ela é o
que decifra as credenciais guardadas; colocá-la junto seria o mesmo que
guardar a chave dentro do cofre trancado. Guarde o `.env` separado, em
outro lugar — e saiba que, para restaurar, você precisa dos dois.
"""
import asyncio
import hashlib
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.backup import BackupRun
from app.models.destino import Destino

log = logging.getLogger("faceops.painel")

PERFIL = "painel"


class PainelBackupError(Exception):
    pass


class PainelBackupService:
    def __init__(self, storage, config=None) -> None:
        self.storage = storage
        self.config = config
        self._lock = asyncio.Lock()

    def ocupado(self) -> bool:
        return self._lock.locked()

    async def executar(
        self, db: AsyncSession, destinos: list[int], *, disparado_por: str
    ) -> BackupRun:
        if self.ocupado():
            raise PainelBackupError(
                "já existe um backup do painel em andamento"
            )

        run = BackupRun(
            host_id=None,
            profile=PERFIL,
            status="executando",
            stage="Iniciando",
            progress=1,
            triggered_by=disparado_por,
            destinations=[],
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)

        async with self._lock:
            try:
                await self._rodar(db, run, destinos)
            except Exception as exc:
                run.status = "falha"
                run.error = f"{type(exc).__name__}: {exc}"[:4000]
                run.stage = "Falhou"
                run.progress = 100
                run.finished_at = datetime.now(timezone.utc)
                await db.commit()
                log.exception("backup do painel falhou")

        await db.refresh(run)
        return run

    async def _rodar(
        self, db: AsyncSession, run: BackupRun, destinos: list[int]
    ) -> None:
        rotulo = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
        nome = f"faceops_painel_{rotulo}.tar.gz"

        staging = Path(settings.LOCAL_BACKUP_DIR) / "_staging"
        staging.mkdir(parents=True, exist_ok=True)
        artefato = staging / nome

        linhas: list[str] = []

        def registrar(texto: str) -> None:
            linhas.append(f"[{datetime.now(timezone.utc):%H:%M:%S}] {texto}")

        # ── Dump do banco ──────────────────────────────────────────────
        run.stage = "Dump do banco do painel"
        run.progress = 20
        await db.commit()

        # `pg_dump` é o caminho preferido — dump nativo, restaurável por
        # objeto. Mas ele pode não existir: a imagem só instala o cliente
        # quando a rede alcança o repositório da PostgreSQL, e num ambiente
        # fechado (o normal aqui) ela não alcança.
        #
        # Sem ele, o painel exporta o próprio banco: uma tabela por arquivo
        # JSON, com o mesmo conteúdo. É menos elegante que um dump binário
        # e infinitamente melhor que não ter backup do painel — que era o
        # que acontecia antes, com FileNotFoundError na cara do operador.
        import shutil as _shutil

        tem_pg_dump = _shutil.which("pg_dump") is not None
        exportacao: dict[str, list[dict]] = {}

        if not tem_pg_dump:
            registrar("pg_dump indisponível — exportando o banco pelo painel")
            from app.db.database import Base

            def _serializavel(valor):
                from datetime import date, datetime as _dt
                from decimal import Decimal

                if isinstance(valor, (_dt, date)):
                    return valor.isoformat()
                if isinstance(valor, Decimal):
                    return float(valor)
                if isinstance(valor, (bytes, bytearray)):
                    import base64

                    return {"__bytes_base64__": base64.b64encode(valor).decode()}
                return valor

            for tabela in Base.metadata.sorted_tables:
                # `registros`, e nao `linhas`: `linhas` e o buffer do LOG
                # desta execucao, e reaproveitar o nome sobrescrevia o log
                # com objetos do banco -- o erro que apareceu em campo,
                # "sequence item 0: expected str instance, RowMapping
                # found", na juncao do log no fim da execucao.
                registros = (await db.execute(tabela.select())).mappings().all()
                exportacao[tabela.name] = [
                    {k: _serializavel(v) for k, v in registro.items()}
                    for registro in registros
                ]
            registrar(
                "exportadas %d tabela(s): %s"
                % (len(exportacao), ", ".join(sorted(exportacao)))
            )

        registrar("iniciando dump do banco do painel")

        def _empacotar() -> tuple[int, str]:
            with tempfile.TemporaryDirectory(prefix="faceops-painel-") as tmp:
                base = Path(tmp)

                # -Fc: comprimido, restaurável por objeto — mesmo formato
                # usado no backup do FindFace, por consistência.
                if tem_pg_dump:
                    dump = base / "faceops.dump"
                    ambiente = {
                        **os.environ,
                        "PGPASSWORD": settings.POSTGRES_PASSWORD,
                    }
                    proc = subprocess.run(
                        [
                            "pg_dump",
                            "-h", settings.POSTGRES_HOST,
                            "-p", str(settings.POSTGRES_PORT),
                            "-U", settings.POSTGRES_USER,
                            "-d", settings.POSTGRES_DB,
                            "-Fc", "--no-password",
                            "-f", str(dump),
                        ],
                        capture_output=True, text=True, timeout=600, env=ambiente,
                    )
                    if proc.returncode != 0:
                        raise PainelBackupError(
                            f"pg_dump falhou: {(proc.stderr or proc.stdout)[-600:]}"
                        )
                else:
                    import json as _json

                    pasta = base / "banco-json"
                    pasta.mkdir(parents=True, exist_ok=True)
                    for nome, registros in exportacao.items():
                        (pasta / f"{nome}.json").write_text(
                            _json.dumps(registros, ensure_ascii=False, indent=1),
                            encoding="utf-8",
                        )

                # Logotipos enviados pela tela
                marca_origem = Path(settings.LOCAL_BACKUP_DIR).parent / "marca"
                if marca_origem.is_dir():
                    shutil.copytree(marca_origem, base / "marca", dirs_exist_ok=True)

                (base / "MANIFESTO.txt").write_text(
                    f"""DGT FaceOps — backup do PAINEL
==============================
Data......: {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC
Banco.....: {settings.POSTGRES_DB}

METODO....: {'pg_dump -Fc' if tem_pg_dump else 'exportacao JSON pelo painel'}

CONTEUDO
  {'faceops.dump   dump binario, restauravel com pg_restore' if tem_pg_dump else 'banco-json/     uma tabela por arquivo JSON'}
                 banco do painel: servidores e credenciais CIFRADAS,
                 destinos, agendamentos, historico, auditoria, usuarios,
                 configuracao e visoes de log
  marca/         logotipos enviados pela tela

NAO CONTEM — e isso e proposital
  A SECRET_KEY. Ela decifra as credenciais guardadas neste dump.
  Coloca-la aqui seria guardar a chave dentro do cofre trancado.

  Guarde o arquivo .env do painel SEPARADO, em outro lugar.
  Para restaurar voce precisa DOS DOIS.

COMO RESTAURAR
  1. Instale o painel na maquina nova (bash instalar.sh)
  2. Pare o backend:  docker compose stop backend
  3. Restaure o banco:
       docker compose exec -T postgres psql -U {settings.POSTGRES_USER} \\
         -d postgres -c "DROP DATABASE IF EXISTS {settings.POSTGRES_DB}"
       docker compose exec -T postgres createdb -U {settings.POSTGRES_USER} \\
         {settings.POSTGRES_DB}
       docker compose exec -T postgres pg_restore -U {settings.POSTGRES_USER} \\
         -d {settings.POSTGRES_DB} --no-owner < faceops.dump
  4. Coloque a SECRET_KEY original no .env
  5. Copie marca/ para o volume de dados
  6. docker compose up -d
  7. Confira em Servidores: "Testar conexao" tem que vir verde. Se der
     "Segredo ilegivel", a SECRET_KEY nao e a mesma.
""",
                    encoding="utf-8",
                )

                with tarfile.open(artefato, "w:gz") as tar:
                    tar.add(base, arcname=f"faceops_painel_{rotulo}")

            tamanho = artefato.stat().st_size
            h = hashlib.sha256()
            with artefato.open("rb") as fh:
                for bloco in iter(lambda: fh.read(4 * 1024 * 1024), b""):
                    h.update(bloco)
            return tamanho, h.hexdigest()

        tamanho, checksum = await asyncio.to_thread(_empacotar)
        registrar(f"artefato montado: {tamanho} bytes")

        run.artifact_name = nome
        run.size_bytes = tamanho
        run.checksum_sha256 = checksum
        run.stage = "Enviando aos destinos"
        run.progress = 80
        run.log = "\n".join(linhas)
        await db.commit()

        # ── Destinos ───────────────────────────────────────────────────
        consulta = select(Destino).where(Destino.enabled.is_(True))
        consulta = (
            consulta.where(Destino.id.in_(destinos)) if destinos
            else consulta.where(Destino.padrao.is_(True))
        )
        objetos = list((await db.execute(consulta)).scalars().all())

        if not objetos:
            raise PainelBackupError(
                "nenhum destino ativo. Cadastre um destino e marque-o como padrão."
            )

        # "painel" como nome de pasta separa do backup dos servidores
        resultados = await self.storage.distribuir(artefato, "_painel", objetos)
        run.destinations = [r.as_dict() for r in resultados]

        if not any(r.ok for r in resultados):
            erros = "; ".join(f"{r.nome}: {r.erro}" for r in resultados)
            raise PainelBackupError(f"nenhum destino aceitou o artefato — {erros}")

        for d in objetos:
            dias = d.retencao_dias or 90
            removidos = await self.storage.aplicar_retencao(d, "_painel", dias)
            if removidos:
                registrar(f"retenção {d.nome}: {len(removidos)} removido(s)")

        falhas = [r for r in resultados if not r.ok]
        run.status = "sucesso"
        run.stage = "Concluído" if not falhas else "Concluído com ressalvas"
        run.progress = 100
        run.finished_at = datetime.now(timezone.utc)
        run.log = "\n".join(linhas)
        if falhas:
            run.error = "destinos com falha: " + "; ".join(
                f"{r.nome}: {r.erro[:200]}" for r in falhas
            )
        await db.commit()
        registrar("concluído")
