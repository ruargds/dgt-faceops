"""
Limpeza de dados antigos do FindFace — procedimento oficial da NtechLab.

É o que ataca a causa raiz do disco cheio. Num servidor real, as fotos de
evento ocupavam 242 GB de 268 GB do diretório de dados. Backup não
resolve isso; retenção de evento resolve.

Comando oficial:

    docker exec <container-legacy> /opt/findface-security/bin/python3 \\
        /tigre_prototype/manage.py cleanup [opções]

**A idade vai em SEGUNDOS.** A tela recebe dias e converte — pedir
segundos ao operador é convite a apagar cinco anos achando que apagou
cinco dias.

O manual traz um aviso que virou trava aqui:

> "Do not restart any FindFace Multi service containers or the Docker
> daemon while manually purging old data from the database as this will
> cause system errors!"

Enquanto uma limpeza roda, o painel **recusa** reiniciar container e
parar o stack naquele servidor. Sem essa trava, dois operadores em telas
diferentes conseguiriam corromper o banco sem nenhum aviso.
"""
import asyncio
import logging
import re
import shlex

from app.services.ssh_service import SSHError, SSHService

log = logging.getLogger("faceops.limpeza")

CAMINHO_PYTHON = "/opt/findface-security/bin/python3"
CAMINHO_MANAGE = "/tigre_prototype/manage.py"

# Opções documentadas na 2.4.1, com o que cada uma apaga. A lista real é
# lida do próprio `--help` do servidor — isto aqui é só a tradução para
# quem lê a tela.
DESCRICOES = {
    "face-events-max-unmatched-age":
        "Eventos de face SEM correspondência (não bateram com nenhum dossiê)",
    "face-events-max-matched-age":
        "Eventos de face COM correspondência",
    "face-events-max-fullframe-unmatched-age":
        "Quadro completo dos eventos de face sem correspondência",
    "face-events-max-fullframe-matched-age":
        "Quadro completo dos eventos de face com correspondência",
    "body-events-max-unmatched-age":
        "Eventos de corpo sem correspondência",
    "body-events-max-matched-age":
        "Eventos de corpo com correspondência",
    "body-events-max-fullframe-unmatched-age":
        "Quadro completo dos eventos de corpo sem correspondência",
    "body-events-max-fullframe-matched-age":
        "Quadro completo dos eventos de corpo com correspondência",
    "car-events-max-unmatched-age":
        "Eventos de veículo sem correspondência",
    "car-events-max-matched-age":
        "Eventos de veículo com correspondência",
    "car-events-max-fullframe-unmatched-age":
        "Quadro completo dos eventos de veículo sem correspondência",
    "car-events-max-fullframe-matched-age":
        "Quadro completo dos eventos de veículo com correspondência",
    "counter-records-max-age":
        "Capturas de tela dos contadores",
    "face-cluster-events-max-age":
        "Eventos de agrupamento de faces (clusters)",
    "body-cluster-events-max-age":
        "Eventos de agrupamento de corpos",
    "car-cluster-events-max-age":
        "Eventos de agrupamento de veículos",
    "audit-logs-max-age":
        "Log de auditoria do próprio FindFace",
    "external-vms-events-max-age":
        "Eventos vindos de VMS externo",
}

# As que mais liberam espaço em instalação com muitas câmeras. A tela
# sugere estas primeiro.
MAIS_PESADAS = frozenset({
    "face-events-max-fullframe-unmatched-age",
    "face-events-max-fullframe-matched-age",
    "face-events-max-unmatched-age",
    "body-events-max-fullframe-unmatched-age",
})

OPCAO_VALIDA = re.compile(r"^[a-z][a-z0-9-]{3,60}$")


class LimpezaError(Exception):
    pass


