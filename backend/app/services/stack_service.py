"""
Status e controle dos serviços do FindFace Multi.

Duas decisões de segurança que valem registro:

1. **Cerca no projeto compose.** Toda ação valida que o container alvo
   pertence ao projeto do FindFace naquele host. Sem essa cerca, um
   endpoint de "reiniciar container" vira controle remoto irrestrito do
   Docker — dá para derrubar o próprio painel, o agente Zabbix, qualquer
   coisa.
2. **Nome validado por allowlist.** O nome vai para a linha de comando;
   `shlex.quote` já resolveria, mas a allowlist rejeita antes de chegar
   perto do shell.

Sobre o binário: o FindFace Multi 2.4.1 é instalado com `docker-compose`
v1 (a doc oficial usa `sudo docker-compose stop`). Servidores atualizados
podem ter só o plugin v2 (`docker compose`). O serviço detecta qual existe.
"""
import json
import re
import shlex

from app.services.ssh_service import SSHError, SSHService

SEP = "###FACEOPS:"

# Nome de container/serviço: o que o Docker aceita, e nada além disso.
NOME_VALIDO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

# Serviços do FindFace Multi que usam GPU — a UI destaca esses.
# O `-lb` é apenas balanceador da extração: não toca na GPU.
SERVICOS_GPU = frozenset({
    "findface-extraction-api",
    "findface-video-worker",
    "findface-liveness-api",
})

# Serviços que guardam estado. Reiniciar é seguro; remover não é.
# `findface-upload` entra aqui porque é onde as fotos de evento moram.
SERVICOS_DADOS = frozenset({
    "postgresql",
    "pgbouncer",
    "tarantool",
    "findface-tarantool-server",
    "mongodb",
    "redis",
    "etcd",
    "timescaledb",
    "rabbitmq",
    "nats",
    "nats-jetstream",
    "findface-upload",
})

# Jobs de uma vez só: rodam na subida, fazem a migração e SAEM com 0.
# Contá-los como "parado" faria o painel reportar meia dúzia de serviços
# com problema para sempre — alarme falso permanente é pior que alarme
# nenhum, porque ensina a ignorar a tela.
SUFIXOS_JOB = ("-migrate", "-init", "-migration")
NOMES_JOB = frozenset({"findface-multi-legacy-migrate"})


def _e_job(servico: str) -> bool:
    """O container é job de execução única em vez de serviço contínuo?"""
    return servico in NOMES_JOB or servico.endswith(SUFIXOS_JOB)


class StackError(Exception):
    """Erro de operação no stack do FindFace."""


def _validar_nome(nome: str) -> str:
    if not NOME_VALIDO.match(nome or ""):
        raise StackError(f"nome de serviço inválido: {nome!r}")
    return nome


def _compose_dir(host) -> str:
    return host.ffmulti_dir or "/opt/findface-multi"


def _compose_file(host) -> str:
    if host.compose_file:
        return host.compose_file
    return f"{_compose_dir(host)}/docker-compose.yaml"


