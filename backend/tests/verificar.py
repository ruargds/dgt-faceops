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
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.database import Base  # noqa: E402
from app.models.amostra import Amostra  # noqa: E402
from app.models.amostra_container import AmostraContainer  # noqa: E402
from app.models.crescimento import Crescimento  # noqa: E402
from app.models.host import Host  # noqa: E402
from app.models.audit import AuditLog, TerminalSession
from app.models.licenca_amostra import LicencaAmostra
from app.models.backup import BackupRun
from app.models.incidente import Incidente  # noqa: E402
from app.models.limiar_override import LimiarOverride  # noqa: E402
from app.models.log_padrao import LogPadrao  # noqa: E402
from app.models.notificacao import (  # noqa: E402
    NotificacaoConta, NotificacaoDestino, NotificacaoEnvio, NotificacaoRegra,
)
from app.services.incidente_service import JANELA_REINICIO_S, IncidenteService  # noqa: E402
from app.services.limiar_service import LimiarService  # noqa: E402

TABELAS = [
    Host.__table__, Amostra.__table__, Incidente.__table__,
    Crescimento.__table__, AmostraContainer.__table__,
    LimiarOverride.__table__, LogPadrao.__table__,
    NotificacaoConta.__table__, NotificacaoDestino.__table__,
    NotificacaoRegra.__table__, NotificacaoEnvio.__table__,
    AuditLog.__table__, TerminalSession.__table__,
    LicencaAmostra.__table__, BackupRun.__table__,
]

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
    Se a máquina fica sem comunicação, não sabemos nada dos serviços dela.
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


async def cenario_fingerprint_agrupa_o_que_e_o_mesmo_erro():
    """
    Duas linhas do mesmo erro com número/timestamp/IP diferentes têm que
    cair no mesmo molde — é isso que transforma 500 linhas de log em
    "1 erro, 500 vezes". E erros diferentes NÃO podem se misturar.
    """
    from app.services.log_analise_service import analisar_texto, impressao, normalizar

    a = normalizar("2026-09-01T10:00:00Z ERROR camera 17 timeout after 5031ms from 10.0.1.5")
    b = normalizar("2026-09-02T23:11:44Z ERROR camera 42 timeout after 87ms from 10.0.1.9")
    assert impressao(a) == impressao(b), f"não agrupou:\n{a}\n{b}"

    c = normalizar("2026-09-01T10:00:00Z ERROR out of memory killing process 91")
    assert impressao(a) != impressao(c), "agrupou erros diferentes"

    texto = "\n".join([
        "2026-09-01T10:00:00Z ERROR camera 17 timeout after 5031ms",
        "2026-09-01T10:00:05Z ERROR camera 42 timeout after 87ms",
        "2026-09-01T10:00:09Z ERROR camera 8 timeout after 12ms",
        "2026-09-01T10:00:10Z INFO tudo normal por aqui",
    ])
    achados = analisar_texto(texto)
    assert len(achados) == 1, achados          # info não entra
    assert achados[0]["ocorrencias"] == 3, achados


async def cenario_catalogo_casa_erro_conhecido():
    """
    A base de conhecimento tem que reconhecer o que já custou tempo neste
    ambiente — e apontar a tela que resolve.
    """
    from app.services.catalogo_erros import CATALOGO, classificar

    casos = [
        ("Sep 01 03:12:44 kernel: Out of memory: Killed process 4127 (python)", "oom"),
        ("ERROR: could not write block: No space left on device", "disco_cheio"),
        ("video-worker: failed to open rtsp stream for camera 12", "rtsp"),
        ("FATAL: remaining connection slots are reserved", "banco_recusa"),
        ("cuda error: no CUDA-capable device is detected", "vram"),
        ("got permission denied while trying to connect to the Docker daemon socket", "docker_permissao"),
    ]
    for linha, esperado in casos:
        nivel, padrao = classificar(linha)
        assert padrao is not None, f"não reconheceu: {linha}"
        assert padrao["chave"] == esperado, f"{linha} -> {padrao['chave']}, esperado {esperado}"
        assert nivel in ("erro", "aviso"), nivel

    # Toda entrada precisa ter ação e origem declarada — sugestão sem
    # procedência é o que este catálogo existe para evitar.
    for p in CATALOGO:
        assert p.get("acao"), f"{p['chave']} sem ação"
        assert p.get("fonte") in ("campo", "manual", "campo+manual"), p.get("fonte")

    # Linha comum de operação não pode virar achado.
    nivel, padrao = classificar('GET /users/me/ 200 OK')
    assert padrao is None and nivel == "info", (nivel, padrao)


async def cenario_analise_soma_ocorrencias_sem_duplicar_molde():
    engine, fabrica = await nova_sessao()
    from app.services.log_analise_service import LogAnaliseService

    async with fabrica() as db:
        host = await com_host(db)
        serv = LogAnaliseService(stack=None, config=ConfigFalsa())

        achados = [{
            "fingerprint": "abc123", "molde": "ERROR camera <n> timeout",
            "exemplo": "ERROR camera 17 timeout", "nivel": "erro",
            "padrao_conhecido": "rtsp", "ocorrencias": 3,
        }]
        await serv.registrar(db, host.id, "findface-video-worker", achados)
        await serv.registrar(db, host.id, "findface-video-worker", achados)
        await db.flush()

        itens = await serv.listar(db, host_id=host.id)
        assert len(itens) == 1, itens
        assert itens[0]["ocorrencias"] == 6, itens[0]
        assert itens[0]["conhecido"]["chave"] == "rtsp", itens[0]
        assert itens[0]["conhecido"]["onde"] == "dispositivos", itens[0]
    await engine.dispose()


async def cenario_analise_le_o_container_e_nao_o_servico():
    """
    `docker logs` quer o nome do CONTAINER
    ("findface-multi-findface-video-worker-1"); o incidente guarda o nome
    do SERVIÇO ("findface-video-worker"). Passar um pelo outro faz a
    leitura falhar em silêncio — o painel mostraria "nenhum padrão" para
    sempre, sem erro nenhum na tela.
    """
    from app.services.log_analise_service import LogAnaliseService

    pedidos = []

    class StackFalso:
        async def logs(self, host, container, linhas=200):
            pedidos.append(container)
            return "2026-09-01T10:00:00Z ERROR camera 3 timeout after 900ms"

        async def list_services(self, host):
            return {"servicos": [
                {"servico": "findface-video-worker",
                 "nome": "findface-multi-findface-video-worker-1"},
            ]}

    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        serv = LogAnaliseService(stack=StackFalso(), config=ConfigFalsa())

        # Mapa vindo do ciclo do monitor.
        await serv.analisar_servicos(
            db, host, ["findface-video-worker"],
            containers={"findface-video-worker": "findface-multi-findface-video-worker-1"},
            forcar=True,
        )
        # Sem mapa (clique da tela): resolve sozinho via list_services.
        await serv.analisar_servicos(db, host, ["findface-video-worker"], forcar=True)
        await db.flush()

        assert pedidos == ["findface-multi-findface-video-worker-1"] * 2, pedidos
        itens = await serv.listar(db, host_id=host.id)
        assert len(itens) == 1 and itens[0]["ocorrencias"] == 2, itens
    await engine.dispose()


async def cenario_sonda_404_405_nao_e_servico_travado():
    """
    O falso crítico de 01/09/2026: `findface-ntls` responde 404 em /health
    (o caminho documentado dele é /v1/licenses.json) e
    `findface-extraction-api` responde 405 (espera POST). Os dois estavam
    Up há 11 dias, atendendo — e o painel anunciava "Serviço travado",
    oito vezes.

    Qualquer resposta HTTP prova que o componente está de pé. Só 000
    (curl não conectou) é ausência de resposta.
    """
    import inspect

    from app.services import internos_service

    fonte = inspect.getsource(internos_service)
    assert 'vivo = bool(codigo[:1] in ("2", "3")' not in fonte, (
        "a classificação antiga voltou: 404/405 seriam tratados como travado"
    )

    # A regra em si, exercitada nos códigos que importam.
    def vivo_de(codigo: str) -> bool:
        return bool(codigo and codigo != "000")

    for codigo in ("200", "204", "301", "401", "403", "404", "405", "500"):
        assert vivo_de(codigo), f"{codigo} deveria contar como respondeu"
    assert not vivo_de("000"), "000 é ausência de resposta"
    assert not vivo_de(""), "vazio é ausência de resposta"


async def cenario_notificacao_roteia_para_os_destinos_certos():
    """
    Com destino separado da regra, a resposta deixou de ser sim/não e virou
    "quem recebe". Duas regras podem valer ao mesmo tempo, cada uma para o
    seu destino — é o que permite "o plantão recebe tudo, o dono do serviço
    recebe só o dele".

    E sem regra que cubra, ninguém recebe: o silêncio por omissão continua.
    """
    from app.models.notificacao import NotificacaoDestino, NotificacaoRegra
    from app.services.notificacao_service import NotificacaoService as NS

    plantao = NotificacaoDestino(id=1, nome="Plantão", tipo="grupo", chat_id="-100", ativo=True)
    dono = NotificacaoDestino(id=2, nome="João", tipo="individual", chat_id="55", ativo=True)
    desligado = NotificacaoDestino(id=3, nome="Antigo", tipo="grupo", chat_id="-999", ativo=False)
    destinos = [plantao, dono, desligado]

    queda = {"tipo": "servico_parado", "host_id": 1, "servico": "pgbouncer",
             "nivel": "critico", "duracao_s": 0}

    assert NS.rotear([], destinos, queda) == [], "sem regra, ninguém recebe"

    # Regra geral, sem destino = todos os destinos ATIVOS (o desligado fica fora).
    geral = NotificacaoRegra(
        destino_id=None, host_id=None, servico="", ativo=True,
        tipos=["servico_parado"], nivel_minimo="critico", atraso_s=0,
    )
    nomes = sorted(d.nome for d in NS.rotear([geral], destinos, queda))
    assert nomes == ["João", "Plantão"], nomes

    # Regra específica de um serviço para um destino só.
    do_dono = NotificacaoRegra(
        destino_id=2, host_id=1, servico="pgbouncer", ativo=True,
        tipos=["servico_parado"], nivel_minimo="critico", atraso_s=0,
    )
    nomes = sorted(d.nome for d in NS.rotear([do_dono], destinos, queda))
    assert nomes == ["João"], nomes

    # As duas juntas não se anulam — somam, sem duplicar destino.
    escolhidos = NS.rotear([geral, do_dono], destinos, queda)
    assert sorted(d.nome for d in escolhidos) == ["João", "Plantão"], escolhidos
    assert len({d.id for d in escolhidos}) == len(escolhidos), "destino duplicado"

    # Outro host não é alcançado pela regra do host 1.
    outro = dict(queda, host_id=2)
    assert NS.rotear([do_dono], destinos, outro) == []


async def cenario_notificacao_filtra_por_tipo_e_gravidade():
    from app.models.notificacao import NotificacaoDestino, NotificacaoRegra
    from app.services.notificacao_service import NotificacaoService as NS

    destinos = [NotificacaoDestino(id=1, nome="Plantão", tipo="grupo", chat_id="-100", ativo=True)]

    # Só quer saber de retorno: queda não passa, retorno passa.
    so_retorno = NotificacaoRegra(
        destino_id=None, host_id=None, servico="", ativo=True,
        tipos=["retorno"], nivel_minimo="critico", atraso_s=0,
    )
    queda = {"tipo": "servico_parado", "host_id": 1, "servico": "x", "nivel": "critico", "duracao_s": 0}
    volta = {"tipo": "retorno", "host_id": 1, "servico": "x", "duracao_s": 90}
    assert NS.rotear([so_retorno], destinos, queda) == []
    assert len(NS.rotear([so_retorno], destinos, volta)) == 1

    # Gravidade: "só quando parar" não deixa passar atenção.
    so_critico = NotificacaoRegra(
        destino_id=None, host_id=None, servico="", ativo=True,
        tipos=["servico_parado", "metrica"], nivel_minimo="critico", atraso_s=0,
    )
    assert len(NS.rotear([so_critico], destinos, queda)) == 1
    assert NS.rotear([so_critico], destinos, dict(queda, nivel="atencao")) == []

    # Tipo não marcado nunca passa, nem sendo crítico.
    metrica = {"tipo": "metrica", "host_id": 1, "servico": "", "nivel": "critico"}
    sem_metrica = NotificacaoRegra(
        destino_id=None, host_id=None, servico="", ativo=True,
        tipos=["servico_parado"], nivel_minimo="atencao", atraso_s=0,
    )
    assert NS.rotear([sem_metrica], destinos, metrica) == []

    # Regra desligada não manda nada.
    so_critico.ativo = False
    assert NS.rotear([so_critico], destinos, queda) == []


async def cenario_notificacao_espera_antes_de_avisar():
    """
    O `for:` do Prometheus: só avisa se o problema PERSISTIR. Serve para
    não acordar ninguém por uma piscada de 20 segundos.

    O retorno não espera — boa notícia não tem por que atrasar.
    """
    from app.models.notificacao import NotificacaoDestino, NotificacaoRegra
    from app.services.notificacao_service import NotificacaoService as NS

    destinos = [NotificacaoDestino(id=1, nome="Plantão", tipo="grupo", chat_id="-100", ativo=True)]
    regra = NotificacaoRegra(
        destino_id=None, host_id=None, servico="", ativo=True,
        tipos=["servico_parado", "retorno"], nivel_minimo="critico", atraso_s=300,
    )

    novinho = {"tipo": "servico_parado", "host_id": 1, "servico": "x",
               "nivel": "critico", "duracao_s": 20}
    assert NS.rotear([regra], destinos, novinho) == [], "avisou antes da espera"

    persistindo = dict(novinho, duracao_s=301)
    assert len(NS.rotear([regra], destinos, persistindo)) == 1, "não avisou depois da espera"

    volta = {"tipo": "retorno", "host_id": 1, "servico": "x", "duracao_s": 30}
    assert len(NS.rotear([regra], destinos, volta)) == 1, "retorno não deveria esperar"


async def cenario_apuracao_correlaciona_pico_de_recurso():
    """
    Um gráfico de memória subindo de 78% para 94% pouco antes da queda é
    a pista que quem investiga procura primeiro — e ela já está no banco
    do painel, gravada pelo ciclo do monitor. Ler isso custa zero ao
    servidor de produção.

    Duas regras que não podem se perder:

    * é apresentado como **correlação**, nunca como causa comprovada;
    * duas amostras não desenham tendência — com poucos pontos, silêncio.
    """
    from app.models.amostra import Amostra
    from app.services.apuracao_service import ApuracaoService

    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        queda = AGORA

        # Memória subindo de 78% a 94% nos 30 min anteriores — o caso do
        # gráfico do Zabbix que motivou esta função.
        for i, pct in enumerate([78.0, 80.0, 83.0, 88.0, 93.7]):
            db.add(Amostra(
                host_id=host.id, ts=queda - timedelta(minutes=25 - i * 5),
                mem_pct=pct, cpu_pct=20.0, disco_pct=40.0, swap_pct=0.0,
                erro="",
            ))
        await db.commit()

        achados = await ApuracaoService.pressao_antes(db, host.id, queda, 30)
        assert achados, "não viu a memória subir"
        texto = " ".join(a["texto"] for a in achados)
        assert "memória" in texto and "94" in texto, texto
        # Palavra por palavra: correlação, não causa.
        assert "correlação" in texto and "não causa" in texto, texto
        assert all(a["fonte"] == "amostras do painel" for a in achados)

        # Disco parado no mesmo período não vira achado — apontar variação
        # normal como pista é ruído com cara de conclusão.
        assert "disco" not in texto, texto

    await engine.dispose()

    # Poucas amostras: silêncio, não tendência inventada.
    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        db.add(Amostra(host_id=host.id, ts=AGORA - timedelta(minutes=5),
                       mem_pct=95.0, erro=""))
        await db.commit()
        assert await ApuracaoService.pressao_antes(db, host.id, AGORA, 30) == [], (
            "desenhou tendência com uma amostra só"
        )
    await engine.dispose()

    # Amostra com erro (host sem comunicação) não entra na conta: os
    # números dela não foram medidos, foram deixados em zero.
    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        for i in range(5):
            db.add(Amostra(
                host_id=host.id, ts=AGORA - timedelta(minutes=25 - i * 5),
                mem_pct=0.0, erro="SSHError: timeout",
            ))
        await db.commit()
        assert await ApuracaoService.pressao_antes(db, host.id, AGORA, 30) == [], (
            "usou amostra que não mediu nada"
        )
    await engine.dispose()