class LimpezaService:
    def __init__(self, ssh: SSHService) -> None:
        self.ssh = ssh
        # Um por host. O manual proíbe reiniciar container durante a
        # limpeza — o lock é o que o painel usa para se recusar.
        self._locks: dict[int, asyncio.Lock] = {}
        self._ultimo: dict[int, dict] = {}

    def _lock(self, host_id: int) -> asyncio.Lock:
        if host_id not in self._locks:
            self._locks[host_id] = asyncio.Lock()
        return self._locks[host_id]

    def em_andamento(self, host_id: int) -> bool:
        """Consultado pelo stack_service antes de reiniciar qualquer coisa."""
        return self._lock(host_id).locked()

    def ultimo(self, host_id: int) -> dict | None:
        return self._ultimo.get(host_id)

    # ── Descoberta ─────────────────────────────────────────────────────

    async def _container_legacy(self, host, stack) -> str:
        """Acha o container do findface-multi-legacy naquele host."""
        projeto = await stack._projeto(host)
        sudo = await self.ssh.docker_needs_sudo(host)
        r = await self.ssh.run(
            host,
            f"docker ps --filter label=com.docker.compose.project={shlex.quote(projeto)} "
            "--filter label=com.docker.compose.service=findface-multi-legacy "
            "--format '{{.Names}}' | head -1",
            sudo=sudo,
            timeout=40,
        )
        nome = (r.stdout or "").strip()
        if not nome:
            raise LimpezaError(
                "container 'findface-multi-legacy' não encontrado neste servidor. "
                "A limpeza roda onde a aplicação do FindFace está."
            )
        return nome

    async def opcoes(self, host, stack) -> dict:
        """
        Pergunta ao próprio `manage.py` quais opções ele aceita.

        Melhor que eu manter uma lista fixa: a lista muda entre versões,
        e uma opção inventada faria o comando falhar inteiro — depois de
        o operador já ter confirmado a limpeza.
        """
        container = await self._container_legacy(host, stack)
        sudo = await self.ssh.docker_needs_sudo(host)

        r = await self.ssh.run(
            host,
            f"docker exec {shlex.quote(container)} {CAMINHO_PYTHON} "
            f"{CAMINHO_MANAGE} cleanup --help 2>&1",
            sudo=sudo,
            timeout=120,
        )
        saida = r.stdout or ""

        encontradas: list[dict] = []
        for m in re.finditer(r"--([a-z][a-z0-9-]*-age)\b", saida):
            nome = m.group(1)
            if any(o["nome"] == nome for o in encontradas):
                continue
            encontradas.append({
                "nome": nome,
                "descricao": DESCRICOES.get(nome, "—"),
                "pesada": nome in MAIS_PESADAS,
            })

        if not encontradas:
            # O `--help` pode falhar sem que a limpeza esteja indisponível;
            # devolvemos a lista documentada, marcando que não foi
            # confirmada pelo servidor.
            encontradas = [
                {"nome": n, "descricao": d, "pesada": n in MAIS_PESADAS}
                for n, d in DESCRICOES.items()
            ]
            return {
                "container": container,
                "opcoes": encontradas,
                "confirmado_pelo_servidor": False,
                "saida_help": saida[-2000:],
            }

        encontradas.sort(key=lambda o: (not o["pesada"], o["nome"]))
        return {
            "container": container,
            "opcoes": encontradas,
            "confirmado_pelo_servidor": True,
            "saida_help": "",
        }

    # ── Execução ───────────────────────────────────────────────────────

    async def executar(self, host, stack, itens: list[dict]) -> dict:
        """
        Roda a limpeza. `itens` = [{"opcao": "...", "dias": 30}, ...].

        Dias viram segundos aqui. `dias = 0` apaga TUDO daquele tipo — o
        manual permite, e a tela avisa em vermelho antes de deixar.
        """
        if not itens:
            raise LimpezaError("informe ao menos um tipo de dado para limpar")

        if self.em_andamento(host.id):
            raise LimpezaError(
                f"já existe uma limpeza em andamento em '{host.name}'"
            )

        argumentos: list[str] = []
        resumo: list[dict] = []
        for item in itens:
            opcao = str(item.get("opcao", "")).strip()
            if not OPCAO_VALIDA.match(opcao):
                raise LimpezaError(f"opção inválida: {opcao!r}")
            try:
                dias = int(item.get("dias"))
            except (TypeError, ValueError):
                raise LimpezaError(f"dias inválido para {opcao}")
            if dias < 0 or dias > 3650:
                raise LimpezaError(f"dias fora do intervalo para {opcao}: {dias}")

            segundos = dias * 86400
            argumentos.append(f"--{opcao} {segundos}")
            resumo.append({
                "opcao": opcao,
                "dias": dias,
                "segundos": segundos,
                "descricao": DESCRICOES.get(opcao, "—"),
            })

        container = await self._container_legacy(host, stack)
        sudo = await self.ssh.docker_needs_sudo(host)

        comando = (
            f"docker exec {shlex.quote(container)} {CAMINHO_PYTHON} "
            f"{CAMINHO_MANAGE} cleanup {' '.join(argumentos)} 2>&1"
        )

        async with self._lock(host.id):
            log.info("limpeza iniciada em %s: %s", host.name, argumentos)
            try:
                # Limpeza em base grande leva muito tempo. 6h é folga; o
                # comando roda dentro do container e não depende da sessão.
                r = await self.ssh.run(host, comando, sudo=sudo, timeout=6 * 3600)
            except SSHError as exc:
                resultado = {
                    "ok": False,
                    "erro": str(exc),
                    "itens": resumo,
                    "saida": "",
                }
                self._ultimo[host.id] = resultado
                raise LimpezaError(
                    f"a limpeza falhou em '{host.name}': {exc}"
                ) from exc

        resultado = {
            "ok": r.ok,
            "erro": "" if r.ok else (r.stderr or r.stdout)[-1500:],
            "itens": resumo,
            "saida": (r.stdout or "")[-8000:],
            "duracao_ms": r.duration_ms,
            "container": container,
        }
        self._ultimo[host.id] = resultado

        if not r.ok:
            raise LimpezaError(
                f"o comando de limpeza retornou erro (exit {r.exit_status}):\n"
                f"{resultado['erro']}"
            )
        return resultado
