"""
Rastreio — o que está quebrado, com prova, impacto e o que fazer.

O painel já mostrava sintomas espalhados: disco numa tela, licença noutra,
container numa terceira. Faltava a pergunta que quem opera faz de verdade:
**"tem algo comprometendo o funcionamento agora?"**

Este serviço responde com achados, não com telemetria. Cada achado carrega
quatro coisas — e a ausência de qualquer uma delas foi motivo para não
incluir a checagem:

* **evidência**: o número ou a mensagem que o servidor devolveu. Sem isso é
  opinião;
* **impacto**: o que para de funcionar, em termos de operação;
* **ação**: onde clicar ou o que rodar. Achado sem ação vira ansiedade;
* **origem**: licença, API, componente, disco, backup ou segurança.

Regras que valem para tudo aqui:

1. **Só leitura.** Nenhuma checagem muda estado, reinicia serviço ou apaga
   nada. Diagnóstico que age sozinho é como alarme de incêndio que abre a
   janela.
2. **Nada de inferir falha de dado ausente.** Servidor que não respondeu
   gera um achado dizendo *isso*, e não um achado sobre o que ele teria.
3. **Sob demanda.** Duas execuções SSH por servidor (componentes e
   licença). Não entra em laço de fundo — o painel promete não pesar.
4. **Severidade do fabricante quando ele define.** Licença a vencer usa os
   60 dias da própria interface do FindFace; ocupação de recurso usa 90% e
   80%, que é o que a interface dele considera erro e aviso.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models.amostra import Amostra
from app.models.backup import BackupRun, Schedule
from app.models.destino import Destino
from app.models.host import Host
from app.models.user import User

log = logging.getLogger("faceops.rastreio")

CRITICO = "critico"
ATENCAO = "atencao"
INFO = "info"

ORDEM = {CRITICO: 0, ATENCAO: 1, INFO: 2}


def _achado(
    nivel: str,
    origem: str,
    titulo: str,
    evidencia: str,
    impacto: str,
    acao: str,
    servidor: str = "",
    manual: str = "",
) -> dict:
    return {
        "nivel": nivel,
        "origem": origem,
        "titulo": titulo,
        "evidencia": evidencia,
        "impacto": impacto,
        "acao": acao,
        "servidor": servidor,
        "manual": manual,
    }


class RastreioService:
    def __init__(self, licenca, internos, ffapi, config=None) -> None:
        self.licenca = licenca
        self.internos = internos
        self.ffapi = ffapi
        self.config = config

    # ── Orquestração ───────────────────────────────────────────────────

    async def rodar(self, db, host_id: int | None = None) -> dict:
        consulta = select(Host).where(Host.enabled.is_(True))
        if host_id is not None:
            consulta = consulta.where(Host.id == host_id)
        hosts = list((await db.execute(consulta)).scalars().all())

        achados: list[dict] = []

        # Checagens do painel: banco local, sem tocar em servidor.
        achados += await self._painel(db)

        # As leituras REMOTAS vão em paralelo — cada host é uma máquina
        # diferente e não há razão para esperar em fila. As checagens que
        # tocam o banco ficam de fora do paralelo, de propósito: a sessão
        # do SQLAlchemy é uma só e não suporta uso concorrente. Rodar as
        # duas coisas juntas dava erro intermitente de "outra operação em
        # andamento" — o pior tipo, o que só aparece com mais de um host.
        leituras = await asyncio.gather(
            *(
                asyncio.gather(
                    self._ler_licenca(h), self._ler_internos(h), return_exceptions=True
                )
                for h in hosts
            ),
            return_exceptions=True,
        )

        for host, par in zip(hosts, leituras):
            if isinstance(par, Exception):
                achados.append(
                    _achado(
                        ATENCAO,
                        "painel",
                        "O rastreio não conseguiu concluir neste servidor",
                        f"{type(par).__name__}: {par}"[:300],
                        "Sem leitura, não há como afirmar que está saudável — "
                        "o silêncio aqui não é boa notícia.",
                        "Confira a conexão em Servidores → Testar conexão.",
                        servidor=host.name,
                    )
                )
                continue

            licenca, internos = par
            achados += self._checar_licenca(host, licenca)
            achados += self._checar_internos(host, internos)
            achados += self._checar_api(host)
            # Sequencial: banco.
            achados += await self._checar_monitor(db, host)

        achados.sort(key=lambda a: (ORDEM.get(a["nivel"], 9), a["origem"], a["titulo"]))

        return {
            "em": datetime.now(timezone.utc).isoformat(),
            "servidores": [h.name for h in hosts],
            "criticos": sum(1 for a in achados if a["nivel"] == CRITICO),
            "atencao": sum(1 for a in achados if a["nivel"] == ATENCAO),
            "info": sum(1 for a in achados if a["nivel"] == INFO),
            "achados": achados,
        }

    # ── Painel ─────────────────────────────────────────────────────────

    async def _painel(self, db) -> list[dict]:
        achados: list[dict] = []

        # 1. Senha de fábrica ainda em uso.
        senhas = (
            await db.execute(
                select(func.count(User.id)).where(User.senha_padrao.is_(True))
            )
        ).scalar() or 0
        if senhas:
            achados.append(
                _achado(
                    CRITICO,
                    "segurança",
                    "Conta com senha de fábrica",
                    f"{senhas} usuário(s) do painel ainda com a senha inicial",
                    "Quem alcança a rede entra no painel — e o painel tem SSH "
                    "com sudo nos servidores do FindFace.",
                    "Troque em Usuários, ou pelo aviso no topo da tela.",
                )
            )

        # 2. O painel nunca foi salvo.
        painel_ok = (
            await db.execute(
                select(func.count(BackupRun.id)).where(
                    BackupRun.host_id.is_(None), BackupRun.status == "sucesso"
                )
            )
        ).scalar() or 0
        if not painel_ok:
            achados.append(
                _achado(
                    ATENCAO,
                    "backup",
                    "O painel nunca foi salvo",
                    "nenhum backup do painel com sucesso no histórico",
                    "Se esta máquina morrer, perdem-se cadastro dos servidores, "
                    "credenciais cifradas, agendamentos, histórico e auditoria.",
                    "Backups → Backup do painel. São alguns MB.",
                )
            )

        # 3. Nenhum destino ativo — backup não tem para onde ir.
        destinos = (
            await db.execute(
                select(func.count(Destino.id)).where(Destino.enabled.is_(True))
            )
        ).scalar() or 0
        if not destinos:
            achados.append(
                _achado(
                    CRITICO,
                    "backup",
                    "Nenhum destino de backup ativo",
                    "zero destinos habilitados",
                    "Toda execução vai falhar no fim, depois de já ter copiado "
                    "o artefato do servidor de produção.",
                    "Cadastre e marque um destino como padrão em Destinos.",
                )
            )

        # 4. Agendamento existe mas está desligado.
        desligados = (
            await db.execute(
                select(func.count(Schedule.id)).where(Schedule.enabled.is_(False))
            )
        ).scalar() or 0
        if desligados:
            achados.append(
                _achado(
                    INFO,
                    "backup",
                    "Agendamento desligado",
                    f"{desligados} agendamento(s) cadastrados e desabilitados",
                    "A recorrência não roda. Se foi proposital, ignore.",
                    "Agendamentos → habilitar, ou remover o que não serve mais.",
                )
            )

        # 5. Backup com falha na última execução de cada servidor.
        ultimas = (
            await db.execute(
                select(BackupRun)
                .where(BackupRun.host_id.isnot(None))
                .order_by(BackupRun.started_at.desc())
                .limit(40)
            )
        ).scalars().all()
        vistos: set[int] = set()
        for run in ultimas:
            if run.host_id in vistos:
                continue
            vistos.add(run.host_id)
            if run.status == "falha":
                nomes = await self._nomes(db)
                achados.append(
                    _achado(
                        ATENCAO,
                        "backup",
                        "O último backup deste servidor falhou",
                        (run.error or "sem detalhe")[:220],
                        "Não existe cópia recente deste servidor.",
                        "Backups → ver o log da execução e disparar de novo.",
                        servidor=nomes.get(run.host_id, "?"),
                    )
                )

        return achados

    async def _nomes(self, db) -> dict[int, str]:
        linhas = await db.execute(select(Host.id, Host.name))
        return {i: n for i, n in linhas.all()}

    # ── Por servidor ───────────────────────────────────────────────────

    async def _ler_licenca(self, host):
        return await self.licenca.ler(host)

    async def _ler_internos(self, host):
        return await self.internos.ler(host)

    # ── Licença ────────────────────────────────────────────────────────

    def _checar_licenca(self, host, dados) -> list[dict]:
        if isinstance(dados, Exception):
            # Só é achado onde o FindFace deveria estar. Máquina de outra
            # função na topologia não tem NTLS, e isso não é falha.
            texto = str(dados)
            if "nenhum container do FindFace" in texto or "não encontrei" in texto:
                return []
            return [
                _achado(
                    ATENCAO,
                    "licença",
                    "Não consegui ler a licença",
                    texto[:280],
                    "Sem leitura da licença, limite estourado e vencimento "
                    "passam em branco até alguém tropeçar neles.",
                    "Licenciamento e dispositivos → Atualizar uso, para ver o "
                    "erro completo.",
                    servidor=host.name,
                )
            ]
        if not isinstance(dados, dict):
            return []

        achados: list[dict] = []
        cab = dados.get("cabecalho") or {}

        if cab.get("valido") is False:
            achados.append(
                _achado(
                    CRITICO,
                    "licença",
                    "Licença inválida",
                    f"licença {cab.get('id', '?')} marcada como inválida pelo NTLS",
                    "O FindFace deixa de processar. É parada de operação, não "
                    "aviso.",
                    "Verifique o arquivo .lic e a comunicação com o servidor de "
                    "licenças da NtechLab.",
                    servidor=host.name,
                )
            )

        dias = cab.get("dias_para_expirar")
        if isinstance(dias, int) and dias <= 60:
            achados.append(
                _achado(
                    CRITICO if dias <= 15 else ATENCAO,
                    "licença",
                    "Licença perto de expirar",
                    f"expira em {dias} dia(s) — {cab.get('validade', '?')}",
                    "No vencimento o reconhecimento para.",
                    "Acione a renovação com a NtechLab e importe o .lic novo.",
                    servidor=host.name,
                    manual="a interface do fabricante avisa com 60 dias",
                )
            )

        for item in dados.get("itens") or []:
            usado, limite = item.get("usado"), item.get("limite")
            if item.get("estourado"):
                achados.append(
                    _achado(
                        CRITICO,
                        "licença",
                        f"Limite estourado: {item.get('recurso')}",
                        f"{usado:,} em uso de {limite:,} liberados".replace(",", "."),
                        "Recurso acima do contratado — o FindFace pode recusar "
                        "novo processamento sem avisar em tela.",
                        "Reduza o consumo (rotatividade e limpeza) ou amplie o "
                        "contrato.",
                        servidor=host.name,
                    )
                )
                continue
            if (
                isinstance(usado, int)
                and isinstance(limite, int)
                and limite > 0
                and not item.get("ilimitado")
            ):
                pct = usado / limite * 100
                if pct >= 90:
                    achados.append(
                        _achado(
                            CRITICO if pct >= 97 else ATENCAO,
                            "licença",
                            f"Recurso quase no limite: {item.get('recurso')}",
                            f"{pct:.0f}% — {usado} de {limite}",
                            "Passando do limite, o processamento é recusado.",
                            "Veja o ritmo de consumo na aba de Licenciamento e "
                            "ajuste a rotatividade em Manutenção.",
                            servidor=host.name,
                            manual="a interface do fabricante trata 90% como erro",
                        )
                    )
        return achados

    # ── Componentes internos ───────────────────────────────────────────

    def _checar_internos(self, host, dados) -> list[dict]:
        if isinstance(dados, Exception):
            return [
                _achado(
                    ATENCAO,
                    "componente",
                    "Não consegui consultar os componentes",
                    str(dados)[:280],
                    "Serviço travado deixa de ser detectável — e é a falha que "
                    "o `docker ps` não mostra.",
                    "Descoberta → Componentes internos, para ver o erro.",
                    servidor=host.name,
                )
            ]
        if not isinstance(dados, dict):
            return []

        achados: list[dict] = []
        for comp in dados.get("componentes") or []:
            container = comp.get("container") or ""
            escutando = comp.get("escutando")
            vivo = comp.get("vivo")

            # O caso que importa: o container está de pé e o serviço não
            # responde. `docker ps` diz "Up" e a operação está parada.
            #
            # `sondavel` é o que separa isso de um falso alarme. Os
            # componentes do FindFace conversam DENTRO da rede do Docker e
            # não publicam porta na máquina; onde a sonda não alcança, o
            # painel não sabe se está vivo — e não saber é diferente de
            # estar parado. Antes desta condição, a tela acusava
            # "reconhecimento parado" num servidor com 453 dispositivos
            # funcionando.
            if (
                container
                and "Up" in container
                and comp.get("sondavel")
                and not vivo
            ):
                achados.append(
                    _achado(
                        CRITICO,
                        "componente",
                        f"Serviço travado: {comp.get('nome')}",
                        f"porta {comp.get('porta')} escutando, container {container}, "
                        f"sem resposta HTTP{(' (' + comp['codigo'] + ')') if comp.get('codigo') else ''}",
                        f"{comp.get('papel')} — o container aparece como saudável "
                        "no `docker ps`, e não está atendendo.",
                        "Serviços → reiniciar este container. Confira antes se "
                        "há limpeza de eventos em andamento.",
                        servidor=host.name,
                        manual="não reiniciar container durante purga de dados",
                    )
                )
                continue

            if container and "Restarting" in container:
                achados.append(
                    _achado(
                        CRITICO,
                        "componente",
                        f"Container reiniciando em laço: {comp.get('nome')}",
                        container,
                        f"{comp.get('papel')} indisponível de forma intermitente.",
                        "Serviços → ver o log deste container.",
                        servidor=host.name,
                    )
                )
            elif container and "unhealthy" in container.lower():
                achados.append(
                    _achado(
                        ATENCAO,
                        "componente",
                        f"Container marcado unhealthy: {comp.get('nome')}",
                        container,
                        f"{comp.get('papel')} pode estar degradado.",
                        "Serviços → ver o log deste container.",
                        servidor=host.name,
                    )
                )

        # Só é falha quando havia como sondar. Componente que o painel não
        # alcança (rede interna do compose, serviço que não fala HTTP) vira
        # informação, não crítico: acusar parada sem ter medido é o tipo de
        # alarme que ensina a ignorar a tela.
        if dados.get("presentes") and not dados.get("vivos"):
            if dados.get("sondaveis"):
                achados.append(
                    _achado(
                        CRITICO,
                        "componente",
                        "Nenhum componente do FindFace respondeu",
                        f"{dados['sondaveis']} componente(s) sondáveis, zero respondendo",
                        "Reconhecimento parado neste servidor.",
                        "Serviços → estado do stack; e Logs ao vivo para achar a causa.",
                        servidor=host.name,
                    )
                )
            elif not dados.get("tem_curl", True):
                achados.append(
                    _achado(
                        INFO,
                        "componente",
                        "Sem `curl` no servidor",
                        "o painel não consegue sondar os componentes daqui",
                        "O estado interno dos componentes fica invisível; "
                        "container parado ainda aparece em Serviços.",
                        "Instale `curl` no servidor, ou acompanhe por Serviços.",
                        servidor=host.name,
                    )
                )
            else:
                achados.append(
                    _achado(
                        INFO,
                        "componente",
                        "Componentes não sondáveis a partir do host",
                        f"{dados['presentes']} container(es) do FindFace rodando, "
                        "nenhum respondendo na rede alcançável",
                        "Não é sinal de parada: os componentes conversam dentro "
                        "da rede do Docker e podem não publicar porta. O que o "
                        "painel afirma aqui é apenas que não conseguiu medir.",
                        "Serviços mostra o estado dos containers, que continua "
                        "valendo.",
                        servidor=host.name,
                    )
                )
        return achados

    # ── Monitor e coleta ───────────────────────────────────────────────

    async def _checar_monitor(self, db, host) -> list[dict]:
        if not host.monitorar:
            return []

        ultima = (
            await db.execute(
                select(Amostra)
                .where(Amostra.host_id == host.id)
                .order_by(Amostra.ts.desc())
                .limit(1)
            )
        ).scalars().first()

        if ultima is None:
            return [
                _achado(
                    INFO,
                    "monitor",
                    "Sem histórico de monitoramento",
                    "nenhuma amostra gravada para este servidor",
                    "Gráficos vazios e alerta de tendência sem base.",
                    "Aguarde um minuto após cadastrar, ou confira a conexão.",
                    servidor=host.name,
                )
            ]

        atraso = datetime.now(timezone.utc) - ultima.ts
        if atraso > timedelta(minutes=10):
            return [
                _achado(
                    ATENCAO,
                    "monitor",
                    "O coletor parou de gravar",
                    f"última amostra há {int(atraso.total_seconds() // 60)} minuto(s)",
                    "O painel deixa de ver o servidor — e alerta que não chega "
                    "parece ausência de problema.",
                    "Confira a conexão SSH em Servidores → Testar conexão.",
                    servidor=host.name,
                )
            ]

        achados: list[dict] = []
        if ultima.erro:
            achados.append(
                _achado(
                    ATENCAO,
                    "monitor",
                    "A última coleta falhou",
                    ultima.erro[:220],
                    "Buraco no gráfico e alerta cego enquanto durar.",
                    "Servidores → Testar conexão.",
                    servidor=host.name,
                )
            )
        if ultima.disco_pct >= 90:
            achados.append(
                _achado(
                    CRITICO if ultima.disco_pct >= 95 else ATENCAO,
                    "disco",
                    "Disco quase cheio",
                    f"{ultima.disco_pct:.0f}% em {ultima.disco_ponto or '/'} — "
                    f"{ultima.disco_livre_gb:.1f} GB livres",
                    "Disco cheio num servidor de reconhecimento facial para a "
                    "gravação de evento e pode corromper banco.",
                    "Manutenção → rotatividade e limpeza de eventos. É a causa "
                    "mais comum: as fotos de evento.",
                    servidor=host.name,
                )
            )
        if ultima.containers_problema:
            achados.append(
                _achado(
                    ATENCAO,
                    "componente",
                    "Containers com problema na última coleta",
                    f"{ultima.containers_problema} de {ultima.containers_total}",
                    "Parte do FindFace fora do ar.",
                    "Serviços → ver quais e o log de cada um.",
                    servidor=host.name,
                )
            )
        return achados

    # ── API do FindFace ────────────────────────────────────────────────

    def _checar_api(self, host) -> list[dict]:
        from app.services.ffapi_service import configurado

        if configurado(host):
            return []
        return [
            _achado(
                INFO,
                "api",
                "API do FindFace não cadastrada",
                f"'{host.name}' sem usuário e senha da API",
                "Contagem de dispositivos, detector externo e retenção da "
                "plataforma ficam indisponíveis; a licença ainda vem por SSH.",
                "Servidores → editar → API do FindFace.",
                servidor=host.name,
            )
        ]