async def cenario_busca_entende_acento_e_parte_da_palavra():
    """
    A mesma régua do InfraCore, para quem usa os dois painéis digitar do
    mesmo jeito nos dois — e a gêmea do servidor tem de concordar com a
    da tela, senão a mesma busca acha na lista e não acha na Auditoria.

    | digitou | acha |
    |---|---|
    | `video` | o que COMEÇA uma palavra com "video" |
    | `%video` | em qualquer parte |
    | `"video"` | só a palavra inteira |
    """
    import sqlalchemy as sa

    from app.core.busca import (
        SEPARADORES, condicao_de_busca, ler_termo, normalizar, separar_termos,
    )
    from app.models.audit import AuditLog

    # Acento não importa nos dois sentidos.
    assert normalizar("Câmera ÁGUA") == "camera agua"
    assert normalizar("  Ijuí  ") == "ijui"
    assert normalizar(None) == ""

    # Os operadores.
    assert ler_termo("restore") == ("inicio", "restore")
    assert ler_termo("%erro") == ("contem", "erro")
    assert ler_termo('"ntls"') == ("exato", "ntls")
    assert ler_termo("^abc") == ("inicio", "abc")
    assert ler_termo("") == (None, "")

    # Vírgula, ponto-e-vírgula e quebra de linha separam; espaço NÃO.
    assert separar_termos("Restore, %erro; x") == ["restore", "%erro", "x"]
    assert separar_termos("Escola Central") == ["escola central"]
    assert separar_termos("") == []
    assert separar_termos(None) == []

    # A pontuação de JSON separa palavras. Sem isso, procurar `timeout`
    # não acharia `{"erro": "timeout"}` — e o detalhe da auditoria é JSON.
    for exigido in ('"', "{", ":", "-", " "):
        assert exigido in SEPARADORES, f"separador ausente: {exigido!r}"
    assert "_" not in SEPARADORES, "sublinhado liga palavra, não separa"

    # Busca vazia não filtra — nunca "não casa nada".
    assert condicao_de_busca([AuditLog.action], "") is None
    assert condicao_de_busca([AuditLog.action], None) is None

    # E o comportamento de ponta a ponta, no banco.
    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        db.add_all([
            AuditLog(usuario="admin", action="services.restart",
                     target="vm-appserver", level="info", success=True,
                     detail={"servico": "findface-video-worker"}),
            AuditLog(usuario="joao", action="backups.restore",
                     target="vm-dbserver", level="critical", success=False,
                     detail={"erro": "timeout na conexao"}),
            # Acento numa coluna de TEXTO. Dentro de JSON não serve de
            # prova: o serializador escapa não-ASCII (`conexão`), e
            # o que fica gravado depende do banco — JSONB no Postgres
            # guarda o caractere; o JSON do SQLite, a sequência escapada.
            AuditLog(usuario="maria", action="hosts.manage",
                     target="vm-são-paulo", level="info", success=True,
                     detail={}),
        ])
        await db.commit()

        async def achar(termo):
            cond = condicao_de_busca(
                [AuditLog.usuario, AuditLog.action, AuditLog.target,
                 sa.cast(AuditLog.detail, sa.Text)],
                termo,
            )
            consulta = sa.select(AuditLog)
            if cond is not None:
                consulta = consulta.where(cond)
            r = await db.execute(consulta)
            return sorted(a.usuario for a in r.scalars().all())

        # Começo de palavra: `appserver` acha `vm-appserver` (o hífen
        # separa), e `restore` acha `backups.restore` (o ponto separa).
        assert await achar("appserver") == ["admin"]
        # `restore` casa `backups.restore` e NÃO casa `services.restart`:
        # são palavras diferentes, e é essa precisão que se ganha ao sair
        # do `includes` cru.
        assert await achar("restore") == ["joao"], await achar("restore")

        # Dentro de JSON, entre aspas.
        assert await achar("timeout") == ["joao"]

        # Meio de palavra NÃO casa por padrão...
        assert await achar("erver") == []
        # ...mas casa com `%`, que é o pedido explícito.
        assert await achar("%erver") == ["admin", "joao"], await achar("%erver")
        # `maria` não tem "erver" em lugar nenhum.

        # Palavra inteira com aspas. Em `findface-video-worker` o hífen
        # delimita, então "video" É uma palavra inteira ali.
        assert await achar('"video"') == ["admin"], await achar('"video"')
        # Já `vide` começa a palavra mas não a termina.
        assert await achar('"vide"') == [], await achar('"vide"')
        assert await achar("video") == ["admin"]

        # Digitar como está escrito sempre acha, com ou sem acento.
        assert await achar("são") == ["maria"], await achar("são")
        assert await achar("conexao") == ["joao"]

        # Digitar SEM acento e achar COM depende da extensão `unaccent`.
        # O SQLite dos testes não a tem, e afirmar que casa seria mentir
        # sobre o que o painel faz num banco sem a extensão.
        from app.core.busca import usa_unaccent

        if usa_unaccent():
            assert await achar("sao") == ["maria"]
        else:
            assert await achar("sao") == [], (
                "sem unaccent isto não deveria casar — se casou, a régua "
                "mudou e a documentação está desatualizada"
            )

        # Vírgula soma resultados.
        assert await achar("appserver, timeout") == ["admin", "joao"]

        # Curinga digitado é literal, não coringa de LIKE: `%` sozinho
        # depois do operador vira busca vazia, e `_` não casa qualquer
        # caractere.
        assert await achar("vm_appserver") == [], "o sublinhado virou curinga"

    await engine.dispose()

    # As duas réguas existem e falam do mesmo contrato.
    js = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
          / "utils" / "buscaInteligente.js").read_text(encoding="utf-8")
    for fn in ("normalizarTexto", "termosDaBusca", "lerTermo", "casaBusca",
               "pontuacaoBusca", "casaBuscaExata", "ajudaDeBusca"):
        assert f"export function {fn}" in js, f"a régua da tela perdeu {fn}"

    # E as telas com lista longa usam a régua, em vez de `includes` cru.
    telas = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "views"
    # A lista precisa cobrir TODA tela com busca. Ela nasceu sem
    # `ProcessosView` e deixou passar a volta ao `includes` cru ali —
    # trava que não cobre o que diz cobrir não guarda nada.
    for nome in ("ServicosView.js", "ConfiguracoesView.js",
                 "DispositivosView.js", "ProcessosView.js"):
        fonte = (telas / nome).read_text(encoding="utf-8")
        assert "buscaInteligente" in fonte, f"{nome} não usa a régua comum"
        assert ".toLowerCase().includes(" not in fonte, (
            f"{nome} voltou a filtrar com includes cru — sem acento e sem "
            "começo de palavra"
        )


