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
        """
        Acha o container do findface-multi-legacy naquele host.

        Três tentativas, da mais específica para a mais tolerante, porque o
        nome muda com a forma de instalar. O manual da NtechLab chama o
        container de `findface-multi-findface-multi-legacy-1` — projeto
        `findface-multi`, serviço `findface-multi-legacy`, sufixo do
        compose v2. Casar só pelo rótulo do projeto detectado falha quando
        a instalação usa outro nome de projeto, e foi o que aconteceu.

        A busca por nome vem por último e exige `legacy` no nome: é
        tolerante o suficiente para achar, e específica o suficiente para
        não pegar outro container por engano — o que rodaria uma limpeza
        destrutiva no lugar errado.
        """
        sudo = await self.ssh.docker_needs_sudo(host)

        tentativas = []
        try:
            projeto = await stack._projeto(host)
        except Exception:
            projeto = ""

        if projeto:
            tentativas.append(
                f"docker ps --filter label=com.docker.compose.project={shlex.quote(projeto)} "
                "--filter label=com.docker.compose.service=findface-multi-legacy "
                "--format '{{.Names}}' | head -1"
            )
        tentativas.append(
            "docker ps --filter label=com.docker.compose.service=findface-multi-legacy "
            "--format '{{.Names}}' | head -1"
        )
        tentativas.append(
            "docker ps --format '{{.Names}}' | grep -i -E 'legacy' "
            "| grep -v -i faceops | head -1"
        )

        for comando in tentativas:
            try:
                r = await self.ssh.run(host, comando, sudo=sudo, timeout=40)
            except SSHError:
                continue
            nome = (r.stdout or "").strip().splitlines()
            nome = nome[0].strip() if nome else ""
            if nome:
                return nome

        # Não achou: dizer o que existe ali vale mais que dizer que não
        # existe. Se o legacy roda em outra máquina, o operador precisa
        # saber em qual — e não descobrir isso por eliminação.
        presentes = ""
        try:
            r = await self.ssh.run(
                host,
                "docker ps --format '{{.Names}}' | grep -i findface | head -10",
                sudo=sudo,
                timeout=40,
            )
            presentes = " ".join((r.stdout or "").split())
        except SSHError:
            presentes = ""

        raise LimpezaError(
            f"não achei o container do findface-multi-legacy em '{host.name}'. "
            + (
                f"Containers do FindFace aqui: {presentes}. "
                if presentes
                else "Nenhum container do FindFace roda aqui. "
            )
            + "A limpeza precisa rodar no servidor onde a aplicação está — "
            "veja em Topologia qual é."
        )

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

    async def executar(
        self, host, stack, itens: list[dict], como_configurado: bool = False
    ) -> dict:
        """
        Roda a limpeza. `itens` = [{"opcao": "...", "dias": 30}, ...].

        Dias viram segundos aqui. `dias = 0` apaga TUDO daquele tipo — o
        manual permite, e a tela avisa em vermelho antes de deixar.

        `como_configurado` usa o `--as-configured` do manual: a idade sai
        da política já configurada na plataforma ("Apply config age options
        for events, counter records and clusters"). É o modo certo para
        recorrência — sem ele, o agendamento carregaria uma segunda verdade
        sobre quanto tempo cada coisa fica, e as duas divergiriam no dia em
        que alguém mexesse só numa.
        """
        if not itens and not como_configurado:
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

        if como_configurado:
            # Sem valor: o manual descreve `--as-configured` como opção
            # solta, e ela convive com as idades explícitas.
            argumentos.insert(0, "--as-configured")
            resumo.insert(0, {
                "opcao": "as-configured",
                "dias": None,
                "segundos": None,
                "descricao": "Idades da política configurada na plataforma",
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