class StackService:
    def __init__(self, ssh: SSHService, config=None) -> None:
        self.ssh = ssh
        # Opcional de proposito: se nao vier, cai no padrao. Servico que
        # quebra por falta de configuracao seria pior que o valor fixo.
        self.config = config
        # host_id -> "docker compose" | "docker-compose"
        self._bin_cache: dict[int, str] = {}

    def _limite_disco(self) -> int:
        if self.config is None:
            return 90
        try:
            return int(self.config.get("sessao.alerta_disco_pct"))
        except (KeyError, ValueError, TypeError):
            return 90

    # ── Detecção de ambiente ───────────────────────────────────────────

    async def compose_bin(self, host) -> str:
        """Descobre se o host usa o plugin v2 ou o binário v1."""
        if host.id in self._bin_cache:
            return self._bin_cache[host.id]

        r = await self.ssh.run(
            host,
            "if docker compose version >/dev/null 2>&1; then echo v2; "
            "elif command -v docker-compose >/dev/null 2>&1; then echo v1; "
            "else echo nenhum; fi",
            timeout=30,
        )
        marca = r.stdout.strip()
        if marca == "v2":
            binario = "docker compose"
        elif marca == "v1":
            binario = "docker-compose"
        else:
            raise StackError(
                f"nem 'docker compose' nem 'docker-compose' encontrados em "
                f"'{host.name}'. O FindFace Multi está instalado aqui?"
            )
        self._bin_cache[host.id] = binario
        return binario

    async def detectar_instalacao(self, host) -> dict:
        """
        Descobre onde o FindFace REALMENTE está, perguntando ao Docker.

        Assumir `/opt/findface-multi` é errado: em instalação distribuída
        o caminho muda, e num servidor real encontrado em campo o
        diretório sequer existia — o backup procuraria `configs/` no
        lugar errado e falharia com mensagem confusa.

        Os rótulos que o compose grava em cada container têm a verdade:
        `project.working_dir` e `project.config_files`.
        """
        sudo = await self.ssh.docker_needs_sudo(host)
        r = await self.ssh.run(
            host,
            "docker inspect $(docker ps -q | head -1) --format "
            "'{{index .Config.Labels \"com.docker.compose.project\"}}|"
            "{{index .Config.Labels \"com.docker.compose.project.config_files\"}}|"
            "{{index .Config.Labels \"com.docker.compose.project.working_dir\"}}|"
            "{{.HostConfig.LogConfig.Type}}' 2>/dev/null",
            sudo=sudo,
            timeout=40,
        )
        partes = (r.stdout or "").strip().split("|")
        if not r.ok or len(partes) < 3 or not partes[2] or partes[2] == "<no value>":
            return {}

        # config_files pode trazer vários caminhos separados por vírgula
        compose = partes[1].split(",")[0].strip()
        return {
            "projeto": partes[0].strip(),
            "compose_file": compose,
            "working_dir": partes[2].strip(),
            "log_driver": partes[3].strip() if len(partes) > 3 else "",
        }

    async def _projeto(self, host) -> str:
        """
        Nome do projeto compose. O Docker deriva do nome do diretório, mas
        um `COMPOSE_PROJECT_NAME` no .env muda isso — então perguntamos ao
        próprio Docker em vez de adivinhar pelo caminho.
        """
        arquivo = shlex.quote(_compose_file(host))
        diretorio = shlex.quote(_compose_dir(host))
        sudo = await self.ssh.docker_needs_sudo(host)
        r = await self.ssh.run(
            host,
            f"docker ps -a --filter label=com.docker.compose.project.config_files={arquivo} "
            "--format '{{index .Labels}}' | head -1",
            sudo=sudo,
            timeout=30,
        )
        for parte in (r.stdout or "").split(","):
            if parte.startswith("com.docker.compose.project="):
                return parte.split("=", 1)[1].strip()

        # Nenhum container de pé — cai no padrão do Docker: nome do diretório
        r2 = await self.ssh.run(host, f"basename {diretorio}", timeout=20)
        nome = r2.stdout.strip().lower()
        return re.sub(r"[^a-z0-9_-]", "", nome) or "findface-multi"

    # ── Leitura ────────────────────────────────────────────────────────

    async def list_services(self, host) -> dict:
        """
        Lista os containers do FindFace com estado, saúde e reinícios.

        A contagem de reinícios é o sinal mais útil aqui: um
        findface-video-worker com RestartCount subindo indica câmera
        problemática ou GPU sem memória, não container "quebrado".
        """
        projeto = await self._projeto(host)
        filtro = shlex.quote(f"label=com.docker.compose.project={projeto}")

        script = f"""
set +e
echo "{SEP}PROJETO"
echo {shlex.quote(projeto)}
echo "{SEP}PS"
docker ps -a --filter {filtro} --format '{{{{json .}}}}' 2>/dev/null
echo "{SEP}INSPECT"
ids=$(docker ps -aq --filter {filtro} 2>/dev/null)
if [ -n "$ids" ]; then
  docker inspect $ids --format \
    '{{{{.Name}}}}|{{{{.State.Status}}}}|{{{{.State.Health.Status}}}}|{{{{.RestartCount}}}}|{{{{.State.StartedAt}}}}|{{{{.State.ExitCode}}}}|{{{{.State.OOMKilled}}}}' 2>/dev/null
fi
echo "{SEP}DF"
df -B1 -P -x tmpfs -x devtmpfs -x overlay -x squashfs 2>/dev/null
echo "{SEP}END"
"""
        sudo = await self.ssh.docker_needs_sudo(host)
        resultado = await self.ssh.run_script(host, script, sudo=sudo, timeout=90)
        secoes = _split_sections(resultado.stdout)

        detalhes: dict[str, dict] = {}
        for linha in secoes.get("INSPECT", "").strip().splitlines():
            partes = linha.split("|")
            if len(partes) < 7:
                continue
            nome = partes[0].lstrip("/")
            detalhes[nome] = {
                "estado": partes[1],
                "saude": partes[2] if partes[2] not in ("", "<no value>") else None,
                "reinicios": int(partes[3]) if partes[3].isdigit() else 0,
                "iniciado_em": partes[4],
                "exit_code": int(partes[5]) if partes[5].lstrip("-").isdigit() else 0,
                "oom_killed": partes[6].lower() == "true",
            }

        servicos: list[dict] = []
        for linha in secoes.get("PS", "").strip().splitlines():
            linha = linha.strip()
            if not linha.startswith("{"):
                continue
            try:
                bruto = json.loads(linha)
            except json.JSONDecodeError:
                continue

            nome = bruto.get("Names", "")
            rotulos = bruto.get("Labels", "") or ""
            servico = ""
            for parte in rotulos.split(","):
                if parte.startswith("com.docker.compose.service="):
                    servico = parte.split("=", 1)[1]
                    break

            extra = detalhes.get(nome, {})
            estado = extra.get("estado") or bruto.get("State", "")
            nome_servico = servico or nome
            e_job = _e_job(nome_servico)

            servicos.append({
                "nome": nome,
                "servico": nome_servico,
                "e_job": e_job,
                "imagem": bruto.get("Image", ""),
                "estado": estado,
                "status_texto": bruto.get("Status", ""),
                "saude": extra.get("saude"),
                "reinicios": extra.get("reinicios", 0),
                "iniciado_em": extra.get("iniciado_em", ""),
                "exit_code": extra.get("exit_code", 0),
                "oom_killed": extra.get("oom_killed", False),
                "portas": bruto.get("Ports", ""),
                "usa_gpu": nome_servico in SERVICOS_GPU,
                "guarda_dados": nome_servico in SERVICOS_DADOS,
            })

        servicos.sort(key=lambda s: (s["e_job"], s["estado"] == "running", s["servico"]))

        rodando = sum(1 for s in servicos if s["estado"] == "running")

        # Um job só é problema se terminou com erro; um serviço contínuo é
        # problema se não está de pé, está unhealthy, ou morreu por falta
        # de memória.
        doentes = [
            s for s in servicos
            if (
                s["oom_killed"]
                or s["saude"] == "unhealthy"
                or (s["e_job"] and s["exit_code"] != 0)
                or (not s["e_job"] and s["estado"] != "running")
            )
        ]

        continuos = [s for s in servicos if not s["e_job"]]

        # Disco vem de carona no mesmo script. Disco cheio derruba
        # PostgreSQL e Tarantool antes de qualquer container aparecer
        # como "com problema" — o painel precisa avisar ANTES disso.
        discos = _parse_df(secoes.get("DF", ""))
        limite = self._limite_disco()
        criticos = [d for d in discos if d["percentual"] >= limite]

        return {
            "host_id": host.id,
            "host": host.name,
            "projeto": projeto,
            "compose_file": _compose_file(host),
            # "total" conta só serviço contínuo: é o denominador que faz
            # sentido no cartão ("26 de 33 rodando"). Jobs concluídos
            # apareceriam como faltando.
            "total": len(continuos),
            "rodando": sum(1 for s in continuos if s["estado"] == "running"),
            "jobs": len(servicos) - len(continuos),
            "com_problema": len(doentes),
            "servicos": servicos,
            "discos": discos,
            "discos_criticos": criticos,
        }

    async def logs(self, host, container: str, linhas: int = 200) -> str:
        """Últimas linhas de log de um container do projeto."""
        _validar_nome(container)
        await self._garantir_do_projeto(host, container)
        linhas = max(1, min(int(linhas), 2000))
        sudo = await self.ssh.docker_needs_sudo(host)
        r = await self.ssh.run(
            host,
            f"docker logs --tail {linhas} --timestamps {shlex.quote(container)} 2>&1",
            sudo=sudo,
            timeout=60,
        )
        return r.stdout or r.stderr

    # ── Cerca de segurança ─────────────────────────────────────────────

    async def _garantir_do_projeto(self, host, container: str) -> None:
        """
        Recusa agir em container que não seja do projeto do FindFace.

        Sem isto, `POST /services/restart` com nome arbitrário derruba
        qualquer container do servidor — inclusive o próprio painel.
        """
        projeto = await self._projeto(host)
        sudo = await self.ssh.docker_needs_sudo(host)
        r = await self.ssh.run(
            host,
            f"docker inspect {shlex.quote(container)} "
            "--format '{{index .Config.Labels \"com.docker.compose.project\"}}' 2>/dev/null",
            sudo=sudo,
            timeout=30,
        )
        dono = r.stdout.strip()
        if not r.ok or not dono or dono in ("<no value>",):
            raise StackError(
                f"container '{container}' não encontrado em '{host.name}' "
                "ou não pertence a nenhum projeto compose."
            )
        if dono != projeto:
            raise StackError(
                f"recusado: '{container}' pertence ao projeto '{dono}', não ao "
                f"projeto do FindFace ('{projeto}'). O painel só age no stack "
                "do FindFace Multi."
            )

    # ── Ações ──────────────────────────────────────────────────────────

    async def restart_container(self, host, container: str, timeout_s: int = 60) -> dict:
        """Reinicia um container. Ação de menor risco — resolve a maioria."""
        _validar_nome(container)
        await self._garantir_do_projeto(host, container)

        r = await self.ssh.run(
            host,
            f"docker restart -t {int(timeout_s)} {shlex.quote(container)}",
            sudo=True,
            timeout=timeout_s + 60,
        )
        if not r.ok:
            raise StackError(
                f"falha ao reiniciar '{container}': {(r.stderr or r.stdout)[:400]}"
            )

        estado = await self.ssh.run(
            host,
            f"docker inspect {shlex.quote(container)} --format "
            "'{{.State.Status}}|{{.State.Health.Status}}'",
            sudo=await self.ssh.docker_needs_sudo(host),
            timeout=30,
        )
        partes = estado.stdout.strip().split("|")
        return {
            "container": container,
            "estado": partes[0] if partes else "desconhecido",
            "saude": partes[1] if len(partes) > 1 and partes[1] != "<no value>" else None,
            "duracao_ms": r.duration_ms,
        }

    async def stack_action(self, host, acao: str) -> dict:
        """
        Para/sobe o stack inteiro. Derruba o reconhecimento facial —
        a rota que chama isto exige dupla confirmação.
        """
        if acao not in ("stop", "up", "restart"):
            raise StackError(f"ação inválida: {acao}")

        binario = await self.compose_bin(host)
        arquivo = shlex.quote(_compose_file(host))
        diretorio = shlex.quote(_compose_dir(host))

        if acao == "stop":
            comando = f"cd {diretorio} && {binario} -f {arquivo} stop"
            limite = 600
        elif acao == "up":
            comando = f"cd {diretorio} && {binario} -f {arquivo} up -d"
            limite = 900
        else:
            comando = f"cd {diretorio} && {binario} -f {arquivo} restart"
            limite = 900

        r = await self.ssh.run(host, comando, sudo=True, timeout=limite)
        if not r.ok:
            raise StackError(
                f"'{acao}' falhou em '{host.name}': {(r.stderr or r.stdout)[:800]}"
            )
        return {
            "acao": acao,
            "duracao_ms": r.duration_ms,
            "saida": (r.stdout or r.stderr)[-4000:],
        }

    async def health_summary(self, host) -> dict:
        """Resumo curto para o cartão do host na tela inicial."""
        try:
            dados = await self.list_services(host)
        except (SSHError, StackError) as exc:
            return {
                "host_id": host.id,
                "ok": False,
                "erro": str(exc)[:300],
                "total": 0,
                "rodando": 0,
                "com_problema": 0,
                "discos_criticos": [],
            }
        criticos = dados.get("discos_criticos", [])
        return {
            "host_id": host.id,
            # Disco em 90%+ conta como "nao ok" mesmo com todo container
            # de pe: e o estado que antecede a queda, e e quando ainda da
            # tempo de agir.
            "ok": dados["com_problema"] == 0 and not criticos,
            "erro": "",
            "total": dados["total"],
            "rodando": dados["rodando"],
            "com_problema": dados["com_problema"],
            "discos_criticos": [
                {"ponto": d["ponto"], "percentual": d["percentual"],
                 "livre_bytes": d["livre_bytes"]}
                for d in criticos
            ],
        }


def _parse_df(texto: str) -> list[dict]:
    """`df -P` -> lista de montagens. Uma linha por sistema de arquivos."""
    montagens: list[dict] = []
    for linha in texto.strip().splitlines()[1:]:
        partes = linha.split()
        if len(partes) < 6:
            continue
        try:
            total, usado, livre = int(partes[1]), int(partes[2]), int(partes[3])
        except ValueError:
            continue
        montagens.append({
            "dispositivo": partes[0],
            "ponto": " ".join(partes[5:]),
            "total_bytes": total,
            "usado_bytes": usado,
            "livre_bytes": livre,
            "percentual": round(usado / total * 100, 1) if total else 0.0,
        })
    return montagens


def _split_sections(saida: str) -> dict[str, str]:
    secoes: dict[str, str] = {}
    atual: str | None = None
    buffer: list[str] = []
    for linha in saida.splitlines():
        if linha.startswith(SEP):
            if atual is not None:
                secoes[atual] = "\n".join(buffer)
            atual = linha[len(SEP):].strip()
            buffer = []
        elif atual is not None:
            buffer.append(linha)
    if atual is not None:
        secoes[atual] = "\n".join(buffer)
    return secoes
