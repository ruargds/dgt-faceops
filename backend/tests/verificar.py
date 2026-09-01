"""
Verificação do monitor: incidentes, limiares e a DDL da subida.

Existe por um motivo concreto: em 01/09/2026 um deploy foi ao ar com dois
índices de mesmo nome no modelo `Incidente`. O código importava, o
frontend compilava, e mesmo assim o painel não subia — o `create_all` da
inicialização falhava, o startup do FastAPI morria junto e o
`atualizar.sh` reverteu sozinho depois de 80 s. Nada disso apareceu em
"compila" ou "importa"; só apareceria rodando.

Roda sem Postgres e sem framework de teste:

    cd backend
    pip install -r requirements-dev.txt      # só aiosqlite
    python tests/verificar.py

Saída: uma linha por cenário e o total. Código de saída != 0 se algo
falhar — serve para chamar do CI ou antes de um deploy.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.database import Base  # noqa: E402
from app.models.amostra import Amostra  # noqa: E402
from app.models.host import Host  # noqa: E402
from app.models.incidente import Incidente  # noqa: E402
from app.models.limiar_override import LimiarOverride  # noqa: E402
from app.services.incidente_service import JANELA_REINICIO_S, IncidenteService  # noqa: E402
from app.services.limiar_service import LimiarService  # noqa: E402

TABELAS = [Host.__table__, Amostra.__table__, Incidente.__table__, LimiarOverride.__table__]

AGORA = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


class ConfigFalsa:
    """Mesma interface do ConfigService, com valores fixos."""

    def __init__(self, valores=None):
        self.valores = valores or {}

    def get(self, chave):
        if chave in self.valores:
            return self.valores[chave]
        raise KeyError(chave)


def host_falso(id_=1, nome="vm-appserver"):
    return Host(id=id_, name=nome, address="10.0.0.1", ssh_user="dgt")


async def nova_sessao():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as con:
        await con.run_sync(lambda c: Base.metadata.create_all(c, tables=TABELAS))
    fabrica = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, fabrica


async def com_host(db, id_=1, nome="vm-appserver"):
    h = host_falso(id_, nome)
    db.add(h)
    await db.flush()
    return h


# ── Cenários ───────────────────────────────────────────────────────────


async def cenario_ddl_sem_indice_duplicado():
    """
    O bug que derrubou o deploy: dois índices com o mesmo nome fazem o
    create_all emitir dois CREATE INDEX iguais, e o segundo estoura.

    A checagem é sobre TODOS os modelos, não só os novos — é barata e
    pega a próxima vez que alguém repetir isso.
    """
    import app.models  # noqa: F401  — registra todo o metadata

    problemas = []
    for tabela in Base.metadata.tables.values():
        nomes = [ix.name for ix in tabela.indexes]
        for nome in set(nomes):
            if nomes.count(nome) > 1:
                problemas.append(f"{tabela.name}.{nome} x{nomes.count(nome)}")
    assert not problemas, f"índices com nome repetido: {problemas}"

    # E a DDL das tabelas novas precisa realmente rodar.
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=TABELAS)


async def cenario_abre_e_fecha_incidente():
    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        serv = IncidenteService()

        # Ciclo 1: serviço parado -> abre.
        await serv.registrar_ciclo(
            db, host, host_ok=True,
            doentes=[{"servico": "findface-video-worker", "estado": "exited"}],
            agora=AGORA,
        )
        await db.flush()
        abertos = await serv.listar_abertos(db)
        assert len(abertos) == 1, abertos
        assert abertos[0]["servico"] == "findface-video-worker"
        assert abertos[0]["aberto"] is True

        # Ciclo 2: continua parado -> NÃO duplica.
        await serv.registrar_ciclo(
            db, host, host_ok=True,
            doentes=[{"servico": "findface-video-worker", "estado": "exited"}],
            agora=AGORA + timedelta(minutes=1),
        )
        await db.flush()
        assert len(await serv.listar_abertos(db)) == 1

        # Ciclo 3: voltou -> fecha, com duração real.
        await serv.registrar_ciclo(
            db, host, host_ok=True, doentes=[], agora=AGORA + timedelta(minutes=6),
        )
        await db.flush()
        assert await serv.listar_abertos(db) == []
        r = await db.execute(sa.select(Incidente))
        inc = r.scalars().one()
        assert inc.fim is not None
        assert inc.duracao_s == 360.0, inc.duracao_s
    await engine.dispose()


async def cenario_host_fora_nao_fecha_servico():
    """
    Se a máquina fica sem contato, não sabemos nada dos serviços dela.
    Fechar o incidente ali registraria uma recuperação que ninguém viu —
    o serviço "voltou" no exato instante em que o host caiu.
    """
    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        serv = IncidenteService()

        await serv.registrar_ciclo(
            db, host, host_ok=True,
            doentes=[{"servico": "pgbouncer", "estado": "exited"}], agora=AGORA,
        )
        await db.flush()

        # Host cai.
        await serv.registrar_ciclo(
            db, host, host_ok=False, doentes=[], agora=AGORA + timedelta(minutes=2),
        )
        await db.flush()
        abertos = await serv.listar_abertos(db)
        tipos = sorted(i["tipo"] for i in abertos)
        assert tipos == ["host", "servico"], tipos

        # Host volta e o serviço voltou junto: agora sim os dois fecham.
        await serv.registrar_ciclo(
            db, host, host_ok=True, doentes=[], agora=AGORA + timedelta(minutes=5),
        )
        await db.flush()
        assert await serv.listar_abertos(db) == []
    await engine.dispose()


async def cenario_reinicio_acumulado_nao_vira_alarme():
    """
    RestartCount do Docker é acumulado desde a criação do container. Um
    worker com 40 reinícios em três meses, estável agora, não pode virar
    incidente — seria alarme falso permanente.
    """
    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        serv = IncidenteService(config=ConfigFalsa({"alerta.servico_reinicios": 5}))

        for i in range(6):
            await serv.registrar_ciclo(
                db, host, host_ok=True, doentes=[],
                reinicios={"findface-video-worker": 40},
                agora=AGORA + timedelta(minutes=i),
            )
        await db.flush()
        assert await serv.listar_abertos(db) == [], "contagem estável virou incidente"
    await engine.dispose()


async def cenario_reinicio_em_laco_abre_e_fecha():
    """Reinício acontecendo AGORA (a contagem sobe dentro da janela) abre."""
    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        serv = IncidenteService(config=ConfigFalsa({"alerta.servico_reinicios": 5}))

        for i in range(6):  # 40 -> 45 em 5 minutos
            await serv.registrar_ciclo(
                db, host, host_ok=True, doentes=[],
                reinicios={"findface-video-worker": 40 + i},
                agora=AGORA + timedelta(minutes=i),
            )
        await db.flush()
        abertos = await serv.listar_abertos(db)
        assert len(abertos) == 1, abertos
        assert "reiniciou" in abertos[0]["causa_provavel"], abertos[0]["causa_provavel"]

        # Parou de reiniciar: a janela desliza e o incidente fecha sozinho.
        depois = AGORA + timedelta(seconds=JANELA_REINICIO_S + 120)
        await serv.registrar_ciclo(
            db, host, host_ok=True, doentes=[],
            reinicios={"findface-video-worker": 45}, agora=depois,
        )
        await db.flush()
        assert await serv.listar_abertos(db) == [], "laço não fechou depois da janela"
    await engine.dispose()


async def cenario_limiar_em_cascata():
    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        outro = await com_host(db, 2, "vm-dbserver")
        serv = LimiarService(config=ConfigFalsa({"alerta.disco_pct": 90}))

        # Sem exceção: vale o padrão global.
        assert await serv.resolver(db, "disco_pct", host.id, padrao=90) == 90

        # Exceção só para este host.
        await serv.salvar(db, "disco_pct", 95, host.id, "", "admin")
        await db.flush()
        assert await serv.resolver(db, "disco_pct", host.id, padrao=90) == 95
        assert await serv.resolver(db, "disco_pct", outro.id, padrao=90) == 90

        # Exceção por serviço, em todos os hosts, e a mais específica ganha.
        await serv.salvar(db, "servico_reinicios", 3, None, "findface-video-worker", "admin")
        await serv.salvar(db, "servico_reinicios", 9, host.id, "findface-video-worker", "admin")
        await db.flush()
        lote = await serv.resolver_lote(db, host.id)
        assert lote["findface-video-worker::servico_reinicios"] == 9, lote
        lote_outro = await serv.resolver_lote(db, outro.id)
        assert lote_outro["findface-video-worker::servico_reinicios"] == 3, lote_outro

        # Restaurar padrão = apagar a exceção.
        overrides = await serv.listar(db)
        alvo = [o for o in overrides if o["host_id"] == host.id and o["chave"] == "disco_pct"][0]
        assert await serv.restaurar(db, alvo["id"]) is True
        await db.flush()
        assert await serv.resolver(db, "disco_pct", host.id, padrao=90) == 90
    await engine.dispose()


async def cenario_limiar_recusa_combinacao_invalida():
    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        serv = LimiarService(config=ConfigFalsa())
        for chave, servico, motivo in [
            ("servico_reinicios", "", "limite de serviço sem serviço"),
            ("disco_pct", "postgresql", "limite de host com serviço"),
        ]:
            try:
                await serv.salvar(db, chave, 5, host.id, servico, "admin")
            except ValueError:
                continue
            raise AssertionError(f"aceitou {motivo}")
    await engine.dispose()


async def cenario_faxina_so_apaga_fechado():
    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        velho_fechado = Incidente(
            host_id=host.id, tipo="servico", servico="a",
            inicio=AGORA - timedelta(days=60), fim=AGORA - timedelta(days=59),
        )
        velho_aberto = Incidente(
            host_id=host.id, tipo="servico", servico="b",
            inicio=AGORA - timedelta(days=60), fim=None,
        )
        recente = Incidente(
            host_id=host.id, tipo="servico", servico="c",
            inicio=datetime.now(timezone.utc) - timedelta(days=1),
            fim=datetime.now(timezone.utc),
        )
        db.add_all([velho_fechado, velho_aberto, recente])
        await db.flush()

        assert await IncidenteService.contar_antigas(db, 30) == 1
        assert await IncidenteService.limpar(db, 30) == 1
        await db.flush()
        r = await db.execute(sa.select(Incidente.servico).order_by(Incidente.servico))
        restantes = sorted(r.scalars().all())
        assert restantes == ["b", "c"], restantes
    await engine.dispose()


async def cenario_resumo_do_painel_degrada_sem_quebrar():
    """
    A faixa do topo do Monitor não pode derrubar a tela inicial.

    Ela é montada a cada 10 s, e é a PRIMEIRA coisa que carrega depois do
    login. Se a consulta de backup falhar (tabela ausente numa instalação
    parcial, banco em manutenção), tem que devolver zero — não 500.
    """
    from app.api.routes.monitor import _resumo_do_painel

    engine, fabrica = await nova_sessao()  # sem a tabela de backups
    async with fabrica() as db:
        r = await _resumo_do_painel(db)
        assert r["backups_com_falha"] == 0, r
        assert "armazenamento" in r, r
    await engine.dispose()


async def cenario_resumo_do_painel_nao_expoe_backup_indevido():
    """
    `/api/monitor/resumo` é liberado por `metrics.view` e devolve também
    backup e disco do painel, que pertencem a `backups.view`.

    Isso só é aceitável porque hoje todo perfil com `metrics.view` tem
    `backups.view`. Este cenário trava essa premissa: no dia em que
    alguém criar um perfil só de métrica, ele falha aqui — e não em
    produção, com dado de backup aparecendo para quem não deveria ver.
    """
    from app.core.permissions import ROLE_PERMISSIONS, permissions_for

    for papel in ROLE_PERMISSIONS:
        podem = permissions_for(papel, False)
        if "metrics.view" in podem:
            assert "backups.view" in podem, (
                f"o perfil '{papel}' vê métrica mas não backup — o resumo do "
                "Monitor precisa passar a filtrar o bloco 'painel' "
                "(ver _resumo_do_painel em app/api/routes/monitor.py)"
            )


CENARIOS = [
    cenario_ddl_sem_indice_duplicado,
    cenario_resumo_do_painel_degrada_sem_quebrar,
    cenario_resumo_do_painel_nao_expoe_backup_indevido,
    cenario_abre_e_fecha_incidente,
    cenario_host_fora_nao_fecha_servico,
    cenario_reinicio_acumulado_nao_vira_alarme,
    cenario_reinicio_em_laco_abre_e_fecha,
    cenario_limiar_em_cascata,
    cenario_limiar_recusa_combinacao_invalida,
    cenario_faxina_so_apaga_fechado,
]


async def principal() -> int:
    falhas = 0
    for cenario in CENARIOS:
        nome = cenario.__name__.replace("cenario_", "").replace("_", " ")
        try:
            await cenario()
            print(f"  ok    {nome}")
        except Exception as exc:
            falhas += 1
            print(f"  FALHA {nome}: {type(exc).__name__}: {exc}")
    print(f"\n{len(CENARIOS) - falhas}/{len(CENARIOS)} cenários passaram")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(principal()))