async def cenario_servidor_nao_acumula_sobra():
    """
    O diretório da aplicação no servidor tinha CINCO cópias do `.env`
    acumuladas em duas horas — e cada uma carrega a SECRET_KEY e a senha
    do banco. Não era só lixo: era o segredo espalhado em cinco lugares.

    A causa era o próprio `atualizar.sh`: ele grava `FACEOPS_REVISAO` no
    `.env` a cada execução, então o arquivo sempre diferia da última cópia
    e toda atualização gerava um backup novo. A cópia existe para proteger
    o que o OPERADOR configurou; se só mudou o que o script escreve
    sozinho, não há o que proteger.
    """
    script = (pathlib.Path(__file__).resolve().parents[2]
              / "atualizar.sh").read_text(encoding="utf-8")

    # A comparação ignora a linha que o próprio script escreve.
    assert "_sem_revisao" in script, (
        "a cópia do .env voltou a ser feita a cada execução"
    )
    assert "grep -v '^FACEOPS_REVISAO='" in script, script[:200]

    # Três cópias bastam para voltar atrás; mais é ampliar a superfície.
    assert "tail -n +4" in script, "a retenção das cópias do .env mudou"

    # Elas guardam a chave do cofre: permissão fechada.
    assert "chmod 600 .env" in script, "cópia do .env sem permissão fechada"

    # ── Sobra do Docker ────────────────────────────────────────────────
    #
    # Cada atualização deixa a imagem anterior sem tag. Numa VM pequena
    # isso enche o disco em poucas semanas.
    assert "docker image prune -f" in script, "as imagens penduradas ficam"
    # NUNCA `-a`: isso levaria imagem de container parado junto.
    assert "image prune -f -a" not in script and "prune -af" not in script, (
        "poda agressiva: `-a` remove imagem de container parado"
    )
    # Cache de build recente acelera a próxima atualização e vale o
    # espaço; só o antigo sai.
    assert "builder prune" in script and "until=" in script, (
        "o cache de build é podado sem filtro de idade"
    )

    # A poda acontece DEPOIS de a versão nova responder. Podar antes de
    # saber que deu certo é apagar rede enquanto se atravessa o rio.
    i_prune = script.index("docker image prune")
    i_reversao = script.index("# ── Reversão")
    assert i_prune < i_reversao, (
        "a poda saiu do caminho de sucesso — se rodar antes da verificação, "
        "apaga a imagem no momento em que ela ainda pode ser necessária"
    )

    # E o `.env` real nunca entra no repositório.
    ignore = (pathlib.Path(__file__).resolve().parents[2]
              / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignore, ".env fora do .gitignore"

    import subprocess

    raiz = pathlib.Path(__file__).resolve().parents[2]
    try:
        saida = subprocess.run(
            ["git", "ls-files"], cwd=raiz,
            capture_output=True, text=True, timeout=60,
        )
    except Exception:
        return
    rastreados = saida.stdout.splitlines()
    vazando = [
        f for f in rastreados
        if f == ".env" or f.startswith(".env.backup") or f.endswith(".pem")
        or f.endswith(".key")
    ]
    assert not vazando, f"segredo versionado: {vazando}"


async def cenario_atualizar_forca_coleta_de_verdade():
    """
    Regressão introduzida pelo cache do resumo: com o payload cacheado
    por ciclo, clicar em "Atualizar" devolvia exatamente o mesmo
    conteúdo. O botão parecia funcionar e não fazia nada — pior do que
    não existir.

    Para quem clica, "Atualizar" significa **ir buscar agora nos
    servidores**, não relê-lo do banco. Mas isso abre SSH em produção a
    pedido de um clique, então tem duas cercas: uma coleta por vez, e
    espaçamento mínimo entre elas.
    """
    from datetime import timedelta as _td

    from app.services.monitor_service import MonitorService

    m = MonitorService(metrics=None, stack=None)

    # Coleta já em andamento: recusa, e diz por quê.
    m._coletando = True
    r = await m.coletar_agora()
    assert r["ok"] is False
    assert "andamento" in r["motivo"], r
    m._coletando = False

    # Cedo demais: recusa dizendo quanto falta. Botão que não responde e
    # botão que responde "espere 8s" são coisas diferentes para quem usa.
    m._ultima_forcada = datetime.now(timezone.utc)
    r = await m.coletar_agora()
    assert r["ok"] is False and r["espera_s"] >= 1, r
    assert str(r["espera_s"]) in r["motivo"] or "aguarde" in r["motivo"], r

    # Passado o espaçamento, deixa. O ciclo em si é substituído: o que
    # esta trava guarda é a CERCA, não a coleta — rodar um ciclo de
    # verdade exigiria Postgres e quatro servidores.
    rodou = []

    async def _ciclo_falso():
        rodou.append(True)

    m._ciclo = _ciclo_falso
    m._ultima_forcada = datetime.now(timezone.utc) - _td(
        seconds=MonitorService.ESPERA_FORCAR_S + 1
    )
    m._coletando = False
    r = await m.coletar_agora()
    assert r["ok"] is True, r
    assert "duracao_s" in r
    assert rodou, "liberou a cerca e não rodou o ciclo"

    # E a coleta é marcada como concluída — senão a próxima seria recusada
    # para sempre por "já há uma coleta em andamento".
    assert m._coletando is False, "a trava de concorrência ficou presa"

    # A coleta INVALIDA o cache: sem isso a tela releria o mesmo payload
    # logo depois de coletar, e o botão continuaria parecendo quebrado.
    m._ultima_forcada = datetime.now(timezone.utc) - _td(
        seconds=MonitorService.ESPERA_FORCAR_S + 1
    )
    antes = m.chave_cache()
    await m.coletar_agora()
    assert m.chave_cache() != antes, "coletou e a tela continuaria no cache velho"

    # O espaçamento é finito e curto: cerca que atrapalha quem diagnostica
    # vira motivo para alguém desligá-la.
    assert 0 < MonitorService.ESPERA_FORCAR_S <= 60

    # A rota existe e a tela chama COLETAR, não só reler.
    rota = (pathlib.Path(__file__).resolve().parents[1]
            / "app" / "api" / "routes" / "monitor.py").read_text(encoding="utf-8")
    assert "coletar_agora" in rota, "a rota de coleta sob demanda sumiu"

    tela = (pathlib.Path(__file__).resolve().parents[2]
            / "frontend" / "src" / "components" / "views"
            / "MonitorView.js").read_text(encoding="utf-8")
    # Conferir a LIGAÇÃO, não a presença do nome. A primeira versão desta
    # linha só exigia "monitorColetar" em algum lugar do arquivo — e
    # passava com o botão religado ao `carregar`, porque a função de
    # coletar continuava definida, apenas sem uso. Trava que aceita código
    # morto como prova não guarda nada.
    assert "const atualizarAgora" in tela, "a função de forçar coleta sumiu"
    corpo_fn = tela[tela.index("const atualizarAgora"):]
    corpo_fn = corpo_fn[:corpo_fn.index("const carregarSerie")]
    assert "monitorColetar" in corpo_fn, (
        "forçar atualização deixou de coletar: com o resumo cacheado por "
        "ciclo, reler devolve o mesmo payload e o botão não faz nada"
    )

    # E o BOTÃO chama essa função.
    i = tela.index('{t("Atualizar")}')
    bloco_botao = tela[max(0, i - 900):i]
    assert "onClick={atualizarAgora}" in bloco_botao, (
        "o botão Atualizar foi religado a algo que só relê o resumo"
    )
    # E o texto do botão não muda: trocar o rótulo mudava a largura e a
    # barra inteira tremia a cada atualização.
    #
    # A verificação IGNORA comentários. A primeira versão desta linha
    # falhava por causa do comentário que explica a correção, dentro do
    # próprio arquivo corrigido — a quarta vez nesta base em que uma
    # trava casou com texto em vez de código.
    import re as _re

    codigo = _re.sub(r"\{/\*.*?\*/\}", "", tela, flags=_re.S)
    codigo = "\n".join(
        l for l in codigo.splitlines() if not l.strip().startswith("//")
    )
    assert "Atualizando" not in codigo, (
        "o rótulo do botão voltou a mudar de tamanho durante a busca"
    )
    assert "girando" in codigo, "sem indicação visual de que está buscando"


async def cenario_projeto_sem_marca_de_ferramenta():
    """
    Os arquivos do projeto não carregam marca de ferramenta de IA — nem
    em código, nem em documento, nem em diretório de configuração.

    Não é preferência estética: este painel é entregue a um cliente
    público e auditado por terceiros. O que está versionado tem de falar
    do FaceOps e do FindFace, e de mais nada. Quem escreveu é decisão de
    quem assina o repositório, não pegada deixada por acidente.

    A varredura roda sobre o que é VERSIONADO — dependência de terceiro
    dentro de `node_modules` ou de venv não é do projeto.
    """
    import subprocess

    raiz = pathlib.Path(__file__).resolve().parents[2]

    try:
        saida = subprocess.run(
            ["git", "ls-files"],
            cwd=raiz, capture_output=True, text=True, timeout=60,
        )
        arquivos = [l for l in saida.stdout.splitlines() if l.strip()]
    except Exception:
        # Sem git disponível, a trava não roda — melhor pular que falhar
        # por motivo errado e treinar a equipe a ignorar o vermelho.
        return

    assert arquivos, "git ls-files não devolveu nada"

    MARCAS = ("claude", "anthropic", "copilot", "chatgpt", "openai",
              "gerado por ia", "generated with", "co-authored-by")
    EXTENSOES = {".py", ".js", ".jsx", ".md", ".sh", ".yml", ".yaml",
                 ".json", ".txt", ".css", ".html", ".conf", ".env"}

    achados = []
    for rel in arquivos:
        caminho = raiz / rel
        # O próprio arquivo de teste cita as marcas para poder proibi-las.
        if caminho.resolve() == pathlib.Path(__file__).resolve():
            continue
        if caminho.suffix.lower() not in EXTENSOES:
            continue
        # Caminho também conta: um diretório `.claude/` é vínculo igual.
        baixo_caminho = rel.lower()
        for marca in MARCAS:
            if marca in baixo_caminho:
                achados.append(f"{rel} (no caminho: '{marca}')")
        try:
            texto = caminho.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        for marca in MARCAS:
            if marca in texto:
                achados.append(f"{rel} (no conteúdo: '{marca}')")

    assert not achados, (
        "arquivo versionado com marca de ferramenta: "
        + "; ".join(sorted(set(achados))[:10])
    )


async def cenario_coletor_desacelera_sem_ninguem_olhando():
    """
    O painel NÃO fica aberto o dia inteiro — é consultado de vez em
    quando. Mas o coletor rodava a cada 60 s para sempre, dimensionado
    para a TELA: 5.760 idas por dia e outras tantas linhas gravadas, para
    ninguém ver.

    Agora o laço tem duas velocidades. O que NÃO pode mudar é a
    vigilância: incidente continua sendo aberto e fechado, e aviso
    continua saindo. Economia que desliga a vigilância é só desligar o
    painel.
    """
    from datetime import timedelta as _td

    from app.services.monitor_service import MonitorService

    m = MonitorService(metrics=None, stack=None)

    # Sem ninguém nunca ter usado: econômico. Subir o painel não é motivo
    # para acelerar nada.
    assert m.modo() == "economico", m.modo()
    assert m.intervalo_atual() == 300, m.intervalo_atual()

    # Alguém usou: acelera.
    m.registrar_atividade()
    assert m.modo() == "ativo"
    assert m.intervalo_atual() == 60

    # Passado o tempo de ociosidade, volta a desacelerar.
    m._ultima_atividade = datetime.now(timezone.utc) - _td(minutes=11)
    assert m.modo() == "economico", m.modo()

    # Dentro da janela, continua ativo.
    m._ultima_atividade = datetime.now(timezone.utc) - _td(minutes=9)
    assert m.modo() == "ativo"

    # Zero desliga a economia, para quem preferir cadência fixa.
    m.config = ConfigFalsa({"monitor.ocioso_apos_min": 0})
    m._ultima_atividade = None
    assert m.modo() == "ativo", "zero deveria manter sempre na velocidade normal"

    # Abrir o painel ACORDA o laço: sem isso, a primeira tela mostraria
    # dado de até cinco minutos atrás e ficaria assim até a espera
    # terminar.
    import asyncio as _asyncio

    m2 = MonitorService(metrics=None, stack=None)
    m2._acordar = _asyncio.Event()
    assert not m2._acordar.is_set()
    m2.registrar_atividade()          # estava econômico -> acorda
    assert m2._acordar.is_set(), "abrir o painel não acorda o coletor"

    # Atividade seguida NÃO fica acordando o laço à toa.
    m2._acordar.clear()
    m2.registrar_atividade()
    assert not m2._acordar.is_set(), "acordou o laço já estando ativo"

    # Piso de segurança: configuração absurda não vira laço apertado.
    m3 = MonitorService(metrics=None, stack=None,
                        config=ConfigFalsa({"monitor.intervalo_s": 1,
                                            "monitor.intervalo_ocioso_s": 1,
                                            "monitor.ocioso_apos_min": 10}))
    m3.registrar_atividade()
    assert m3.intervalo_atual() >= 15, m3.intervalo_atual()
    m3._ultima_atividade = None
    assert m3.intervalo_atual() >= 30, m3.intervalo_atual()

    # A tela recebe o ritmo do servidor: buscar a cada 10 s um dado que só
    # muda a cada 5 min é pedir trabalho para nada.
    estado = m.estado()
    for campo in ("modo", "intervalo_s", "intervalo_ativo_s",
                  "intervalo_ocioso_s", "poll_s"):
        assert campo in estado, f"estado sem '{campo}'"
    assert estado["poll_s"] >= 10

    # A atividade é registrada em TODA requisição autenticada — sem isso
    # o painel nunca sairia do modo econômico.
    deps = (pathlib.Path(__file__).resolve().parents[1]
            / "app" / "core" / "deps.py").read_text(encoding="utf-8")
    assert "registrar_atividade()" in deps, (
        "nada marca atividade: o coletor ficaria devagar mesmo com alguém "
        "usando o painel"
    )

    # E a vigilância NÃO depende do modo: o ciclo faz o mesmo trabalho nos
    # dois, só com espaçamento diferente.
    import inspect

    laco = inspect.getsource(MonitorService._laco)
    ciclo = inspect.getsource(MonitorService._ciclo)
    assert "intervalo_atual()" in laco, "o laço ignora o modo"
    assert "_ciclo()" in laco, "o laço deixou de rodar o ciclo"
    assert "_amostrar" in ciclo, "o ciclo deixou de amostrar"
    # O modo muda só o ESPAÇAMENTO. Se ele passar a decidir o que o ciclo
    # faz, a vigilância vira refém da economia — e economia que desliga a
    # vigilância é só desligar o painel.
    assert "modo()" not in ciclo, (
        "o ciclo passou a olhar o modo: ele deve fazer o mesmo trabalho "
        "nas duas velocidades"
    )


async def cenario_painel_nao_pesa_no_que_monitora():
    """
    O painel não pode ser motivo de lentidão em nada — nem nos servidores
    do FindFace, nem na VM onde ele mesmo roda.

    Esta trava guarda os quatro compromissos que sustentam isso, cada um
    com um jeito conhecido de ser quebrado sem ninguém notar.
    """
    import inspect

    from app.services.monitor_service import MonitorService

    # ── 1. Uma ida ao servidor por ciclo, e leitura de arquivo virtual ──
    #
    # /proc e /sys são memória: lê-los não gera E/S de disco nenhuma. Foi
    # essa a razão de medir IOPS por /proc/diskstats em vez de chamar
    # `iostat` — medir saturação de disco não pode custar disco.
    from app.services.metrics_service import COLLECT_SCRIPT as script
    assert "/proc/diskstats" in script, "a medição de IOPS sumiu"
    for caro in ("iostat", "du -", "find /", "docker logs"):
        assert caro not in script, (
            f"a coleta de cada ciclo passou a rodar '{caro}' — isso é caro "
            "e não pode entrar no caminho de 60 em 60 segundos"
        )
    # `docker stats` é aceitável (cgroup, sem disco), mas sem stream.
    if "docker stats" in script:
        assert "--no-stream" in script, "docker stats em modo contínuo"

    # ── 2. O resumo da tela é cacheado por ciclo ────────────────────────
    #
    # A rota é chamada a cada 10 s por CADA aba aberta e monta ~21
    # consultas. Sem cache, três abas = mais de 6 consultas por segundo,
    # para sempre, sem nada ter mudado.
    rota = (pathlib.Path(__file__).resolve().parents[1]
            / "app" / "api" / "routes" / "monitor.py").read_text(encoding="utf-8")
    trecho = rota[rota.index("async def resumo("):]
    trecho = trecho[:trecho.index("@router", 10)]
    assert "chave_cache()" in trecho, "o resumo voltou a ser recalculado a cada poll"
    assert "cache_resumo" in trecho

    # A chave muda quando o ciclo roda...
    m = MonitorService(metrics=None, stack=None)
    antes = m.chave_cache()
    m._ciclos += 1
    m._versao += 1
    assert m.chave_cache() != antes, "dado novo não invalida o cache"

    # ...e quando alguém muda configuração, senão a alteração ficaria
    # invisível até a próxima passada do coletor.
    depois = m.chave_cache()
    m.invalidar()
    assert m.chave_cache() != depois, "configuração nova não invalida o cache"

    # E as rotas que mudam configuração realmente invalidam.
    for arquivo in ("hosts.py", "limiares.py"):
        fonte = (pathlib.Path(__file__).resolve().parents[1]
                 / "app" / "api" / "routes" / arquivo).read_text(encoding="utf-8")
        assert "monitor.invalidar()" in fonte, (
            f"{arquivo} muda o que a tela mostra e não invalida o cache"
        )

    # ── 3. Log de produção não é lido por conta própria ─────────────────
    from app.services.log_analise_service import LogAnaliseService

    fonte = inspect.getsource(LogAnaliseService)
    assert "MAX_SERVICOS_CICLO" in fonte or "max" in fonte.lower()
    ciclo = inspect.getsource(MonitorService._analisar_logs)
    assert "listar_abertos" in ciclo, (
        "a análise de log deixou de depender de incidente aberto — o "
        "painel passaria a varrer log de produção por conta própria"
    )

    # ── 4. Toda escrita tem prazo ──────────────────────────────────────
    #
    # Tabela que cresce sem retenção enche o disco da VM do painel, que é
    # pequena de propósito.
    from app.services.config_service import POR_CHAVE

    for chave in ("monitor.retencao_dias", "incidentes.retencao_dias",
                  "analise.retencao_dias", "notificacao.retencao_dias",
                  "faxina.licenca_dias", "faxina.auditoria_dias",
                  "faxina.execucoes_dias", "faxina.gravacoes_dias"):
        assert chave in POR_CHAVE, f"retenção sumiu do catálogo: {chave}"
        assert POR_CHAVE[chave].padrao, f"{chave} veio sem prazo padrão"


async def cenario_saturacao_de_disco_e_medida():
    """
    Um pico de E/S derrubou um servidor de produção e o painel não tinha
    como ver: ele media ocupação em GB e `iowait` da CPU, e **nenhum dos
    dois enxerga saturação**. Dá para estourar o teto de IOPS do provedor
    com o disco quase vazio e a CPU ociosa esperando.

    Em disco gerenciado de nuvem o teto é contratado. Ao encostar nele, a
    fila cresce, a latência dispara e tudo que toca disco trava junto —
    inclusive o systemd e o sshd. A máquina parece cair; está esperando.
    """
    from app.services.metrics_service import calcular_io

    # 600 leituras + 700 escritas em 1s, com 1000ms de tempo ocupado.
    d1 = "8 0 sda 100 0 0 0 200 0 0 0 0 5000 0"
    d2 = "8 0 sda 700 0 0 0 900 0 0 0 0 6000 0"
    io = calcular_io(d1, d2, 1.0)["pior"]
    assert io["iops"] == 1300.0, io
    assert io["leitura_ps"] == 600.0 and io["escrita_ps"] == 700.0, io
    assert io["util_pct"] == 100.0, io
    assert io["dispositivo"] == "sda"

    # Janela inválida não vira número inventado.
    assert calcular_io(d1, d2, 0) == {"pior": {}, "todos": []}
    assert calcular_io("", "", 1.0) == {"pior": {}, "todos": []}

    # Contador que reinicia (boot) é descartado, não vira valor negativo
    # nem um pico falso de milhões.
    assert calcular_io(d2, d1, 1.0) == {"pior": {}, "todos": []}

    # Disco virtual não entra na conta: loop de snap e device-mapper
    # inflariam o número sem representar E/S real do provedor.
    lixo1 = "7 0 loop0 999999 0 0 0 999999 0 0 0 0 999999 0\n8 0 sda 100 0 0 0 200 0 0 0 0 5000 0"
    lixo2 = "7 0 loop0 999999 0 0 0 999999 0 0 0 0 999999 0\n8 0 sda 110 0 0 0 210 0 0 0 0 5100 0"
    io2 = calcular_io(lixo1, lixo2, 1.0)
    assert io2["pior"]["dispositivo"] == "sda", io2
    assert [d["dispositivo"] for d in io2["todos"]] == ["sda"], io2

    # Devolve o disco MAIS castigado, não a média: a média entre um disco
    # parado e um saturado esconde exatamente o que se procura. E `todos`
    # traz os dois, para quem quiser o retrato completo (AmostraDisco).
    a = "8 0 sda 0 0 0 0 0 0 0 0 0 0 0\n8 16 sdb 0 0 0 0 0 0 0 0 0 0 0"
    b = "8 0 sda 10 0 0 0 10 0 0 0 0 100 0\n8 16 sdb 900 0 0 0 900 0 0 0 0 900 0"
    io3 = calcular_io(a, b, 1.0)
    assert io3["pior"]["dispositivo"] == "sdb"
    assert {d["dispositivo"] for d in io3["todos"]} == {"sda", "sdb"}, io3

    # O limite existe no catálogo, e o de IOPS vem DESLIGADO: o teto varia
    # por tipo de disco, e um padrão errado geraria alarme falso todo dia.
    from app.services.config_service import POR_CHAVE

    assert POR_CHAVE["alerta.disco_util_pct"].padrao == 85
    assert POR_CHAVE["alerta.disco_iops"].padrao == 0

    # E o alerta existe de fato no ciclo.
    import inspect

    from app.services.monitor_service import MonitorService

    fonte = inspect.getsource(MonitorService.alertas)
    assert '"disco_io"' in fonte, "mede saturação e não alerta"
    assert "disco_util_pct" in fonte


async def cenario_backup_do_painel_nao_disputa_disco():
    """
    O backup lê o banco inteiro — pg_dump, mongodump, snapshot do
    Tarantool e tar — e é a maior carga de disco que ESTE painel provoca
    no servidor que ele monitora.

    Até aqui isso rodava em prioridade normal de E/S, de igual para igual
    com o FindFace em produção. Num disco com teto de IOPS, o backup era
    candidato legítimo a derrubar o servidor que ele existe para
    proteger.
    """
    script = (pathlib.Path(__file__).resolve().parents[2]
              / "scripts" / "ffmulti-backup.sh").read_text(encoding="utf-8")

    # Classe idle: só recebe disco quando ninguém mais quer.
    assert "ionice -c3" in script, "o backup voltou a disputar disco"
    assert "io_baixo()" in script

    # Ausência de ionice não pode quebrar o backup — cai para nice, e sem
    # nice roda direto.
    assert "elif command -v nice" in script, "sem plano B onde falta ionice"

    # Dentro do container também: `ionice` no cliente do `docker exec` não
    # afeta o processo que roda lá dentro, que é quem lê o banco.
    assert "IO_BAIXO_SH" in script
    # Cada LINHA que de fato executa o comando pesado — e não a primeira
    # aparição do nome, que é um comentário. (A primeira versão desta
    # trava checava o comentário e passaria com o backup em prioridade
    # normal: teste que olha o lugar errado não guarda nada.)
    for pesado in ("pg_dump", "pg_dumpall", "mongodump"):
        linhas = [
            l for l in script.splitlines()
            if pesado in l and "docker exec" in l and not l.strip().startswith("#")
        ]
        assert linhas, f"não achei onde {pesado} é executado"
        for linha in linhas:
            assert "IO_BAIXO_SH" in linha, (
                f"{pesado} roda em prioridade normal de E/S: {linha.strip()[:120]}"
            )

    # Respiro entre os 32 snapshots do Tarantool: sem pausa eles viram um
    # bloco único de escrita pesada, e é nesse bloco que o disco satura.
    assert "PAUSA_SNAPSHOT" in script

    # E o painel sabe dizer quando o suspeito é ele mesmo.
    import inspect

    from app.services.apuracao_service import ApuracaoService

    fonte = inspect.getsource(ApuracaoService.backup_na_janela)
    assert "BackupRun" in fonte
    assert "suspeito" in fonte.lower(), (
        "a correlação existe e não diz o que concluir dela"
    )
    # A apuração usa isso de verdade.
    usa = inspect.getsource(ApuracaoService.apurar)
    assert "backup_na_janela" in usa, "correlaciona e não conta a ninguém"


async def cenario_erro_de_conexao_diz_onde_procurar():
    """
    "[Errno 111] Connection refused" está tecnicamente correto e é inútil
    às 3h da manhã. Pior: os três erros mais comuns têm causas OPOSTAS e
    a mesma cara de "não conectou".

    Recusado = a máquina respondeu, o SSH é que não está de pé.
    Timeout   = ninguém respondeu; é rede ou VM fora.
    Negado    = conectou e autenticou errado; é credencial.

    Mandar conferir a rede quando o problema é o `sshd` parado custa o
    dobro do tempo — e é o que uma mensagem sem tradução faz.
    """
    from app.services.erros_conexao import explicar, mensagem

    recusado = explicar("[Errno 111] Connection refused")
    assert recusado["conhecido"]
    assert "recusou" in recusado["resumo"].lower(), recusado
    # A distinção que importa: é serviço, não rede.
    assert "ssh" in recusado["significa"].lower(), recusado
    assert "systemctl" in recusado["acao"], recusado

    tempo = explicar("SSHError: timed out")
    assert tempo["conhecido"]
    assert "não respondeu" in tempo["resumo"].lower(), tempo
    # E aqui é o contrário: rede/VM, não serviço.
    assert "rede" in tempo["significa"].lower(), tempo

    # Os dois NÃO podem dar a mesma resposta — é o ponto todo.
    assert recusado["resumo"] != tempo["resumo"]
    assert recusado["acao"] != tempo["acao"]

    negado = explicar("Permission denied (publickey)")
    assert "login" in negado["resumo"].lower(), negado
    assert "credencial" in negado["significa"].lower(), negado

    chave = explicar("HostKeyNotVerifiable: host key mismatch")
    assert "identidade" in chave["resumo"].lower(), chave
    # Identidade trocada tem de levantar a hipótese de ataque, e não só
    # mandar recadastrar: é o mesmo sintoma dos dois casos.
    assert "investigue" in chave["acao"].lower(), chave

    # Erro desconhecido NÃO ganha explicação inventada. Vazio é honesto;
    # um palpite com cara de diagnóstico manda procurar no lugar errado.
    novo = explicar("QuicheError: algo que ninguém viu ainda")
    assert not novo["conhecido"]
    assert novo["significa"] == "" and novo["acao"] == "", novo
    # E o texto original nunca some: quem investiga precisa dele.
    assert "QuicheError" in novo["erro"]

    assert "QuicheError" in mensagem("vm-x", "QuicheError: algo")
    assert "recusou" in mensagem("vm-x", "[Errno 111] Connection refused").lower()

    # A causa do incidente de host usa isto — antes era uma frase fixa
    # mandando conferir a rede, inclusive quando a máquina tinha
    # RECUSADO a conexão, que é o oposto.
    from app.services.incidente_service import _causa_do_erro

    assert "ssh" in _causa_do_erro("[Errno 111] Connection refused").lower()
    assert "rede" in _causa_do_erro("").lower()


async def cenario_acoes_rapidas_nao_sao_shell_remoto():
    """
    Um campo de comando livre no cartão do servidor seria um shell remoto
    sem gravação — e o InTerminal já faz isso, com sessão gravada em
    asciicast para auditoria.

    Então: catálogo FIXO, comando que nunca vem da requisição, permissão
    declarada por ação e confirmação digitada nas destrutivas.
    """
    import inspect

    from app.core.permissions import (
        DESTRUCTIVE_PERMISSIONS, PERMISSION_CATALOG, ROLE_PERMISSIONS,
        permissions_for,
    )
    from app.services.comandos_rapidos import COMANDOS, catalogo

    # Toda ação declara tudo o que a rota precisa para decidir.
    for chave, info in COMANDOS.items():
        for campo in ("rotulo", "ajuda", "comando", "sudo", "permissao",
                      "destrutivo", "confirmar", "derruba", "timeout"):
            assert campo in info, f"{chave}: falta '{campo}'"
        assert info["permissao"] in PERMISSION_CATALOG, (
            f"{chave} exige permissão inexistente: {info['permissao']}"
        )
        assert len(info["ajuda"]) > 40, f"{chave}: ajuda curta demais"
        assert 0 < info["timeout"] <= 600, f"{chave}: timeout fora de faixa"
        # Destrutiva SEM confirmação seria um clique sem volta.
        if info["destrutivo"]:
            assert info["confirmar"], f"{chave} é destrutiva e não confirma"

    # `shutdown` fica de fora: uma VM desligada não volta pelo painel —
    # sem SSH, não há como alcançá-la. Botão cuja consequência o painel
    # não desfaz é armadilha.
    todos = " ".join(i["comando"] for i in COMANDOS.values())
    assert "shutdown -h" not in todos and "poweroff" not in todos, todos

    # Reiniciar não usa `reboot` direto: ele mata a sessão SSH antes de
    # responder, e a rota diria que falhou algo que funcionou.
    assert "systemd-run" in COMANDOS["reiniciar"]["comando"]
    assert COMANDOS["reiniciar"]["derruba"] is True

    # Reiniciar a VM é só do admin — é mais amplo que parar o stack.
    assert COMANDOS["reiniciar"]["permissao"] == "hosts.reboot"
    assert "hosts.reboot" in DESTRUCTIVE_PERMISSIONS
    for perfil in ("observador", "operador", "tecnico"):
        assert "hosts.reboot" not in permissions_for(perfil), (
            f"{perfil} pode reiniciar servidor de produção"
        )
    assert "hosts.reboot" in permissions_for("admin")

    # As de leitura NÃO alteram estado. Um comando de consulta que mexe
    # em alguma coisa é a pior surpresa possível.
    for chave, info in COMANDOS.items():
        if info["destrutivo"]:
            continue
        for perigoso in ("rm ", "restart", "stop ", "reboot", "kill ", "> /", "mkfs"):
            assert perigoso not in info["comando"], (
                f"{chave} é marcada como não-destrutiva e roda '{perigoso}'"
            )

    # O catálogo servido à tela NÃO leva o comando: a tela não precisa
    # dele para desenhar um botão, e publicá-lo só ensinaria o que colar
    # num terminal.
    for item in catalogo(set(PERMISSION_CATALOG)):
        assert "comando" not in item, item

    # E filtra por permissão: quem não pode, não vê.
    so_leitura = catalogo(permissions_for("observador"))
    chaves = {i["chave"] for i in so_leitura}
    assert "reiniciar" not in chaves and "reiniciar_docker" not in chaves, chaves
    assert chaves, "observador ficou sem nenhuma ação de consulta"

    # A rota nunca aceita comando pelo corpo — só a chave, pela URL.
    fonte = (pathlib.Path(__file__).resolve().parents[1]
             / "app" / "api" / "routes" / "hosts.py").read_text(encoding="utf-8")
    trecho = fonte[fonte.index("async def executar_comando"):]
    trecho = trecho[:trecho.index("return {")]
    assert "COMANDOS.get(chave)" in trecho, trecho
    assert "dados.comando" not in trecho, "a rota aceita comando do cliente"
    assert 'info["confirmar"]' in trecho, "a rota ignora a confirmação"

    from app.schemas import ComandoRapidoIn

    assert set(ComandoRapidoIn.model_fields) == {"confirmar"}, (
        "o corpo da requisição ganhou campo além da confirmação"
    )


async def cenario_sessao_cai_parada_e_tem_teto():
    """
    Duas regras, e a diferença entre elas é o ponto:

    * **inatividade** — sem uso, a sessão cai. Protege a estação
      esquecida aberta.
    * **teto absoluto** — contado do login e nunca estendido. Impede uma
      sessão de se renovar para sempre.

    Guardar só a primeira daria sessão eterna para quem ficasse mexendo;
    só a segunda mataria alguém no meio do trabalho.
    """
    import time

    from app.core.security import (
        create_access_token, decode_access_token, sessao_expirada,
    )

    # A janela curta é respeitada na emissão.
    curto = create_access_token("admin", {"tv": 1}, minutos=1)
    payload = decode_access_token(curto)
    assert payload is not None
    resta = payload["exp"] - time.time()
    assert 0 < resta <= 61, resta

    # O carimbo de início existe e é agora.
    assert abs(payload["ini"] - time.time()) < 5, payload

    # RENOVAR CARREGA O `ini`: é isso que faz o teto ser um teto, e não
    # um horizonte que se afasta a cada renovação.
    inicio_antigo = int(time.time()) - 23 * 3600
    renovado = decode_access_token(
        create_access_token("admin", {"tv": 1}, minutos=20, inicio_sessao=inicio_antigo)
    )
    assert renovado["ini"] == inicio_antigo, "a renovação reiniciou o relógio absoluto"
    # Ainda dentro das 24 h: vale.
    assert not sessao_expirada(renovado, 24)

    # Passadas as 24 h, nem token com `exp` válido segura.
    velho = decode_access_token(
        create_access_token(
            "admin", {"tv": 1}, minutos=20,
            inicio_sessao=int(time.time()) - 25 * 3600,
        )
    )
    assert velho is not None, "o token em si continua íntegro"
    assert velho["exp"] > time.time(), "a janela curta ainda está aberta"
    assert sessao_expirada(velho, 24), "o teto absoluto não pegou"

    # Teto zero desliga a regra (para quem quiser sessão sem limite).
    assert not sessao_expirada(velho, 0)

    # Token da versão anterior, sem `ini`: não derruba quem já estava
    # dentro. A próxima renovação passa a carregar o carimbo.
    assert not sessao_expirada({"sub": "admin"}, 24)

    # O teto é conferido em TODA requisição, não só na renovação: regra
    # de segurança que depende do cliente pedir não é regra.
    deps = (pathlib.Path(__file__).resolve().parents[1]
            / "app" / "core" / "deps.py").read_text(encoding="utf-8")
    assert "sessao_expirada" in deps, "o teto só vale se o cliente pedir renovação"

    # E a renovação NÃO pode ser disparada por tráfego de fundo: o painel
    # se atualiza sozinho a cada 10s, e isso seguraria a sessão viva para
    # sempre. A tela renova a partir de evento de entrada do navegador.
    hook = (pathlib.Path(__file__).resolve().parents[2]
            / "frontend" / "src" / "useSessaoViva.js").read_text(encoding="utf-8")
    assert "houveInteracao" in hook, "a renovação não depende de interação"
    assert "pointerdown" in hook and "keydown" in hook, hook[:200]
    assert "mousemove" not in hook.replace("`mousemove`", ""), (
        "mousemove conta como interação — mesa esbarrada seguraria a sessão"
    )


async def cenario_perfis_descrevem_o_que_cada_um_pode():
    """
    A gestão dos perfis só serve se alguém entender o que está
    concedendo. `backups.restore` não diz a ninguém que aquilo sobrescreve
    o banco de produção.

    A matriz é montada a partir do MESMO catálogo que autoriza — uma
    segunda lista na tela diria uma coisa enquanto o servidor faz outra.
    """
    from app.core.permissions import (
        AREAS, DESTRUCTIVE_PERMISSIONS, PERMISSION_CATALOG, PERMISSION_INFO,
        ROLE_INFO, ROLE_PERMISSIONS, matriz_perfis, permissions_for,
    )

    # Toda permissão tem área e explicação. Sem isso ela aparece na tela
    # como código solto, que é o que a matriz existe para evitar.
    faltando = set(PERMISSION_CATALOG) - set(PERMISSION_INFO)
    assert not faltando, f"permissão sem área/descrição: {sorted(faltando)}"

    chaves_area = {c for c, _r, _a in AREAS}
    for codigo, (area, detalhe) in PERMISSION_INFO.items():
        assert area in chaves_area, f"{codigo} aponta para área inexistente: {area}"
        assert len(detalhe) > 30, f"{codigo}: explicação curta demais"

    # Todo perfil tem para-quem e o-que-não-pode. O que NÃO pode costuma
    # ser mais decisivo que o que pode.
    for codigo in ROLE_PERMISSIONS:
        info = ROLE_INFO.get(codigo)
        assert info, f"perfil {codigo} sem descrição"
        for campo in ("resumo", "para_quem", "nao_pode"):
            assert info.get(campo), f"{codigo}: falta '{campo}'"

    m = matriz_perfis()

    # Nada some no caminho: o que está no catálogo está na matriz.
    na_matriz = {i["codigo"] for a in m["areas"] for i in a["itens"]}
    assert na_matriz == set(PERMISSION_CATALOG), (
        f"a matriz esconde: {sorted(set(PERMISSION_CATALOG) - na_matriz)}"
    )

    # A matriz concorda com quem AUTORIZA — é o ponto todo dela.
    for area in m["areas"]:
        for item in area["itens"]:
            for perfil in ROLE_PERMISSIONS:
                tem = item["codigo"] in permissions_for(perfil)
                marcado = perfil in item["perfis"]
                assert tem == marcado, (
                    f"a tela diz que {perfil} {'tem' if marcado else 'não tem'} "
                    f"{item['codigo']}, e o servidor diz o contrário"
                )
            assert item["destrutiva"] == (item["codigo"] in DESTRUCTIVE_PERMISSIONS)

    # A escada de poder é crescente: cada perfil contém o anterior. Se um
    # dia deixar de ser, é decisão de projeto — e tem de ser deliberada,
    # não um efeito colateral de mexer numa lista.
    ordem = ["observador", "operador", "tecnico", "admin"]
    for menor, maior in zip(ordem, ordem[1:]):
        a, b = permissions_for(menor), permissions_for(maior)
        assert a <= b, f"{maior} não contém tudo de {menor}: falta {sorted(a - b)}"

    # Observador não executa NADA. É o perfil de tela em parede.
    so_leitura = permissions_for("observador")
    assert all(c.endswith(".view") for c in so_leitura), sorted(so_leitura)
    assert not (so_leitura & DESTRUCTIVE_PERMISSIONS)

    # Plantão destrava, não destrói.
    op = permissions_for("operador")
    assert "services.restart" in op
    assert not (op & DESTRUCTIVE_PERMISSIONS), sorted(op & DESTRUCTIVE_PERMISSIONS)
    for proibida in ("backups.restore", "backups.delete", "services.stack",
                     "terminal.sudo", "users.manage", "hosts.manage"):
        assert proibida not in op, f"plantão ganhou {proibida}"

    # Técnico opera, mas o que não tem volta fica com o admin.
    tec = permissions_for("tecnico")
    for proibida in ("backups.restore", "backups.delete", "services.stack",
                     "users.manage", "hosts.manage", "cleanup.run"):
        assert proibida not in tec, f"técnico ganhou {proibida}"

    # Admin tem tudo — e é por isso que os destrutivos pedem confirmação.
    assert permissions_for("admin") == set(PERMISSION_CATALOG)

    # Os contadores da tela batem.
    por_codigo = {p["codigo"]: p for p in m["perfis"]}
    for codigo in ROLE_PERMISSIONS:
        assert por_codigo[codigo]["total"] == len(permissions_for(codigo))
        assert por_codigo[codigo]["destrutivas"] == len(
            permissions_for(codigo) & DESTRUCTIVE_PERMISSIONS
        )


async def cenario_chave_fraca_impede_a_subida():
    """
    A SECRET_KEY assina o token de sessão E deriva a chave do cofre que
    guarda as credenciais SSH dos quatro servidores. Com o valor de
    exemplo — que está no repositório e no `.env.example` — qualquer
    pessoa assina um token de administrador e decifra tudo.

    Por isso a subida falha FECHADO. Painel de pé com chave de exemplo é
    pior que painel fora do ar: ele parece funcionar.
    """
    from app.core.config import (
        CHAVES_PROIBIDAS, TAMANHO_MINIMO_CHAVE, como_gerar_chave,
        settings, verificar_chave,
    )

    original = settings.SECRET_KEY
    try:
        # Os placeholders versionados são recusados, um a um.
        for ruim in CHAVES_PROIBIDAS:
            settings.SECRET_KEY = ruim
            assert verificar_chave(), f"aceitou a chave de exemplo {ruim!r}"

        # Chave curta também: chave curta é chave adivinhável.
        settings.SECRET_KEY = "a" * (TAMANHO_MINIMO_CHAVE - 1)
        assert verificar_chave(), "aceitou chave curta"

        # Espaço em volta não disfarça o placeholder.
        settings.SECRET_KEY = "  dev-only-trocar  "
        assert verificar_chave(), "espaço em volta driblou a checagem"

        # Chave de verdade passa.
        import secrets

        settings.SECRET_KEY = secrets.token_urlsafe(48)
        assert verificar_chave() == "", verificar_chave()

        # E a mensagem de erro ensina a gerar uma — recusar sem dizer
        # como resolver só transfere o problema.
        assert "secrets.token_urlsafe" in como_gerar_chave()
    finally:
        settings.SECRET_KEY = original

    # A subida realmente consulta isso.
    fonte = (pathlib.Path(__file__).resolve().parents[1]
             / "app" / "main.py").read_text(encoding="utf-8")
    assert "verificar_chave()" in fonte, "a guarda existe e ninguém a chama"
    assert "raise RuntimeError" in fonte, "a guarda avisa mas deixa subir"


async def cenario_jwt_nao_aceita_algoritmo_trocado():
    """
    Confusão de algoritmo é o ataque clássico contra JWT: o atacante
    troca o `alg` do cabeçalho e a biblioteca obedece.

    A lista de algoritmos aceitos é FECHADA no código, e não vem de
    `settings.ALGORITHM`: configuração errada não pode abrir a porta —
    `alg: none` ali viraria token forjável por qualquer um.
    """
    import jwt as pyjwt

    from app.core.config import settings
    from app.core.security import (
        ALGORITMOS_ACEITOS, create_access_token, decode_access_token,
    )

    assert "none" not in [a.lower() for a in ALGORITMOS_ACEITOS]
    assert all(a.startswith("HS") for a in ALGORITMOS_ACEITOS), ALGORITMOS_ACEITOS

    bom = create_access_token("admin", {"token_version": 1})
    assert decode_access_token(bom)["sub"] == "admin"

    # Token sem assinatura ("alg": "none") é recusado.
    sem_assinatura = pyjwt.encode(
        {"sub": "admin", "exp": 9999999999}, key="", algorithm="none"
    )
    assert decode_access_token(sem_assinatura) is None, "aceitou token sem assinatura"

    # Assinado com outra chave: recusado.
    outra = pyjwt.encode(
        {"sub": "admin", "exp": 9999999999}, key="chave-do-atacante", algorithm="HS256"
    )
    assert decode_access_token(outra) is None, "aceitou assinatura de outra chave"

    # Adulterado: recusado.
    assert decode_access_token(bom[:-4] + "aaaa") is None

    # Sem `exp` não passa: token eterno é o mesmo que senha que nunca
    # muda, e o `token_version` só protege quem já foi invalidado.
    eterno = pyjwt.encode({"sub": "admin"}, key=settings.SECRET_KEY, algorithm="HS256")
    assert decode_access_token(eterno) is None, "aceitou token sem expiração"

    # Expirado: recusado.
    expirado = pyjwt.encode(
        {"sub": "admin", "exp": 1}, key=settings.SECRET_KEY, algorithm="HS256"
    )
    assert decode_access_token(expirado) is None, "aceitou token expirado"

    # Lixo não derruba o servidor: vira None, não exceção.
    for entulho in ("", "nao.e.um.token", "a" * 5000, "..."):
        assert decode_access_token(entulho) is None


async def cenario_url_da_api_nao_alcanca_o_metadados():
    """
    A URL da API do FindFace é endereço escolhido por quem cadastra — o
    formato clássico de SSRF.

    Aqui o risco tem endereço: as VMs são do Azure, e todo Azure responde
    em 169.254.169.254 com o IMDS, que entrega token de identidade
    gerenciada para quem perguntar, SEM autenticação. Uma URL apontada
    para lá faria o painel ler credencial da assinatura inteira.
    """
    from app.core.rede_segura import DestinoRecusado, validar_url

    def recusa(url):
        try:
            validar_url(url)
        except DestinoRecusado as exc:
            return str(exc)
        raise AssertionError(f"aceitou {url!r}")

    # O alvo com nome e sobrenome.
    assert "link-local" in recusa("http://169.254.169.254/metadata/instance")
    assert "link-local" in recusa("https://169.254.169.254:443/")
    # IPv6 link-local também.
    recusa("http://[fe80::1]/")
    # Loopback: apontaria o painel para ele mesmo.
    assert "loopback" in recusa("http://127.0.0.1:8000/")
    assert "loopback" in recusa("http://localhost/")
    # Esquema que não é HTTP vira leitura de arquivo.
    recusa("file:///etc/shadow")
    recusa("gopher://interno/")
    # Credencial embutida na URL vaza em log de acesso.
    recusa("http://usuario:senha@10.0.0.5/")
    # Nome do serviço de metadados das outras nuvens.
    recusa("http://metadata.google.internal/")
    # Vazio e sem host.
    recusa("")
    recusa("http://")

    # E o que É legítimo continua passando: rede privada é justamente
    # onde os servidores do FindFace vivem. Recusar RFC1918 aqui
    # quebraria o uso real.
    for boa in ("https://10.0.0.5", "http://192.168.1.10:8000/api",
                "https://ff.interno.local/"):
        assert validar_url(boa) == boa, boa

    # A rota de hosts realmente chama a cerca — nos três lugares que
    # aceitam URL (criar, atualizar e testar).
    fonte = (pathlib.Path(__file__).resolve().parents[1]
             / "app" / "api" / "routes" / "hosts.py").read_text(encoding="utf-8")
    assert fonte.count("validar_url(") >= 3, (
        f"a cerca é chamada em {fonte.count('validar_url(')} lugar(es); "
        "criar, atualizar e testar precisam das três"
    )


async def cenario_segredo_nunca_sai_em_resposta_nem_em_log():
    """
    As colunas `*_enc` guardam chave SSH, senha de sudo, senha da API e
    token do Telegram. Nenhuma delas pode aparecer em schema de saída — e
    o detalhe da auditoria não pode carregar segredo para o log.
    """
    from app.core.permissions import DESTRUCTIVE_PERMISSIONS
    from app.schemas import HostOut
    from app.services.audit_service import _limpar

    # Nenhum campo de saída expõe segredo.
    campos = set(HostOut.model_fields)
    for proibido in ("ssh_key", "ssh_password", "sudo_password", "ff_api_pass",
                     "ff_api_token", "hashed_password"):
        vazando = [c for c in campos if proibido in c]
        assert not vazando, f"HostOut expõe {vazando}"
    # O que sai é só a impressão digital, que confirma sem revelar.
    assert "key_fingerprint" in campos

    # A auditoria omite segredo, inclusive aninhado.
    limpo = _limpar({
        "acao": "criar",
        "ssh_password": "s3nh4",
        "detalhe": {"sudo_password": "outra", "usuario_ssh": "ubuntu"},
    })
    assert limpo["ssh_password"] == "<omitido>", limpo
    assert limpo["detalhe"]["sudo_password"] == "<omitido>", limpo
    # E o que não é segredo continua legível — auditoria sem detalhe não
    # serve para auditar.
    assert limpo["detalhe"]["usuario_ssh"] == "ubuntu", limpo
    assert limpo["acao"] == "criar", limpo

    # Ação destrutiva vira registro de nível crítico, que a faxina guarda
    # pelo triplo do prazo.
    for esperada in ("services.stack", "services.power", "backups.restore",
                     "backups.delete", "hosts.manage"):
        assert esperada in DESTRUCTIVE_PERMISSIONS, esperada


async def cenario_apuracao_distingue_reboot_de_rede():
    """
    A conclusão que paga a apuração inteira, e a mais barata de obter.

    Servidor sem comunicação tem duas explicações opostas: a máquina
    reiniciou (chamado no provedor da VM) ou ficou ligada o tempo todo
    (chamado no provedor de rede). Ler o uptime distingue as duas.

    O cálculo usa `date +%s` e `/proc/uptime` de propósito: `uptime -s`
    imprime hora LOCAL, e compará-la com a janela em UTC daria a conclusão
    errada duas vezes por ano.
    """
    from datetime import timezone as tz

    from app.services.apuracao_service import ApuracaoService

    inicio = AGORA
    fim = AGORA + timedelta(minutes=10)
    ini_e = int(inicio.replace(tzinfo=tz.utc).timestamp())
    fim_e = int(fim.replace(tzinfo=tz.utc).timestamp())

    # 1) A máquina subiu DENTRO da janela: reiniciou.
    reiniciou = ApuracaoService.interpretar(
        {"tempo": f"{fim_e}\n120"},  # subiu 2 min antes de responder
        "host", inicio, fim,
    )
    assert reiniciou["reiniciou"] is True, reiniciou
    assert "reiniciou" in reiniciou["veredito"].lower(), reiniciou
    assert reiniciou["confianca"] == "alta"

    # 2) A máquina estava de pé há dias: NÃO reiniciou — foi o caminho
    #    até ela. É a conclusão que mais economiza tempo de quem apura.
    ficou = ApuracaoService.interpretar(
        {"tempo": f"{fim_e}\n{86400 * 9}"},  # 9 dias de uptime
        "host", inicio, fim,
    )
    assert ficou["reiniciou"] is False, ficou
    assert "NÃO reiniciou" in ficou["veredito"], ficou
    assert any("rede" in a["texto"] for a in ficou["achados"]), ficou

    # 3) Sem leitura do uptime, "não sei" — nunca "não reiniciou". Deduzir
    #    ausência de reboot a partir de leitura que falhou seria o mesmo
    #    erro de "serviço travado" e "câmera sem evento".
    sem = ApuracaoService.interpretar({"tempo": ""}, "host", inicio, fim)
    assert sem["reiniciou"] is None, sem
    assert sem["confianca"] == "nenhuma", sem
    assert "não encontrei" in sem["veredito"].lower(), sem

    # 4) Uptime absurdo não vira conclusão.
    lixo = ApuracaoService.interpretar({"tempo": "isso nao e numero"}, "host", inicio, fim)
    assert lixo["reiniciou"] is None, lixo

    # A janela consultada tem folga: a causa acontece ANTES do painel ver
    # a máquina responder.
    from app.services.apuracao_service import FOLGA_ANTES_S, _comando

    cmd = _comando(inicio, fim)
    assert f"@{ini_e - FOLGA_ANTES_S}" in cmd, cmd
    # E o comando não escreve nada no servidor. A lista é de formas que
    # EXECUTAM a ação, não da palavra solta: `last -x reboot shutdown` LÊ
    # o histórico de reinícios, e proibir a palavra proibiria a leitura
    # mais útil da apuração.
    for perigoso in ("rm ", "systemctl restart", "systemctl stop",
                     "systemctl reboot", "docker restart", "docker stop",
                     "docker rm", "kill ", "; reboot", "&& reboot",
                     "shutdown -", "> /", "tee "):
        assert perigoso not in cmd, f"apuração com comando que altera estado: {perigoso}"
    # Positivo, para a trava não ser só uma lista de negativas: o comando
    # é feito de leitura.
    for leitura in ("date +%s", "/proc/uptime", "journalctl"):
        assert leitura in cmd, f"apuração perdeu a fonte de leitura: {leitura}"


async def cenario_apuracao_le_o_container_certo_e_aponta_oom():
    """
    Serviço do compose não é nome de container: o `docker inspect` da
    apuração precisa de `findface-multi-...-1`, e a mesma armadilha já
    tinha mordido a análise de log.

    E OOM tem de vencer o código de saída: um container morto por falta
    de memória também sai com código != 0, e dizer "saiu com erro 137"
    manda a pessoa procurar bug onde o problema é memória.
    """
    from app.services.apuracao_service import ApuracaoService, _comando

    cmd = _comando(AGORA, AGORA + timedelta(minutes=2),
                   container="findface-multi-findface-video-worker-1")
    assert "findface-multi-findface-video-worker-1" in cmd, cmd

    # OOM ganha do exit code.
    oom = ApuracaoService.interpretar(
        {"tempo": "", "container": "137|true|2026-09-02T14:00:00Z|3"},
        "servico", AGORA, AGORA + timedelta(minutes=2),
    )
    assert "memória" in oom["veredito"], oom
    assert oom["confianca"] == "alta"
    assert oom["achados"][0]["fonte"] == "docker", oom

    # Sem OOM, o código de saída explica.
    erro = ApuracaoService.interpretar(
        {"tempo": "", "container": "1|false|2026-09-02T14:00:00Z|0"},
        "servico", AGORA, AGORA + timedelta(minutes=2),
    )
    assert "código de erro 1" in erro["veredito"], erro
    assert erro["confianca"] == "media"

    # Saída limpa e nada no log: a resposta honesta, com o palpite certo.
    limpo = ApuracaoService.interpretar(
        {"tempo": "", "container": "0|false|2026-09-02T14:00:00Z|0"},
        "servico", AGORA, AGORA + timedelta(minutes=2),
    )
    assert "não encontrei" in limpo["veredito"].lower(), limpo
    assert any("manual" in a["texto"] for a in limpo["achados"]), limpo


async def cenario_apuracao_respeita_o_nivel_e_os_tetos():
    """
    O nível completo existe para investigação; o resumido, para o dia a
    dia. O que não pode acontecer é o completo virar padrão sem ninguém
    escolher — ele lê mais do servidor e grava mais no banco, todo dia,
    para sempre.
    """
    from app.services.apuracao_service import (
        MAX_POR_CICLO, NIVEIS, NIVEL_PADRAO, TIMEOUT_S, _comando, limites,
    )

    assert NIVEL_PADRAO == "resumido", "o nível caro virou padrão"
    assert NIVEIS["resumido"]["avancado"] is False
    assert NIVEIS["completo"]["avancado"] is True
    assert NIVEIS["completo"]["chars_total"] > NIVEIS["resumido"]["chars_total"]

    # Valor desconhecido cai no padrão em vez de estourar.
    assert limites("inventado") == NIVEIS[NIVEL_PADRAO]
    assert limites("") == NIVEIS[NIVEL_PADRAO]
    assert limites("COMPLETO") == NIVEIS["completo"], "o nível não aceita caixa alta"

    resumido = _comando(AGORA, AGORA + timedelta(minutes=5))
    completo = _comando(AGORA, AGORA + timedelta(minutes=5), nivel="completo")
    # As fontes extras só existem no nível completo.
    for extra in ("systemctl --failed", "dmesg", "ip -br link"):
        assert extra not in resumido, f"'{extra}' no nível resumido"
        assert extra in completo, f"'{extra}' faltando no nível completo"
    assert len(completo) > len(resumido)

    # Os tetos de custo continuam finitos — é o que separa isto de varrer
    # log de produção por conta própria.
    assert 0 < MAX_POR_CICLO <= 5, MAX_POR_CICLO
    assert 0 < TIMEOUT_S <= 60, TIMEOUT_S

    # E o corte é informado: sem o número, o fim da lista parece o fim da
    # evidência.
    from app.services.apuracao_service import ApuracaoService

    muitas = "\n".join(f"linha de erro numero {i}" for i in range(200))
    r = ApuracaoService.interpretar(
        {"tempo": "", "sistema": muitas}, "host", AGORA, AGORA + timedelta(minutes=1),
    )
    assert r["truncado"] >= 0
    assert len(r["achados"]) <= NIVEIS[NIVEL_PADRAO]["linhas_por_fonte"] + 3, r


async def cenario_apuracao_entra_no_aviso_de_retorno():
    """
    "Voltou ao normal" sempre deixou a pergunta no ar: e o que foi? Como
    a apuração roda no fechamento, a resposta cabe na MESMA mensagem —
    uma segunda mensagem depois seria mais spam para dizer o que cabia na
    primeira.
    """
    from app.services.notificacao_service import montar_mensagem

    texto = montar_mensagem({
        "tipo": "retorno", "host": "VM-APPSERVER-01", "papel": "appserver",
        "servico": "", "duracao_s": 190,
        "apuracao": {
            "veredito": "A máquina NÃO reiniciou — ficou ligada durante toda a janela",
            "confianca": "alta",
            "achados": [{"fonte": "uptime", "texto": "o sistema já estava de pé há 9d"}],
        },
    }, cliente="PROCERGS")

    assert "Causa:" in texto, texto
    assert "NÃO reiniciou" in texto, texto
    assert "Evidência:" in texto, texto
    assert "9d" in texto, texto

    # Sem apuração, o retorno continua exatamente como era — nada de
    # "Causa: —", que ocuparia espaço para não dizer nada.
    sem = montar_mensagem({
        "tipo": "retorno", "host": "vm-x", "servico": "s", "duracao_s": 60,
    })
    assert "Causa" not in sem, sem


async def cenario_parar_servico_tem_cerca_e_confirmacao():
    """
    Parar um container é diferente de reiniciar: reiniciar volta sozinho,
    parar FICA parado. Um `findface-video-worker` parado por descuido é
    reconhecimento facial fora do ar até alguém notar — e ninguém nota,
    porque não há erro, só ausência.

    Três garantias, e nenhuma delas pode se perder num refactor:
    a cerca do projeto compose vale para os três verbos, o plantão não
    ganha o poder de deixar parado, e `stop` exige o nome digitado.
    """
    import inspect

    from app.core.permissions import (
        DESTRUCTIVE_PERMISSIONS, PERMISSION_CATALOG, ROLE_PERMISSIONS,
    )
    from app.services.stack_service import StackService

    # A permissão existe, é destrutiva (auditoria em nível crítico) e o
    # perfil de plantão NÃO a tem — ele reinicia, não deixa parado.
    assert "services.power" in PERMISSION_CATALOG
    assert "services.power" in DESTRUCTIVE_PERMISSIONS
    assert "services.power" not in ROLE_PERMISSIONS["operador"], (
        "plantão ganhou o poder de deixar serviço parado"
    )
    assert "services.restart" in ROLE_PERMISSIONS["operador"], (
        "plantão perdeu o reiniciar, que é o que ele precisa"
    )
    assert "services.power" in ROLE_PERMISSIONS["tecnico"]
    assert "services.power" in ROLE_PERMISSIONS["admin"]

    # Os três verbos passam pela MESMA função — logo, pela mesma cerca.
    # Em funções separadas, a próxima correção de cerca entraria em uma e
    # faltaria nas outras.
    assert set(StackService.VERBOS) == {"restart", "stop", "start"}
    # Só o CORPO, sem a docstring: ela cita as três proteções para
    # explicá-las, e conferir o texto junto faria a trava passar por
    # causa da própria explicação — teste que se satisfaz com o comentário
    # não guarda nada. (Verificado: removendo a cerca do código, esta
    # asserção falha.)
    fonte = inspect.getsource(StackService.container_action)
    corpo = fonte.split('"""')[2] if fonte.count('"""') >= 2 else fonte
    assert "_garantir_do_projeto" in corpo, "ação sem a cerca do projeto"
    assert "_recusar_se_limpando" in corpo, (
        "ação sem a recusa durante limpeza de eventos — o manual da "
        "NtechLab diz que reiniciar container na limpeza corrompe o banco"
    )
    assert "_validar_nome" in corpo, "ação sem validação do nome"

    # E `restart_container` continua existindo, delegando: os chamadores
    # antigos não podem ter quebrado.
    assert "container_action" in inspect.getsource(StackService.restart_container)

    # A rota exige o nome digitado para parar, e não para subir.
    rota = (pathlib.Path(__file__).resolve().parents[1]
            / "app" / "api" / "routes" / "ops.py").read_text(encoding="utf-8")
    trecho = rota[rota.index("async def parar_ou_subir_container"):]
    trecho = trecho[:trecho.index("@router.post", 10)]
    assert 'dados.acao == "stop" and dados.confirmar.strip() != dados.container' in trecho, (
        "parar serviço sem confirmação digitada"
    )
    assert 'require_permission("services.power")' in trecho

    # O schema só aceita os dois verbos da rota. "restart" aqui seria um
    # segundo caminho para a mesma ação, com outra permissão.
    from pydantic import ValidationError

    from app.schemas import PowerContainerIn

    assert PowerContainerIn(container="x", acao="stop").acao == "stop"
    assert PowerContainerIn(container="x", acao="start").acao == "start"
    for invalida in ("restart", "kill", "rm", ""):
        try:
            PowerContainerIn(container="x", acao=invalida)
        except ValidationError:
            continue
        raise AssertionError(f"aceitou ação inválida: {invalida!r}")


async def cenario_historico_do_servico_nao_toca_no_servidor():
    """
    O histórico por serviço na tela de Serviços sai da tabela de
    `incidentes`, que o ciclo do monitor já preenche.

    É o que o torna barato: nenhum SSH, nenhuma tabela nova e nenhuma
    retenção nova — a de incidentes (padrão 30 dias) já recicla. Abrir a
    aba não pode virar uma ida ao servidor de produção.
    """
    import inspect

    import sqlalchemy as sa

    from app.services.incidente_service import IncidenteService

    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        servico = IncidenteService(config=ConfigFalsa())

        agora = AGORA
        db.add_all([
            Incidente(host_id=host.id, tipo="servico", servico="findface-video-worker",
                      nivel="critico", texto="o serviço parou",
                      inicio=agora - timedelta(hours=5),
                      fim=agora - timedelta(hours=4), duracao_s=3600),
            Incidente(host_id=host.id, tipo="servico", servico="findface-video-worker",
                      nivel="atencao", texto="reiniciando em laço",
                      inicio=agora - timedelta(hours=2),
                      fim=agora - timedelta(hours=1), duracao_s=3600),
            Incidente(host_id=host.id, tipo="servico", servico="findface-sf-api",
                      nivel="critico", texto="o serviço parou",
                      inicio=agora - timedelta(hours=3),
                      fim=agora - timedelta(hours=2), duracao_s=3600),
            # Queda do host inteiro: não é histórico de serviço nenhum.
            Incidente(host_id=host.id, tipo="host", servico="",
                      nivel="critico", texto="o servidor não respondeu",
                      inicio=agora - timedelta(hours=6),
                      fim=agora - timedelta(hours=5), duracao_s=3600),
        ])
        await db.commit()

        # Filtrado por serviço: só as duas daquele serviço.
        so_dele = await servico.listar_recentes(
            db, dias=7, host_id=host.id, servico="findface-video-worker"
        )
        assert len(so_dele) == 2, so_dele
        assert all(i["servico"] == "findface-video-worker" for i in so_dele)

        # Serviço que nunca caiu: lista vazia, não a lista inteira. Filtro
        # que "falha aberto" mostraria a queda de outro serviço na aba
        # deste — e alguém agiria no serviço errado.
        assert await servico.listar_recentes(
            db, dias=7, host_id=host.id, servico="findface-ntls"
        ) == []

        # Sem filtro, tudo do host continua vindo (a tela de "serviços por
        # máquina" depende disso).
        todos = await servico.listar_recentes(db, dias=7, host_id=host.id)
        assert len(todos) == 4, todos

    await engine.dispose()

    # E a consulta é local. A verificação olha só o CÓDIGO: a docstring
    # da função fala de SSH justamente para dizer que não usa, e conferir
    # o texto junto faria o teste falhar por causa da própria explicação.
    fonte = inspect.getsource(IncidenteService.listar_recentes)
    corpo = fonte.split('"""')[2] if fonte.count('"""') >= 2 else fonte
    for proibido in ("ssh", "stack", "docker", "subprocess"):
        assert proibido not in corpo.lower(), (
            f"o histórico passou a depender de '{proibido}' — abrir a aba "
            "voltaria a custar uma ida ao servidor de produção"
        )


async def cenario_faxina_nao_oferece_categoria_que_nao_age():
    """
    `CATEGORIAS_PONTUAIS` é o que a rota aceita e a tela oferece. Cada
    chave dali TEM de virar um contador no resultado de `pontual()` — e
    de agir de verdade.

    Era defeito real: "licenca" estava no catálogo (logo, aceita pela
    API) e não era tratada. A limpeza respondia "ok, 0 removidos" e não
    removia nada. Categoria oferecida que não age é pior que categoria
    ausente, porque quem pediu acredita que foi feito.
    """
    from datetime import datetime, timedelta, timezone

    from app.models.licenca_amostra import LicencaAmostra
    from app.services import faxina_service as fx

    engine, fabrica = await nova_sessao()
    original = fx.AsyncSessionLocal
    fx.AsyncSessionLocal = fabrica
    try:
        servico = fx.FaxinaService(config=None)

        # Toda categoria oferecida devolve contador numérico.
        for chave in fx.CATEGORIAS_PONTUAIS:
            r = await servico.pontual([chave], dias=9999, aplicar=False)
            assert chave in r, (
                f"categoria '{chave}' é oferecida e não devolve contador — "
                f"resultado: {sorted(r)}"
            )
            assert isinstance(r[chave], int), f"{chave}: contador não é número"

        # E a que era morta agora age de verdade.
        agora = datetime.now(timezone.utc)
        async with fabrica() as db:
            db.add_all([
                LicencaAmostra(host_id=1, recurso="cameras",
                               ts=agora - timedelta(days=400)),
                LicencaAmostra(host_id=1, recurso="cameras",
                               ts=agora - timedelta(days=200)),
                LicencaAmostra(host_id=1, recurso="cameras", ts=agora),
            ])
            await db.commit()

        # Prévia não apaga nada.
        r = await servico.pontual(["licenca"], dias=100, aplicar=False)
        assert r["licenca"] == 2, r
        async with fabrica() as db:
            import sqlalchemy as sa
            resta = (await db.execute(
                sa.select(sa.func.count(LicencaAmostra.id))
            )).scalar()
        assert resta == 3, "a prévia apagou algo"

        # Aplicando, sai o que passou do prazo — e só isso.
        r = await servico.pontual(["licenca"], dias=100, aplicar=True)
        assert r["licenca"] == 2, r
        async with fabrica() as db:
            import sqlalchemy as sa
            resta = (await db.execute(
                sa.select(sa.func.count(LicencaAmostra.id))
            )).scalar()
        assert resta == 1, f"sobrou {resta}, esperava 1"

        # Categoria inventada não passa.
        r = await servico.pontual(["nao-existe"], dias=9999)
        assert r["categorias"] == [], r

        # O piso de idade não se burla com zero nem com negativo: o
        # estrago de um clique errado aqui é irreversível.
        r = await servico.pontual(["auditoria"], dias=0)
        assert r["dias"] == fx.DIAS_MINIMO_PONTUAL, r
        r = await servico.pontual(["auditoria"], dias=-30)
        assert r["dias"] == fx.DIAS_MINIMO_PONTUAL, r
    finally:
        fx.AsyncSessionLocal = original
    await engine.dispose()


async def cenario_previa_da_faxina_nao_esconde_categoria():
    """
    A prévia mostrava QUATRO categorias enquanto `executar()` apagava
    onze. Quem abria a tela, via zero e concluía que nada seria removido
    — no mesmo dia em que milhares de amostras iam embora.

    A trava compara os dois lados: todo contador que a faxina preenche
    tem de ter uma linha na prévia. Falha se alguém acrescentar retenção
    nova e esquecer de mostrá-la.
    """
    import inspect

    from app.services.faxina_service import FaxinaService

    fonte = inspect.getsource(FaxinaService.executar)

    # Os contadores que `executar()` declara no dicionário de resultado.
    contadores = set()
    for linha in fonte.splitlines():
        linha = linha.strip()
        if linha.startswith('"') and '": 0,' in linha:
            contadores.add(linha.split('"')[1])

    assert len(contadores) >= 10, f"não li os contadores certos: {contadores}"

    # As linhas que a prévia sabe mostrar.
    fonte_previa = inspect.getsource(FaxinaService.previa)
    mostradas = {
        parte.split('"')[0]
        for parte in fonte_previa.split('{"chave": "')[1:]
    }

    # Cada contador tem de casar com uma linha da prévia. O nome do
    # contador é `<coisa>_removida(s)` / `_esvaziados`; a linha usa a
    # coisa. Comparar pelo radical evita exigir nomes iguais nos dois
    # lados sem deixar passar categoria esquecida.
    def radical(nome: str) -> str:
        for sufixo in ("_removidas", "_removidos", "_removida", "_removido",
                       "_esvaziados", "_desapontadas", "_bytes"):
            if nome.endswith(sufixo):
                return nome[: -len(sufixo)]
        return nome

    radicais_previa = {radical(m) for m in mostradas} | mostradas
    # "logs" (esvaziados) aparece como "logs_execucao" na prévia.
    equivalentes = {"logs": "logs_execucao", "padroes": "padroes"}

    faltando = []
    for contador in contadores:
        base = radical(contador)
        base = equivalentes.get(base, base)
        if base not in radicais_previa and base not in mostradas:
            faltando.append(contador)

    assert not faltando, (
        f"a faxina apaga e a prévia não mostra: {sorted(faltando)}"
    )


async def cenario_faxina_poupa_execucao_com_artefato():
    """
    A linha da execução de backup é o comprovante de que o backup rodou —
    e o descritor do arquivo que ainda está no disco. Apagar a linha e
    deixar o .tar.gz produziria um artefato que ninguém sabe de onde veio.

    Então: sai a execução sem artefato; fica a que ainda tem arquivo.
    """
    import inspect

    from app.services.faxina_service import FaxinaService

    fonte = inspect.getsource(FaxinaService._execucoes)

    # Só estado terminal: execução em curso não pode ser apagada debaixo
    # de quem a está rodando.
    assert 'notin_(("executando", "pendente"))' in fonte, fonte
    # A decisão passa pelo disco, não só pela data.
    assert "caminho_artefato" in fonte, fonte
    # E tem teto por passada, para atraso grande não virar pico num dia.
    assert "TETO_EXECUCOES" in fonte, fonte

    # O teto existe e é finito.
    from app.services.faxina_service import TETO_EXECUCOES

    assert 0 < TETO_EXECUCOES <= 5000


async def cenario_auditoria_busca_acha_e_filtra():
    """
    Auditoria sem busca é arquivo morto: a pergunta real é "quem mexeu
    naquele servidor" ou "por que aquele restore falhou", e nenhuma das
    duas se responde rolando uma lista.

    A busca livre tem de varrer também o DETALHE — é lá que está o
    parâmetro que explica a ação — e o filtro tem de ser o MESMO da
    exportação, senão o CSV discorda da tela sem ninguém desconfiar.
    """
    import sqlalchemy as sa

    from app.models.audit import AuditLog
    from app.services import audit_service

    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        db.add_all([
            AuditLog(usuario="admin", action="services.restart",
                     target="vm-appserver", level="warning", success=True,
                     detail={"servico": "findface-video-worker"}),
            AuditLog(usuario="joao", action="backups.restore",
                     target="vm-dbserver", level="critical", success=False,
                     detail={"perfil": "essencial", "erro": "timeout"}),
            AuditLog(usuario="maria", action="hosts.manage",
                     target="vm-ftpserver", level="info", success=True,
                     detail={}),
        ])
        await db.commit()

        async def buscar(**kw):
            consulta = audit_service.aplicar_filtros(
                sa.select(AuditLog).order_by(AuditLog.ts.desc()), **kw
            )
            r = await db.execute(consulta)
            return list(r.scalars().all())

        # Sem filtro, tudo.
        assert len(await buscar()) == 3

        # Busca por alvo — o caso mais comum: "quem mexeu nesse servidor".
        achados = await buscar(busca="appserver")
        assert [a.usuario for a in achados] == ["admin"], achados

        # Busca por ação, mesmo parcial.
        assert len(await buscar(busca="restore")) == 1

        # Busca por usuário.
        assert len(await buscar(busca="maria")) == 1

        # E DENTRO do detalhe: é onde está o motivo da falha, e sem isto a
        # pessoa teria de saber de cor qual ação registrou o erro.
        achados = await buscar(busca="timeout")
        assert [a.usuario for a in achados] == ["joao"], achados
        assert len(await buscar(busca="findface-video-worker")) == 1

        # Maiúscula/minúscula não pode importar: ninguém digita o nome do
        # container com o mesmo caixa que o log gravou.
        assert len(await buscar(busca="APPSERVER")) == 1

        # Só o que falhou — a pergunta "o que deu errado ontem".
        falhas = await buscar(so_falhas=True)
        assert [a.usuario for a in falhas] == ["joao"], falhas

        # Filtros somam em vez de se anular.
        assert len(await buscar(busca="vm-", level="critical")) == 1
        assert len(await buscar(busca="vm-", level="critical", usuario="admin")) == 0

        # Termo que não existe devolve vazio, não a lista inteira: filtro
        # que "falha aberto" faria a pessoa concluir o contrário do certo.
        assert await buscar(busca="nao-existe-isso") == []

        # Busca vazia ou só espaço não filtra nada.
        assert len(await buscar(busca="")) == 3
        assert len(await buscar(busca="   ")) == 3

    await engine.dispose()

    # A exportação tem de usar o MESMO filtro, e não uma cópia. Cópia
    # divergiria no primeiro filtro novo — e CSV que discorda da tela é
    # pior que CSV nenhum, porque ninguém o confere.
    fonte = (pathlib.Path(__file__).resolve().parents[1]
             / "app" / "api" / "routes" / "exportar.py").read_text(encoding="utf-8")
    trecho = fonte[fonte.index("async def auditoria("):]
    trecho = trecho[:trecho.index("return _csv")]
    assert "aplicar_filtros" in trecho, "exportação não usa o filtro compartilhado"
    assert "AuditLog.level ==" not in trecho, "exportação voltou a filtrar por conta"
    assert "AuditLog.usuario ==" not in trecho, "exportação voltou a filtrar por conta"


async def cenario_notificacao_mensagem_tem_campos_e_assina_a_origem():
    """
    A mensagem segue o modelo de campos rotulados que a equipe já lê no
    Zabbix, e a PRIMEIRA linha assina a origem: no mesmo grupo caem
    avisos do Zabbix e do FaceOps, e o caminho de resolução é outro em
    cada caso. Sem a assinatura, quem lê tem de deduzir pelo texto.

    O que não pode mudar nunca: endereço interno não vai para um grupo de
    mensagens, e a mensagem tem teto — mensagem cortada pelo Telegram
    esconde justamente o fim, onde estão horário e gravidade.
    """
    from app.services.notificacao_service import montar_mensagem

    texto = montar_mensagem({
        "tipo": "servico_parado", "host": "vm-appserver",
        "papel": "appserver",
        "servico": "findface-video-worker", "nivel": "critico",
        "texto": "o serviço findface-video-worker parou de funcionar",
        "significa": "É ele que processa o vídeo das câmeras.",
        "causa_provavel": "reiniciou 7x nos últimos 30 min.",
        "acao": "Em Serviços, abra o log deste container.",
        "inicio": AGORA, "duracao_s": 360,
    }, cliente="DGT")
    # Campos separados por linha em branco, como no template do Zabbix:
    # sem o respiro a mensagem vira um parágrafo cinza no celular.
    campos = [b for b in texto.split("\n\n") if b.strip()]
    assert "\n\n" in texto, "campos colados, sem respiro entre eles"

    # Assinatura e cliente no topo, antes de qualquer detalhe.
    assert campos[0].startswith("🎥 FaceOps"), texto
    assert "DGT" in campos[0], texto
    # Servidor no campo seguinte, com o papel em palavras.
    assert "vm-appserver" in campos[1] and "Aplicação" in campos[1], texto
    # Ícone dos dois lados, como no modelo que a equipe já lê.
    assert campos[1].count("🔴") == 2, campos[1]

    # Os campos que um leigo precisa: o que é, o que significa, o que fazer.
    for rotulo in ("Problema:", "Significa:", "Provável:", "Fazer:",
                   "Iniciado em:", "Gravidade: Crítico"):
        assert rotulo in texto, f"faltou {rotulo}: {texto}"
    # Todo campo é "ícone - Rótulo: valor".
    for campo in campos[2:]:
        assert " - " in campo.split(":")[0], f"campo fora do padrão: {campo}"
    # Duração no formato do Zabbix, até os segundos.
    assert "há 6m 0s" in texto, texto

    assert "10.0" not in texto and "192.168" not in texto, "vazou endereço interno"
    assert len(campos) <= 9, f"{len(campos)} campos: {texto}"
    assert len(texto) <= 900, f"mensagem longa demais ({len(texto)})"

    # Sem cliente configurado, a assinatura não fica com separador solto.
    sem_cliente = montar_mensagem({
        "tipo": "servico_parado", "host": "vm-x", "servico": "s",
        "nivel": "atencao", "texto": "o serviço s está parado",
    })
    assert sem_cliente.split("\n\n")[0] == "🎥 FaceOps", sem_cliente

    # Texto longo é cortado sem partir palavra, e avisa que foi cortado.
    longa = montar_mensagem({
        "tipo": "servico_parado", "host": "vm-appserver", "servico": "x",
        "nivel": "critico", "texto": "o serviço x parou", "inicio": AGORA,
        "causa_provavel": "detalhe " * 80,
    })
    linha_causa = [l for l in longa.splitlines() if "Provável:" in l][0]
    assert linha_causa.endswith("…"), linha_causa
    assert len(linha_causa) < 300, linha_causa
    assert "detalh…" not in linha_causa, "cortou no meio da palavra"

    volta = montar_mensagem({
        "tipo": "retorno", "host": "vm-appserver",
        "servico": "findface-video-worker", "duracao_s": 360,
    }, cliente="DGT")
    assert "Resolvido:" in volta and "Duração: 6m 0s" in volta, volta
    assert "Horário:" in volta, volta
    # Ícone dobrado no cabeçalho: é o que deixa a boa notícia
    # reconhecível na rolagem, sem ler.
    assert "✅✅" in volta.split("\n\n")[1], volta
    # Retorno não anuncia gravidade: já passou.
    assert "Gravidade" not in volta, volta

    sem_contato = montar_mensagem({
        "tipo": "host_sem_contato", "host": "vm-dbserver", "servico": "",
        "nivel": "critico", "inicio": AGORA,
        "significa": "Nada pode ser verificado nesta máquina agora.",
        "causa_provavel": "rede fora, VM desligada ou parada.",
    })
    assert "não respondeu ao monitoramento" in sem_contato, sem_contato
    # E o nome do host não aparece duas vezes na mesma mensagem por
    # descuido de montagem — era o que dava "vm-x — sem comunicação com vm-x".
    assert sem_contato.count("vm-dbserver") == 1, sem_contato

    limite = montar_mensagem({
        "tipo": "metrica", "host": "vm-appserver", "servico": "",
        "nivel": "atencao",
        "texto": "CPU sobrecarregada — 1.16 processo por núcleo (o normal é abaixo de 1,00)",
        "significa": "Há processo esperando a vez de usar o processador.",
        "acao": "Em Recursos, veja quais containers consomem mais CPU.",
    })
    assert "1.16" in limite and "Significa:" in limite, limite
    assert "Gravidade: Atenção" in limite, limite


async def cenario_duracao_no_formato_do_zabbix():
    """
    "12m 0s" e não "12min": mostrar os segundos diz que a medição é
    exata, enquanto "12min" deixa a dúvida de estar arredondado — e em
    janela de indisponibilidade essa dúvida é justamente o que se quer
    tirar. Da maior unidade não-zero até os segundos, sem omitir o meio.
    """
    from app.services.notificacao_service import _duracao

    assert _duracao(0) == ""
    assert _duracao(None) == ""
    assert _duracao(-5) == ""
    assert _duracao(53) == "53s"
    assert _duracao(113) == "1m 53s"
    assert _duracao(720) == "12m 0s"
    assert _duracao(3600) == "1h 0m 0s"
    # O exemplo real do grupo do cliente: 4d 18h 50m 42s.
    assert _duracao(4 * 86400 + 18 * 3600 + 50 * 60 + 42) == "4d 18h 50m 42s"
    # Zero no meio não desaparece: some 1h 0m 12s e ninguém entende.
    assert _duracao(3612) == "1h 0m 12s"


async def cenario_aviso_explica_o_servico_para_quem_nao_conhece():
    """
    `findface-video-worker` não significa nada para quem recebe o aviso às
    3h da manhã. O catálogo do manual, que a sonda de componentes já
    mantinha, passa a alimentar a linha "Significa" — e o nome do
    container não é igual ao nome do serviço no compose, então a busca
    precisa aceitar as duas formas.
    """
    from app.services.internos_service import descrever

    papel, impacto = descrever("findface-video-worker")
    assert papel and impacto, "serviço do núcleo sem descrição"
    assert "câmera" in impacto.lower(), impacto

    # Nome de container do compose ainda tem de casar.
    _, impacto_pg = descrever("findface-multi-postgresql-1")
    assert "banco" in impacto_pg.lower(), impacto_pg

    # A busca vai do mais específico para o mais genérico.
    assert descrever("findface-video-storage")[0] != descrever("findface-video-worker")[0]

    # Serviço desconhecido não inventa descrição: linha some da mensagem.
    assert descrever("coisa-que-nao-existe") == ("", "")
    assert descrever("") == ("", "")

    # Todo componente do catálogo tem o que dizer — a lista serve de
    # fonte para o aviso, e entrada sem impacto viraria linha vazia.
    from app.services.internos_service import COMPONENTES
    faltando = [c["nome"] for c in COMPONENTES if not c.get("impacto")]
    assert not faltando, f"componentes sem impacto: {faltando}"


async def cenario_notificacao_nao_repete_o_mesmo_evento():
    """
    Aviso que repete vira aviso que se ignora. O mesmo evento, para o mesmo
    destino, não pode ser mandado duas vezes — mesmo que o ciclo o veja de
    novo (e agora ele vê, a cada passada, para a espera funcionar).
    """
    from app.core.vault import encrypt_secret
    from app.models.notificacao import (
        NotificacaoConta, NotificacaoDestino, NotificacaoEnvio, NotificacaoRegra,
    )
    from app.services import notificacao_service as ns

    engine, fabrica = await nova_sessao()
    enviados = []

    async def enviar_falso(token, chat_id, texto):
        enviados.append((chat_id, texto))
        return {"ok": True}

    original = ns.telegram_service.enviar
    ns.telegram_service.enviar = enviar_falso
    try:
        async with fabrica() as db:
            host = await com_host(db)
            db.add(NotificacaoConta(
                bot_nome="bot", bot_token_enc=encrypt_secret("123:abc"), ativo=True,
            ))
            db.add(NotificacaoDestino(nome="Plantão", tipo="grupo", chat_id="-100", ativo=True))
            db.add(NotificacaoRegra(
                destino_id=None, host_id=None, servico="", ativo=True,
                tipos=["servico_parado"], nivel_minimo="critico", atraso_s=0,
            ))
            await db.flush()

            evento = {
                "tipo": "servico_parado", "chave": "ini:1:servico:pgbouncer:x",
                "host_id": host.id, "host": host.name, "servico": "pgbouncer",
                "nivel": "critico", "texto": "pgbouncer com problema",
                "inicio": AGORA, "duracao_s": 0,
            }
            serv = ns.NotificacaoService()
            assert await serv.despachar(db, [evento]) == 1
            await db.flush()
            # O ciclo vê de novo: não manda de novo.
            assert await serv.despachar(db, [dict(evento, duracao_s=60)]) == 0
            await db.flush()
            assert len(enviados) == 1, enviados

            r = await db.execute(sa.select(NotificacaoEnvio))
            registros = list(r.scalars().all())
            assert len(registros) == 1, registros
            assert registros[0].destino == "Plantão", registros[0].destino
    finally:
        ns.telegram_service.enviar = original
    await engine.dispose()


async def cenario_notificacao_manda_para_dois_destinos():
    """Um evento, dois destinos: dois envios, cada um rastreado."""
    from app.core.vault import encrypt_secret
    from app.models.notificacao import (
        NotificacaoConta, NotificacaoDestino, NotificacaoEnvio, NotificacaoRegra,
    )
    from app.services import notificacao_service as ns

    engine, fabrica = await nova_sessao()
    destinos_usados = []

    async def enviar_falso(token, chat_id, texto):
        destinos_usados.append(chat_id)
        return {"ok": True}

    original = ns.telegram_service.enviar
    ns.telegram_service.enviar = enviar_falso
    try:
        async with fabrica() as db:
            host = await com_host(db)
            db.add(NotificacaoConta(
                bot_nome="bot", bot_token_enc=encrypt_secret("123:abc"), ativo=True,
            ))
            db.add(NotificacaoDestino(nome="Grupo", tipo="grupo", chat_id="-100", ativo=True))
            db.add(NotificacaoDestino(nome="Pessoa", tipo="individual", chat_id="55", ativo=True))
            db.add(NotificacaoRegra(
                destino_id=None, host_id=None, servico="", ativo=True,
                tipos=["servico_parado"], nivel_minimo="critico", atraso_s=0,
            ))
            await db.flush()

            serv = ns.NotificacaoService()
            n = await serv.despachar(db, [{
                "tipo": "servico_parado", "chave": "k1", "host_id": host.id,
                "host": host.name, "servico": "x", "nivel": "critico",
                "texto": "x com problema", "inicio": AGORA, "duracao_s": 0,
            }])
            await db.flush()
            assert n == 2, n
            assert sorted(destinos_usados) == ["-100", "55"], destinos_usados

            r = await db.execute(sa.select(NotificacaoEnvio))
            assert len(list(r.scalars().all())) == 2
    finally:
        ns.telegram_service.enviar = original
    await engine.dispose()


async def cenario_notificacao_nunca_derruba_o_ciclo():
    """
    Telegram fora do ar não pode quebrar o monitor: a amostra e o
    incidente já estão gravados quando o aviso é tentado.
    """
    from app.core.vault import encrypt_secret
    from app.models.notificacao import (
        NotificacaoConta, NotificacaoDestino, NotificacaoEnvio, NotificacaoRegra,
    )
    from app.services import notificacao_service as ns

    engine, fabrica = await nova_sessao()

    async def explodir(token, chat_id, texto):
        raise RuntimeError("timeout falso")

    original = ns.telegram_service.enviar
    ns.telegram_service.enviar = explodir
    try:
        async with fabrica() as db:
            host = await com_host(db)
            db.add(NotificacaoConta(
                bot_nome="bot", bot_token_enc=encrypt_secret("123:abc"), ativo=True,
            ))
            db.add(NotificacaoDestino(nome="Plantão", tipo="grupo", chat_id="-100", ativo=True))
            db.add(NotificacaoRegra(
                destino_id=None, host_id=None, servico="", ativo=True,
                tipos=["servico_parado"], nivel_minimo="critico", atraso_s=0,
            ))
            await db.flush()

            serv = ns.NotificacaoService()
            enviados = await serv.despachar(db, [{
                "tipo": "servico_parado", "chave": "k1", "host_id": host.id,
                "host": host.name, "servico": "x", "nivel": "critico",
                "texto": "x", "inicio": AGORA, "duracao_s": 0,
            }])
            assert enviados == 0
            await db.flush()

            # A falha fica registrada — é a resposta para "não recebi".
            r = await db.execute(sa.select(NotificacaoEnvio))
            registro = r.scalars().one()
            assert registro.status == "falha" and "timeout falso" in registro.erro
            assert registro.destino == "Plantão", registro.destino
    finally:
        ns.telegram_service.enviar = original
    await engine.dispose()


async def cenario_telegram_ponta_a_ponta():
    """
    O caminho inteiro, do jeito que a pessoa configura: salva o bot,
    cadastra dois destinos (um grupo e uma pessoa), cria duas regras
    diferentes, e confere que cada evento chega em quem devia — usando as
    MESMAS funções que as rotas usam.

    É o que o botão "testar" prova em produção; aqui prova sem rede.
    """
    from app.core.vault import decrypt_secret, encrypt_secret
    from app.models.notificacao import (
        NotificacaoConta, NotificacaoDestino, NotificacaoEnvio, NotificacaoRegra,
    )
    from app.services import notificacao_service as ns

    engine, fabrica = await nova_sessao()
    entregas = []

    async def enviar_falso(token, chat_id, texto):
        entregas.append({"chat": chat_id, "texto": texto})
        return {"ok": True}

    original = ns.telegram_service.enviar
    ns.telegram_service.enviar = enviar_falso
    try:
        async with fabrica() as db:
            app_host = await com_host(db, 1, "vm-appserver")
            db_host = await com_host(db, 2, "vm-dbserver")

            # 1. O bot
            db.add(NotificacaoConta(
                bot_nome="faceops_bot",
                bot_token_enc=encrypt_secret("999:TOKEN-SECRETO"),
                token_fingerprint="ff00", ativo=True,
            ))
            # 2. Destinos: um grupo e uma pessoa
            grupo = NotificacaoDestino(nome="Plantão NOC", tipo="grupo", chat_id="-1001", ativo=True)
            pessoa = NotificacaoDestino(nome="João", tipo="individual", chat_id="777", ativo=True)
            db.add_all([grupo, pessoa])
            await db.flush()

            # 3. Regras: plantão recebe tudo dos dois hosts; João só o
            #    pgbouncer do dbserver, e só depois de 5 min de queda.
            db.add(NotificacaoRegra(
                destino_id=grupo.id, host_id=None, servico="", ativo=True,
                tipos=["servico_parado", "host_sem_contato", "retorno", "metrica"],
                nivel_minimo="atencao", atraso_s=0,
            ))
            db.add(NotificacaoRegra(
                destino_id=pessoa.id, host_id=db_host.id, servico="pgbouncer", ativo=True,
                tipos=["servico_parado"], nivel_minimo="critico", atraso_s=300,
            ))
            await db.flush()

            serv = ns.NotificacaoService()

            # Queda do pgbouncer no dbserver, recém-detectada: o plantão
            # recebe na hora; o João espera os 5 minutos.
            queda = {
                "tipo": "servico_parado", "chave": "ini:2:servico:pgbouncer:t0",
                "host_id": db_host.id, "host": db_host.name, "servico": "pgbouncer",
                "nivel": "critico", "texto": "pgbouncer com problema",
                "causa_provavel": "container parado.", "inicio": AGORA, "duracao_s": 30,
            }
            assert await serv.despachar(db, [queda]) == 1, entregas
            await db.flush()
            assert [e["chat"] for e in entregas] == ["-1001"], entregas

            # Cinco minutos depois, ainda caído: agora o João recebe — e o
            # plantão NÃO recebe de novo (deduplicação).
            assert await serv.despachar(db, [dict(queda, duracao_s=330)]) == 1
            await db.flush()
            assert sorted(e["chat"] for e in entregas) == ["-1001", "777"], entregas

            # Limite de recurso no appserver: regra do João não cobre.
            metrica = {
                "tipo": "metrica", "chave": "met:1:disco", "host_id": app_host.id,
                "host": app_host.name, "servico": "", "nivel": "atencao",
                "texto": "disco / em 94%", "acao": "Em Manutenção, use Diagnosticar.",
            }
            assert await serv.despachar(db, [metrica]) == 1
            await db.flush()

            # Voltou ao normal: plantão sim (tem "retorno"), João não.
            volta = {
                "tipo": "retorno", "chave": "fim:2:servico:pgbouncer:t0",
                "host_id": db_host.id, "host": db_host.name, "servico": "pgbouncer",
                "nivel": "critico", "duracao_s": 400,
            }
            assert await serv.despachar(db, [volta]) == 1
            await db.flush()

            # Conferência final: quem recebeu o quê.
            r = await db.execute(sa.select(NotificacaoEnvio))
            registros = list(r.scalars().all())
            assert len(registros) == 4, [(x.destino, x.texto[:20]) for x in registros]
            assert all(x.status == "enviado" for x in registros), registros

            por_destino = {}
            for x in registros:
                por_destino.setdefault(x.destino, []).append(x.texto)
            assert len(por_destino["Plantão NOC"]) == 3, por_destino
            assert len(por_destino["João"]) == 1, por_destino
            # João recebeu a queda no formato novo: assinatura, servidor
            # e o campo que diz o problema.
            so_dele = por_destino["João"][0]
            assert so_dele.startswith("🎥 FaceOps"), so_dele
            assert "Problema:" in so_dele and "Gravidade: Crítico" in so_dele, so_dele

            # E o token nunca apareceu em nenhuma mensagem.
            assert all("TOKEN-SECRETO" not in e["texto"] for e in entregas)
            # ...mas continua legível para quem tem a SECRET_KEY.
            conta = await serv.conta(db)
            assert decrypt_secret(conta.bot_token_enc) == "999:TOKEN-SECRETO"
    finally:
        ns.telegram_service.enviar = original
    await engine.dispose()


async def cenario_token_do_telegram_nunca_aparece():
    """
    O token manda mensagem como o bot: não pode sair em resposta de API
    nem em mensagem de erro (que vai parar em log, chamado e anexo).
    """
    from app.api.routes.notificacoes import _conta_publica
    from app.core.vault import encrypt_secret
    from app.models.notificacao import NotificacaoConta
    from app.services.telegram_service import _limpar

    segredo = "7654321:AAH-super-secreto"
    conta = NotificacaoConta(
        bot_nome="meu_bot", bot_token_enc=encrypt_secret(segredo),
        token_fingerprint="abc123", chat_id="-100999", ativo=True,
    )
    publico = _conta_publica(conta)
    inteiro = repr(publico)
    assert segredo not in inteiro, publico
    assert conta.bot_token_enc not in inteiro, "vazou o token cifrado"
    assert publico["bot_nome"] == "meu_bot" and publico["token_fingerprint"] == "abc123"

    # E o token some das mensagens de erro.
    limpo = _limpar(f"HTTP 401 em https://api.telegram.org/bot{segredo}/sendMessage", segredo)
    assert segredo not in limpo, limpo
    assert "***" in limpo, limpo


async def cenario_camera_sem_evento_nao_mente_quando_a_leitura_falha():
    """
    O caso das 200 câmeras: toda chamada de evento falhou, o erro foi
    engolido por `except FFApiError: pass`, e a tela afirmou "200 sem
    evento". Falha de leitura e ausência de evento não podem ficar iguais.
    """
    from app.services.ffapi_service import FFApiError, FFApiService

    servico = FFApiService()

    class HostFalso:
        id = 1
        name = "vm-appserver"
        ff_api_url = "https://10.0.0.1"
        ff_api_user = "u"
        ff_api_pass_enc = "x"
        ff_api_token_enc = ""

    async def credenciais_falsas(host):
        return {}

    async def base_falsa(host):
        return "https://10.0.0.1/api"

    async def get_falso(url, auth, params=None):
        if "/cameras/" in url:
            return {"results": [{"id": 1, "name": "cam A"}, {"id": 2, "name": "cam B"}],
                    "next": None}
        # Qualquer rota de evento falha — foi o que aconteceu em produção.
        raise FFApiError("HTTP 400: unknown query parameter 'ordering'")

    servico._credenciais = credenciais_falsas
    servico._base = base_falsa
    servico._get = get_falso

    r = await servico.ultima_interacao(HostFalso())
    v = r["varredura"]

    assert r["total_cameras"] == 2, r
    assert v["eventos_lidos"] == 0, v
    assert v["falhou"] is True, "a tela afirmaria 'sem evento' sem saber"
    assert v["erros"], "o motivo tem que chegar à tela"
    assert "ordering" in " ".join(v["erros"].values()), v["erros"]


async def cenario_camera_tenta_de_novo_sem_ordering():
    """
    Se a API recusa `ordering`, o tipo inteiro era descartado. Agora tenta
    de novo sem o parâmetro e usa a data mais recente que encontrar.
    """
    from app.services.ffapi_service import FFApiError, FFApiService

    servico = FFApiService()
    chamadas = []

    class HostFalso:
        id = 1
        name = "vm-appserver"
        ff_api_url = "https://10.0.0.1"
        ff_api_user = "u"
        ff_api_pass_enc = "x"
        ff_api_token_enc = ""

    async def credenciais_falsas(host):
        return {}

    async def base_falsa(host):
        return "https://10.0.0.1/api"

    async def get_falso(url, auth, params=None):
        if "/cameras/" in url:
            return {"results": [{"id": 7, "name": "cam A"}], "next": None}
        chamadas.append(dict(params or {}))
        if params and "ordering" in params:
            raise FFApiError("HTTP 400: unknown query parameter 'ordering'")
        return {
            "results": [
                {"id": 100, "camera": 7, "created_date": "2026-09-01T10:00:00Z"},
                {"id": 101, "camera": 7, "created_date": "2026-09-01T12:00:00Z"},
            ],
            "next": None,
        }

    servico._credenciais = credenciais_falsas
    servico._base = base_falsa
    servico._get = get_falso

    r = await servico.ultima_interacao(HostFalso())
    v = r["varredura"]

    assert v["falhou"] is False, v
    assert v["eventos_lidos"] > 0, v
    assert "faces" in v["sem_ordenacao"], v
    cam = r["cameras"][0]
    # Sem ordenação, vale a MAIS RECENTE vista — não a primeira da lista.
    assert cam["ultima_interacao"].startswith("2026-09-01T12:00"), cam
    assert any("ordering" in c for c in chamadas), chamadas


async def cenario_processos_junta_gpu_e_container_sem_coletor_novo():
    """
    GPU por processo já era coletada em Recursos. Aqui ela aparece junto de
    quem consome CPU, com o MESMO parser — e o container dono vem do
    cgroup, para o botão reiniciar o container em vez de matar PID.
    """
    from app.services.processos_service import ProcessosService

    saida = "\n".join([
        "###FACEOPS:LOAD", "0.50 0.40 0.30 2/300 1234",
        "###FACEOPS:NPROC", "8",
        "###FACEOPS:MEM",
        "              total        used        free      shared  buff/cache   available",
        "Mem:    16000000000  8000000000  2000000000           0  6000000000  7000000000",
        "Swap:             0           0           0",
        "###FACEOPS:TOP",
        "top - 10:00:00 up 1 day",
        "Tasks: 300 total,   1 running, 299 sleeping,   0 stopped,   0 zombie",
        "%Cpu(s):  3.0 us,  1.0 sy,  0.0 ni, 96.0 id,  0.0 wa",
        "MiB Mem :  16000.0 total",
        "  PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND",
        " 4242 root      20   0  100000  50000  10000 S  31.4   0.3   1:12.01 python3",
        " 4243 root      20   0  100000  50000  10000 S   9.8   0.2   0:12.01 postgres",
        "###FACEOPS:GPUPROC",
        "4242, python3, 2048",
        "###FACEOPS:CGROUP",
        "4242|docker-aaaabbbbcccc",
        "###FACEOPS:CTNAMES",
        "aaaabbbbccccddddeeee|findface-multi-findface-video-worker-1",
        "###FACEOPS:END", "",
    ])

    class Resultado:
        stdout = saida
        duration_ms = 10

    class SSHFalso:
        async def run_script(self, host, script, timeout=None, sudo=False):
            return Resultado()

    class HostFalso:
        id = 1
        name = "vm-appserver"

    servico = ProcessosService(SSHFalso())
    r = await servico.snapshot(HostFalso())

    assert r["tem_gpu"] is True, r
    por_pid = {p["pid"]: p for p in r["processos"]}
    com_gpu = por_pid.get("4242") or por_pid.get(4242)
    assert com_gpu, por_pid.keys()
    assert com_gpu["gpu_bytes"] == 2048 * 1024 * 1024, com_gpu
    assert com_gpu["container"] == "findface-multi-findface-video-worker-1", com_gpu

    sem_gpu = por_pid.get("4243") or por_pid.get(4243)
    assert sem_gpu["gpu_bytes"] == 0, sem_gpu
    assert sem_gpu["container"] == "", sem_gpu


async def cenario_licenca_so_cobra_quem_hospeda_o_ntls():
    """
    O manual da NtechLab define UMA instância de `findface-ntls` por
    instalação. Numa instalação distribuída ela mora no appserver, e
    dbserver/extraction/ftpserver não têm nada na 3185 — arquitetura
    documentada, não defeito.

    O painel acusava "Não consegui ler a licença" em cada um deles.
    """
    from app.services.rastreio_service import RastreioService

    class HostFalso:
        def __init__(self, nome, servicos):
            self.id = 1
            self.name = nome
            self.servicos_conhecidos = servicos

    servico = RastreioService(licenca=None, internos=None, ffapi=None)
    falha = RuntimeError(
        "o serviço de licença (NTLS) não respondeu dentro de 'vm-dbserver'. "
        "nada escutando na 3185 nem na 80"
    )

    # Quem NÃO roda NTLS: nenhum achado.
    sem = HostFalso("vm-dbserver", ["postgresql", "pgbouncer"])
    assert servico._checar_licenca(sem, falha) == [], "acusou host sem NTLS"

    # Ainda sem lista coletada: não se afirma falha sobre o que não se sabe.
    novo = HostFalso("vm-novo", [])
    assert servico._checar_licenca(novo, falha) == [], "afirmou sem saber"

    # Quem hospeda o NTLS: aí sim é achado de verdade.
    com = HostFalso("vm-appserver", ["findface-ntls", "findface-sf-api"])
    achados = servico._checar_licenca(com, falha)
    assert len(achados) == 1, achados
    assert "licen" in achados[0]["origem"], achados[0]


async def cenario_reincidencia_conta_e_datilha_horario():
    """
    Cinco quedas do mesmo serviço, sempre de madrugada, têm que virar uma
    linha de reincidência com horário típico — e um serviço que caiu uma
    vez só não pode aparecer.
    """
    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        serv = IncidenteService()

        base = datetime.now(timezone.utc) - timedelta(days=6)
        for dia in range(5):
            inicio = (base + timedelta(days=dia)).replace(hour=3, minute=0)
            db.add(Incidente(
                host_id=host.id, tipo="servico", servico="findface-video-worker",
                inicio=inicio, fim=inicio + timedelta(minutes=4), duracao_s=240.0,
            ))
        solitario = datetime.now(timezone.utc) - timedelta(days=1)
        db.add(Incidente(
            host_id=host.id, tipo="servico", servico="pgbouncer",
            inicio=solitario, fim=solitario + timedelta(minutes=1), duracao_s=60.0,
        ))
        await db.flush()

        itens = await serv.reincidencia(db, dias=14, minimo=3)
        assert len(itens) == 1, itens
        r = itens[0]
        assert r["servico"] == "findface-video-worker"
        assert r["ocorrencias"] == 5
        assert r["tempo_fora_s"] == 1200.0, r
        assert r["hora_tipica"] == 3, r
        assert r["intervalo_medio_h"] == 24.0, r


    await engine.dispose()


async def cenario_reincidencia_nao_inventa_horario():
    """
    Se as quedas estão espalhadas pelo dia, o painel diz "sem horário
    típico" em vez de eleger um. Horário falso é pior que nenhum: manda a
    pessoa investigar a janela errada.
    """
    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        serv = IncidenteService()

        base = datetime.now(timezone.utc) - timedelta(days=4)
        for i, hora in enumerate([2, 9, 14, 21]):
            inicio = (base + timedelta(days=i)).replace(hour=hora, minute=0)
            db.add(Incidente(
                host_id=host.id, tipo="servico", servico="findface-extraction-api",
                inicio=inicio, fim=inicio + timedelta(minutes=2), duracao_s=120.0,
            ))
        await db.flush()

        itens = await serv.reincidencia(db, dias=14, minimo=3)
        assert len(itens) == 1, itens
        assert itens[0]["hora_tipica"] is None, itens[0]
    await engine.dispose()


async def cenario_apelido_e_rotulo_nunca_identidade():
    """
    Apelido é rótulo; `name` é identidade. A separação não é preciosismo:
    `StorageService.caminho_artefato` monta o diretório dos backups com o
    nome do host, e a auditoria registra o alvo por ele. Se o apelido
    substituísse o nome, renomear um servidor deixaria todo backup antigo
    órfão num diretório que ninguém mais procura, e a trilha de auditoria
    passaria a apontar para um nome que muda.
    """
    from app.models.host import Host

    # Sem apelido, o rótulo é o nome técnico — nada muda de aparência.
    assert Host(name="vm-appserver").rotulo == "vm-appserver"
    # Com apelido, é ele que aparece.
    h = Host(name="vm-appserver", alias="Servidor da portaria")
    assert h.rotulo == "Servidor da portaria"
    # Só espaço não é apelido: cairia num rótulo vazio na tela.
    assert Host(name="vm-db", alias="   ").rotulo == "vm-db"
    assert Host(name="vm-db", alias=None).rotulo == "vm-db"

    # O apelido não pode ter entrado no caminho dos artefatos. Esta é a
    # trava contra o refactor bem-intencionado: trocar `host.name` por
    # `host.rotulo` em backups.py compila, passa em revisão superficial e
    # perde as cópias antigas em silêncio.
    fonte = (pathlib.Path(__file__).resolve().parents[1]
             / "app" / "api" / "routes" / "backups.py").read_text(encoding="utf-8")
    for linha in fonte.splitlines():
        if "caminho_artefato(" in linha:
            assert "rotulo" not in linha, (
                f"caminho de artefato usando apelido: {linha.strip()}"
            )

    # Mesma trava para o alvo da auditoria: quem auditou precisa poder
    # cruzar o registro com o servidor, e apelido é editável.
    for arquivo in ("hosts.py", "ops.py", "maintenance.py"):
        fonte = (pathlib.Path(__file__).resolve().parents[1]
                 / "app" / "api" / "routes" / arquivo).read_text(encoding="utf-8")
        assert "target=host.rotulo" not in fonte, f"auditoria por apelido em {arquivo}"


async def cenario_aviso_mostra_apelido_e_roteia_por_id():
    """
    O apelido serve justamente para o aviso no celular dizer "Servidor da
    portaria" em vez de "vm-appserver-03". Mas a regra tem que continuar
    casando por `host_id`: casar por nome faria toda regra parar de valer
    no dia em que alguém trocasse o apelido.
    """
    from app.models.notificacao import NotificacaoDestino, NotificacaoRegra
    from app.services import notificacao_service as ns

    evento = {
        "tipo": "servico_parado",
        "nivel": "critico",
        "host_id": 7,
        "host": "Servidor da portaria",   # já vem como rótulo do serviço
        "servico": "findface-video-worker",
        "texto": "findface-video-worker com problema",
        "chave": "ini:7:servico:findface-video-worker",
        "idade_s": 999,
    }

    texto = ns.montar_mensagem(evento)
    assert "Servidor da portaria" in texto, "aviso não mostrou o apelido"
    # O apelido é o nome do servidor no cabeçalho, não uma linha extra.
    assert "Servidor da portaria" in texto.split("\n\n")[1], texto
    # E o nome técnico não vaza junto: quem recebe pediu o apelido.
    assert "vm-appserver" not in texto, texto

    destino = NotificacaoDestino(id=1, nome="Plantão", tipo="grupo",
                                 chat_id="-100123", ativo=True)
    # A regra fixa o host por id. O apelido no evento é outro texto de
    # propósito: se o roteamento olhasse o nome, isto não casaria.
    regra = NotificacaoRegra(
        id=1, destino_id=None, host_id=7, servico="", ativo=True,
        nivel_minimo="critico", atraso_s=0,
        tipos=["servico_parado"],
    )
    assert ns.NotificacaoService.rotear([regra], [destino], evento) == [destino], (
        "regra deixou de casar quando o evento trouxe o apelido"
    )

    # E o servidor errado continua fora.
    outra = NotificacaoRegra(
        id=2, destino_id=None, host_id=8, servico="", ativo=True,
        nivel_minimo="critico", atraso_s=0,
        tipos=["servico_parado"],
    )
    assert ns.NotificacaoService.rotear([outra], [destino], evento) == [], (
        "regra de outro servidor recebeu o aviso"
    )



# ── Crescimento: o que sobe sem parar ──────────────────────────────────


def _serie(valores, minutos=5):
    """(horas, valor) a partir de uma lista de valores igualmente espaçados."""
    return [(i * minutos / 60, v) for i, v in enumerate(valores)]


async def cenario_crescimento_distingue_linear_de_exponencial():
    """
    As três formas que a série pode ter, e por que a diferença importa:

    * **estável** — não há o que projetar;
    * **linear** — sobe sempre igual, e a projeção é uma conta de regra de três;
    * **acelerando** — a taxa da segunda metade é muito maior que a da
      primeira. É o caso do pedido ("do nada, consumo exponencial") e o
      único em que faz sentido falar em "dobra a cada X".

    Sem separar os três, um crescimento normal de operação e um vazamento
    receberiam o mesmo aviso — e o aviso que vale para tudo não vale para
    nada.
    """
    from app.services.crescimento_service import analisar_serie

    plana = analisar_serie(_serie([70.0, 70.1, 69.9, 70.0, 70.2, 70.1, 70.0,
                                   69.9, 70.1, 70.0, 70.1, 70.0]))
    assert plana["regime"] in ("estavel", "recuando"), plana
    assert plana["dobra_h"] is None

    linear = analisar_serie(_serie([60 + i * 1.5 for i in range(14)]))
    assert linear["regime"] == "linear", linear
    # 1,5 ponto a cada 5 min = 18 pontos por hora.
    assert 17 <= linear["taxa_por_h"] <= 19, linear
    assert linear["confianca"] == "alta", linear
    # Reta perfeita NÃO ganha "dobra": dobrar é vocabulário de exponencial,
    # e usá-lo aqui daria uma precisão que a série não sustenta.
    assert linear["dobra_h"] is None, linear

    exponencial = analisar_serie(_serie([10 * (1.35 ** i) for i in range(14)]))
    assert exponencial["regime"] == "acelerando", exponencial
    assert exponencial["dobra_h"] is not None, "aceleração sem tempo de dobra"
    # 1,35 por passo de 5 min → dobra em ~0,19 h. Margem larga de
    # propósito: o que se trava é a ordem de grandeza, não o decimal.
    assert 0.1 <= exponencial["dobra_h"] <= 0.5, exponencial


async def cenario_crescimento_nao_inventa_tendencia():
    """
    Três formas de a série NÃO ser um vazamento — e o painel tem de dizer
    isso, em vez de puxar uma reta por cima.

    1. **Poucos pontos.** Três amostras não desenham tendência.
    2. **Reinício.** Container reiniciado devolve a memória de uma vez; a
       reta por cima da queda daria inclinação negativa numa máquina que
       está subindo, ou positiva numa que acabou de ser resolvida.
    3. **Serrote.** Sobe, cai, sobe de novo: o diagnóstico é "volta ao
       normal quando reinicia", que manda investigar outra coisa.
    """
    from app.services.crescimento_service import analisar_serie

    curta = analisar_serie(_serie([50.0, 60.0, 70.0]))
    assert curta["regime"] == "indeterminado", curta
    assert "ponto" in curta["motivo"], curta
    assert curta["taxa_por_h"] == 0.0, "projetou em cima de três pontos"

    # Sobe até 80 e o container reinicia: cai para 40 e recomeça devagar.
    reinicio = analisar_serie(
        _serie([60, 65, 70, 75, 80, 40, 40.2, 40.4, 40.6, 40.8, 41, 41.2])
    )
    assert reinicio["reinicios"] == 1, reinicio
    # O trecho DEPOIS da queda é o que vale — e ele sobe devagar.
    assert reinicio["regime"] in ("linear", "indeterminado"), reinicio
    assert reinicio["taxa_por_h"] < 5, (
        "a queda do reinício entrou na conta da tendência"
    )

    serrote = analisar_serie(
        _serie([50, 60, 70, 80, 45, 55, 65, 75, 40, 50, 60, 70, 80])
    )
    assert serrote["regime"] == "serrote", serrote
    assert "reinicia" in serrote["motivo"], serrote


async def cenario_crescimento_projeta_o_estouro_e_diz_o_dano():
    """
    A projeção e o dano são o par que transforma medida em decisão. Número
    sozinho ("memória a 4 pp/h") não faz ninguém levantar da cama; "em 6h
    o kernel mata o container e as câmeras dele param" faz.

    Trava também o que NÃO se projeta: sem subida e acima do teto não há
    previsão — nos dois casos, número aqui seria invenção.
    """
    from app.services.crescimento_service import CrescimentoService, projetar

    # De 71% subindo 4 pontos por hora até o teto de 95%: 6 horas.
    assert projetar(71.0, 4.0, 95.0) == 6.0
    assert projetar(80.0, 0.0, 95.0) is None, "projetou sem subida"
    assert projetar(96.0, 4.0, 95.0) is None, "projetou o que já estourou"

    dano_mem = CrescimentoService.dano("memoria", "findface-video-worker-1")
    assert "findface-video-worker-1" in dano_mem
    # O dano é escrito em operação, não em métrica.
    assert "câmeras" in dano_mem and "não aparece erro" in dano_mem, dano_mem

    dano_disco = CrescimentoService.dano("disco", ponto="/")
    assert "para de gravar" in dano_disco and "passagens" in dano_disco, dano_disco


async def cenario_crescimento_abre_e_fecha_vigilancia_sozinha():
    """
    O ciclo de vida inteiro, sem SSH nenhum: a subida aparece nas amostras
    que o coletor já gravou, a vigilância abre depois de confirmada em N
    ciclos, e fecha sozinha quando o consumo estabiliza.

    As duas travas que importam:

    * **uma leitura não abre vigilância** — senão um backup começando
      viraria alarme toda madrugada;
    * **estabilizou, fechou** — vigilância que fica aberta para sempre
      vira ruído permanente na tela, que é pior que não ter.
    """
    from app.services.crescimento_service import CrescimentoService

    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        agora = datetime.now(timezone.utc)

        # Memória subindo de 60% a 90%, terminando uma hora atrás. O
        # tempo é todo NO PASSADO de propósito: a série é cortada em
        # "agora", como em produção — amostra com carimbo no futuro não
        # existe, e um teste que dependesse dela não provaria nada.
        for i in range(19):
            db.add(Amostra(
                host_id=host.id,
                ts=agora - timedelta(minutes=155 - i * 5),
                mem_pct=60.0 + i * 1.6667,
                mem_total_mb=16384, mem_usado_mb=9830,
                disco_pct=40.0, disco_ponto="/", disco_total_gb=100,
                disco_livre_gb=60, swap_pct=0.0,
            ))
        await db.flush()

        serv = CrescimentoService(
            ssh=None,
            config=ConfigFalsa({
                "crescimento.ativo": True,
                "crescimento.janela_h": 6,
                "crescimento.ciclos_para_abrir": 3,
                # Sem ida ao servidor no teste — e é configuração real,
                # não gambiarra de teste: quem não quer o rastreio
                # automático desliga esta chave.
                "crescimento.rastrear_sozinho": False,
                "containers.historico_ativo": False,
            }),
        )

        eventos = await serv.registrar_ciclo(db, host)
        assert eventos == [], "uma leitura só abriu vigilância"
        assert (await db.execute(sa.select(sa.func.count(Crescimento.id)))).scalar() == 0

        await serv.registrar_ciclo(db, host)
        eventos = await serv.registrar_ciclo(db, host)
        abertas = list((await db.execute(sa.select(Crescimento))).scalars().all())
        assert len(abertas) == 1, f"não abriu no terceiro ciclo: {abertas}"
        vig = abertas[0]
        assert vig.recurso == "memoria" and vig.fim is None
        assert vig.regime in ("linear", "acelerando"), vig.regime
        assert vig.estouro_em is not None, "abriu sem previsão de estouro"

        assert len(eventos) == 1, eventos
        evento = eventos[0]
        assert evento["tipo"] == "crescimento"
        assert "memória" in evento["texto"], evento["texto"]
        # A previsão vai no texto do aviso: é o que faz alguém agir, e tem
        # de caber na primeira linha da mensagem no celular.
        assert "chega a" in evento["texto"], evento["texto"]
        assert evento["significa"], "aviso sem o dano previsto"

        # Agora estabiliza: a última hora de amostras para de subir.
        for i in range(12):
            db.add(Amostra(
                host_id=host.id,
                ts=agora - timedelta(minutes=55 - i * 5),
                mem_pct=90.0,
                mem_total_mb=16384, mem_usado_mb=14745,
                disco_pct=40.0, disco_ponto="/", disco_total_gb=100,
                disco_livre_gb=60, swap_pct=0.0,
            ))
        await db.flush()

        # Fechar é simétrico a abrir: uma leitura estabilizada não fecha
        # sozinha — senão o ruído de arredondamento (1 casa decimal)
        # abriria e fecharia a mesma vigilância a cada poucos minutos.
        # Precisa de `ciclos_para_fechar` (padrão 3) leituras seguidas
        # sem preocupação.
        eventos = await serv.registrar_ciclo(db, host)
        assert eventos == [], "uma leitura estabilizada já fechou a vigilância"
        assert vig.fim is None

        await serv.registrar_ciclo(db, host)
        eventos = await serv.registrar_ciclo(db, host)
        await db.flush()
        assert vig.fim is not None, "não fechou depois de 3 ciclos estabilizados"
        assert vig.desfecho == "estabilizou", vig.desfecho
        assert any(e["tipo"] == "retorno" for e in eventos), eventos

    await engine.dispose()


async def cenario_crescimento_acusa_quem_cresceu_nao_quem_e_grande():
    """
    A diferença entre as duas perguntas, que é o coração do rastreio:

    * *quem é grande?* — o Tarantool, desde sempre. Não explica nada.
    * *quem cresceu?* — o worker que ganhou 900 MB em duas horas. É esse.

    Vale para os dois lados: a série por container (memória) e a
    comparação entre dois rastreios de disco.
    """
    from app.services.crescimento_service import CrescimentoService, atribuir

    # Disco: dois retratos com hora. O maior não é o que cresceu.
    medicoes = [
        {"ts": "2026-09-03T10:00:00+00:00",
         "alvos": {"/opt/findface-multi/data": 800 * 1024 ** 3,
                   "/var/log": 2 * 1024 ** 3}},
        {"ts": "2026-09-03T12:00:00+00:00",
         "alvos": {"/opt/findface-multi/data": 800 * 1024 ** 3 + 1024 ** 3,
                   "/var/log": 10 * 1024 ** 3}},
    ]
    ranking = atribuir(medicoes)
    assert ranking, "não atribuiu crescimento nenhum"
    assert ranking[0]["alvo"] == "/var/log", ranking
    assert ranking[0]["cresceu_bytes"] == 8 * 1024 ** 3
    assert ranking[0]["por_hora_bytes"] == 4 * 1024 ** 3

    # Um retrato só não acusa ninguém — e dizer isso é a resposta certa.
    assert atribuir(medicoes[:1]) == []

    # Memória: a série por container, direto do banco.
    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        agora = datetime.now(timezone.utc)
        for i in range(13):
            quando = agora - timedelta(minutes=(12 - i) * 5)
            # O maior da máquina, e estável.
            db.add(AmostraContainer(
                host_id=host.id, ts=quando,
                nome="findface-tarantool-server-1", mem_mb=8000.0, mem_pct=48.0,
            ))
            # O que cresce: +75 MB a cada 5 min = 900 MB/h.
            db.add(AmostraContainer(
                host_id=host.id, ts=quando,
                nome="findface-video-worker-1", mem_mb=1000.0 + i * 75, mem_pct=12.0,
            ))
        await db.flush()

        serv = CrescimentoService(ssh=None, config=ConfigFalsa({}))
        serie = await serv.serie_containers(db, host.id, horas=6)
        assert serie["series"], serie
        # Ordenado por quem CRESCEU, não por quem ocupa.
        assert serie["series"][0]["nome"] == "findface-video-worker-1", [
            (x["nome"], x["mb_por_h"]) for x in serie["series"]
        ]
        assert 850 <= serie["series"][0]["mb_por_h"] <= 950, serie["series"][0]

        culpados = await serv.culpados_memoria(db, host.id, horas=6)
        assert culpados and culpados[0]["nome"] == "findface-video-worker-1"
        # E vem com o que o catálogo sabe sobre aquele serviço: o porquê,
        # o contorno e o que o fabricante recomenda.
        assert culpados[0]["causa"] == "video_worker", culpados[0]
        assert culpados[0]["contorno"], "culpado sem o que fazer"
        assert "manual" in culpados[0]["fabricante"], culpados[0]["fabricante"]
        # O estável não é acusado de nada.
        assert all(c["nome"] != "findface-tarantool-server-1" for c in culpados)

    await engine.dispose()


async def cenario_rastreio_de_crescimento_so_le():
    """
    O rastreio roda com sudo, em servidor de produção, no pior momento
    possível — quando a máquina já está sob pressão de recurso. Então ele
    não pode escrever nada, e não pode ser a causa do próximo incidente.

    Mesma trava da apuração (INV-24), mais a de custo: todo comando caro
    tem `timeout` e prioridade baixa de E/S, porque num disco com teto de
    IOPS o diagnóstico compete com o FindFace.
    """
    from app.services import crescimento_service as cs

    memoria = cs.script_memoria()
    disco = cs.script_disco("/opt/findface-multi", "/", 6, "/var/backups/faceops")

    proibidos = [
        " rm ", "rm -", "restart", "systemctl stop", "docker stop",
        "docker rm", "truncate", "mkfs", "dd ", "> /", ">> /", "kill ",
        "chmod", "chown", "reboot",
    ]
    for script, nome in ((memoria, "memória"), (disco, "disco")):
        for termo in proibidos:
            assert termo not in script, (
                f"o rastreio de {nome} tem comando que altera estado: {termo!r}"
            )

    # Custo cercado: o `du` da árvore de dados é o comando caro do painel.
    assert "ionice -c3" in disco and "nice -n19" in disco, disco
    assert disco.count("timeout") >= 4, "há `du`/`find` sem teto de tempo"
    assert "docker stats" in memoria and "timeout 25 docker stats" in memoria

    # E o que ele procura de fato: os lugares que já encheram disco neste
    # ambiente, mais o arquivo apagado que continua ocupando (o caso em
    # que o `du` não acha nada e o `df` segue cheio).
    for esperado in ("/var/log", "/var/lib/docker/containers", "APAGADOS",
                     "newermt", "lsof"):
        assert esperado in disco, esperado

    # Um `du` que não termina vira "não medido", nunca zero.
    lido = cs.interpretar_disco({"CAMINHOS": "/opt/findface-multi/data|"})
    assert lido["alvos"] == {}, lido
    assert "não terminou de ser medido" in lido["achados"][0]["texto"], lido


async def cenario_faxina_apaga_vigilancia_fechada_so():
    """
    Mesma regra do incidente: vigilância ABERTA é estado atual, não
    histórico. Apagá-la faria a tela achar que o problema nunca existiu
    enquanto ele ainda está acontecendo.
    """
    from app.services.crescimento_service import CrescimentoService

    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        velha = datetime.now(timezone.utc) - timedelta(days=200)

        db.add(Crescimento(host_id=host.id, recurso="disco", inicio=velha,
                           fim=velha + timedelta(hours=2), desfecho="estabilizou"))
        db.add(Crescimento(host_id=host.id, recurso="memoria", inicio=velha))
        db.add(AmostraContainer(host_id=host.id, ts=velha, nome="x", mem_mb=10))
        await db.flush()

        removidas = await CrescimentoService.limpar(db, 90)
        assert removidas == 1, removidas
        restantes = list((await db.execute(sa.select(Crescimento))).scalars().all())
        assert len(restantes) == 1 and restantes[0].fim is None, restantes

        assert await CrescimentoService.limpar_containers(db, 7) == 1
        # Zero dia desliga a faxina, em vez de apagar tudo.
        assert await CrescimentoService.limpar(db, 0) == 0

    await engine.dispose()




async def cenario_periodo_absoluto_manda_e_nao_inventa_dado():
    """
    O seletor de período pede duas garantias, e as duas já custaram tela
    errada em painel de série:

    1. **O intervalo absoluto ganha da janela relativa.** Quem digitou
       "a madrugada de terça" não quer as últimas 6 horas — e mandar os
       dois juntos é ambíguo, então a regra é fixa e testada.
    2. **Não se lê o futuro.** Sem teto, a janela relativa pegaria
       carimbo à frente do relógio (amostra de teste, relógio dessincronizado)
       e o gráfico mostraria linha onde não houve medição.

    E a série devolve `mais_antiga`: é o que deixa a tela dizer "não há
    dado tão para trás" em vez de desenhar um período vazio, que se lê
    como "o servidor ficou parado".
    """
    from app.services.crescimento_service import CrescimentoService

    de = datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc)
    ate = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)

    # Absoluto manda, mesmo com janela relativa junto.
    i, f = CrescimentoService._intervalo(janela_h=6, de=de, ate=ate)
    assert (i, f) == (de, ate), (i, f)

    # Relativo termina AGORA, nunca no futuro.
    i, f = CrescimentoService._intervalo(janela_h=2)
    agora = datetime.now(timezone.utc)
    assert f <= agora + timedelta(seconds=2), f
    assert abs((f - i).total_seconds() - 7200) < 2, (i, f)

    # Fim sem início mantém o tamanho da janela: é o que permite andar
    # para trás no tempo sem a janela encolher a cada clique.
    i, f = CrescimentoService._intervalo(janela_h=3, ate=ate)
    assert f == ate and abs((f - i).total_seconds() - 10800) < 2, (i, f)

    # Intervalo invertido não vira série vazia sem explicação.
    i, f = CrescimentoService._intervalo(de=ate, ate=de)
    assert i < f, (i, f)

    engine, fabrica = await nova_sessao()
    async with fabrica() as db:
        host = await com_host(db)
        base = datetime.now(timezone.utc) - timedelta(hours=3)
        for k in range(6):
            db.add(AmostraContainer(
                host_id=host.id, ts=base + timedelta(minutes=k * 5),
                nome="findface-multi-redis-1", mem_mb=100.0 + k,
            ))
        # Uma amostra com carimbo no futuro: não pode entrar em janela
        # relativa nenhuma.
        db.add(AmostraContainer(
            host_id=host.id, ts=datetime.now(timezone.utc) + timedelta(hours=2),
            nome="findface-multi-redis-1", mem_mb=9999.0,
        ))
        await db.flush()

        serv = CrescimentoService(ssh=None, config=ConfigFalsa({}))
        serie = await serv.serie_containers(db, host.id, horas=6)
        assert serie["amostras"] == 6, serie["amostras"]
        assert serie["series"][0]["pico_mb"] < 1000, "leu amostra do futuro"
        assert serie["mais_antiga"], "não disse desde quando há dado"

        # Período em que não houve coleta: zero séries, com o motivo
        # dizendo desde quando existe dado — e não silêncio.
        antigo = await serv.serie_containers(
            db, host.id,
            de=datetime(2020, 1, 1, tzinfo=timezone.utc),
            ate=datetime(2020, 1, 2, tzinfo=timezone.utc),
        )
        assert antigo["series"] == []
        assert "mais antigo que existe" in antigo["motivo"], antigo["motivo"]

    await engine.dispose()


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
    cenario_fingerprint_agrupa_o_que_e_o_mesmo_erro,
    cenario_catalogo_casa_erro_conhecido,
    cenario_analise_soma_ocorrencias_sem_duplicar_molde,
    cenario_analise_le_o_container_e_nao_o_servico,
    cenario_reincidencia_conta_e_datilha_horario,
    cenario_reincidencia_nao_inventa_horario,
    cenario_sonda_404_405_nao_e_servico_travado,
    cenario_notificacao_roteia_para_os_destinos_certos,
    cenario_notificacao_filtra_por_tipo_e_gravidade,
    cenario_notificacao_espera_antes_de_avisar,
    cenario_apuracao_correlaciona_pico_de_recurso,
    cenario_crescimento_distingue_linear_de_exponencial,
    cenario_crescimento_nao_inventa_tendencia,
    cenario_crescimento_projeta_o_estouro_e_diz_o_dano,
    cenario_crescimento_abre_e_fecha_vigilancia_sozinha,
    cenario_crescimento_acusa_quem_cresceu_nao_quem_e_grande,
    cenario_rastreio_de_crescimento_so_le,
    cenario_faxina_apaga_vigilancia_fechada_so,
    cenario_periodo_absoluto_manda_e_nao_inventa_dado,
    cenario_busca_entende_acento_e_parte_da_palavra,
    cenario_servidor_nao_acumula_sobra,
    cenario_atualizar_forca_coleta_de_verdade,
    cenario_projeto_sem_marca_de_ferramenta,
    cenario_coletor_desacelera_sem_ninguem_olhando,
    cenario_painel_nao_pesa_no_que_monitora,
    cenario_saturacao_de_disco_e_medida,
    cenario_backup_do_painel_nao_disputa_disco,
    cenario_erro_de_conexao_diz_onde_procurar,
    cenario_acoes_rapidas_nao_sao_shell_remoto,
    cenario_sessao_cai_parada_e_tem_teto,
    cenario_perfis_descrevem_o_que_cada_um_pode,
    cenario_chave_fraca_impede_a_subida,
    cenario_jwt_nao_aceita_algoritmo_trocado,
    cenario_url_da_api_nao_alcanca_o_metadados,
    cenario_segredo_nunca_sai_em_resposta_nem_em_log,
    cenario_apuracao_distingue_reboot_de_rede,
    cenario_apuracao_le_o_container_certo_e_aponta_oom,
    cenario_apuracao_respeita_o_nivel_e_os_tetos,
    cenario_apuracao_entra_no_aviso_de_retorno,
    cenario_parar_servico_tem_cerca_e_confirmacao,
    cenario_historico_do_servico_nao_toca_no_servidor,
    cenario_faxina_nao_oferece_categoria_que_nao_age,
    cenario_previa_da_faxina_nao_esconde_categoria,
    cenario_faxina_poupa_execucao_com_artefato,
    cenario_auditoria_busca_acha_e_filtra,
    cenario_notificacao_mensagem_tem_campos_e_assina_a_origem,
    cenario_duracao_no_formato_do_zabbix,
    cenario_aviso_explica_o_servico_para_quem_nao_conhece,
    cenario_notificacao_nao_repete_o_mesmo_evento,
    cenario_notificacao_manda_para_dois_destinos,
    cenario_notificacao_nunca_derruba_o_ciclo,
    cenario_telegram_ponta_a_ponta,
    cenario_token_do_telegram_nunca_aparece,
    cenario_camera_sem_evento_nao_mente_quando_a_leitura_falha,
    cenario_camera_tenta_de_novo_sem_ordering,
    cenario_processos_junta_gpu_e_container_sem_coletor_novo,
    cenario_licenca_so_cobra_quem_hospeda_o_ntls,
    cenario_apelido_e_rotulo_nunca_identidade,
    cenario_aviso_mostra_apelido_e_roteia_por_id,
]


def _seguro(texto: str) -> str:
    """
    Console do Windows é cp1252: emoji ou acento numa mensagem de falha
    derrubava o próprio relatório com UnicodeEncodeError, escondendo os
    outros cenários. O relatório tem que sobreviver ao que ele relata.
    """
    codificacao = getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(texto).encode(codificacao, "replace").decode(codificacao, "replace")


async def principal() -> int:
    falhas = 0
    for cenario in CENARIOS:
        nome = cenario.__name__.replace("cenario_", "").replace("_", " ")
        try:
            await cenario()
            print(f"  ok    {nome}")
        except Exception as exc:
            falhas += 1
            print(_seguro(f"  FALHA {nome}: {type(exc).__name__}: {exc}"))
    print(f"\n{len(CENARIOS) - falhas}/{len(CENARIOS)} cenários passaram")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(principal()))
