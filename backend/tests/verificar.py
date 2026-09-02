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
from app.models.host import Host  # noqa: E402
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
    LimiarOverride.__table__, LogPadrao.__table__,
    NotificacaoConta.__table__, NotificacaoDestino.__table__,
    NotificacaoRegra.__table__, NotificacaoEnvio.__table__,
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


async def cenario_notificacao_mensagem_curta_e_sem_ip():
    """
    A mensagem tem que caber na prévia do celular — e não pode levar
    endereço interno para um grupo de Telegram. Um tipo por formato: quem
    lê precisa distinguir "parou" de "voltou" de "limite" no primeiro
    caractere.
    """
    from app.services.notificacao_service import montar_mensagem

    texto = montar_mensagem({
        "tipo": "servico_parado", "host": "vm-appserver", "servico": "findface-video-worker",
        "nivel": "critico", "texto": "findface-video-worker com problema",
        "causa_provavel": "reiniciou 7x nos últimos 30 min — sinal de câmera problemática.",
        "inicio": AGORA,
    })
    linhas = texto.splitlines()
    assert len(linhas) <= 4, texto
    assert "PARADO" in linhas[0] and "vm-appserver" in linhas[0], texto
    assert "10.0" not in texto and "192.168" not in texto, "vazou endereço interno"
    assert len(texto) <= 220, f"mensagem longa demais ({len(texto)}): {texto}"

    # A causa entra cortada na primeira frase e limitada a 140 caracteres.
    longa = montar_mensagem({
        "tipo": "servico_parado", "host": "vm-appserver", "servico": "x", "nivel": "critico",
        "texto": "x com problema", "inicio": AGORA,
        "causa_provavel": "primeira frase curta. " + ("detalhe " * 40),
    })
    assert "detalhe" not in longa, longa

    volta = montar_mensagem({
        "tipo": "retorno", "host": "vm-appserver", "servico": "findface-video-worker",
        "duracao_s": 360,
    })
    assert "NORMALIZADO" in volta and "6min" in volta, volta

    # Cada tipo tem cabeçalho próprio.
    sem_contato = montar_mensagem({
        "tipo": "host_sem_contato", "host": "vm-dbserver", "servico": "",
        "nivel": "critico", "inicio": AGORA,
        "causa_provavel": "rede fora, VM desligada ou parada.",
    })
    assert "SEM CONTATO" in sem_contato, sem_contato

    limite = montar_mensagem({
        "tipo": "metrica", "host": "vm-appserver", "servico": "", "nivel": "atencao",
        "texto": "disco / em 94% — só 6 GB livres",
        "acao": "Em Manutenção, use Diagnosticar. Outra frase que não deve entrar.",
    })
    assert "LIMITE" in limite and "94%" in limite, limite
    assert "Outra frase" not in limite, limite


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
            assert "PARADO" in por_destino["João"][0], por_destino["João"]

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
    assert len(texto.splitlines()) <= 4, "aviso passou de quatro linhas"

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
    cenario_notificacao_mensagem_curta_e_sem_ip,
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
