"""
Status e controle dos serviços do Face Detect.

Duas decisões de segurança que valem registro:

1. **Cerca no projeto compose.** Toda ação valida que o container alvo
   pertence ao projeto do Face Detect naquele host. Sem essa cerca, um
   endpoint de "reiniciar container" vira controle remoto irrestrito do
   Docker — dá para derrubar o próprio painel, o agente Zabbix, qualquer
   coisa.
2. **Nome validado por allowlist.** O nome vai para a linha de comando;
   `shlex.quote` já resolveria, mas a allowlist rejeita antes de chegar
   perto do shell.

Sobre o binário: o Face Detect 2.4.1 é instalado com `docker-compose`
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

# Serviços do Face Detect que usam GPU — a UI destaca esses.
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
    """Erro de operação no stack do Face Detect."""


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
    def __init__(self, ssh: SSHService, config=None, limpeza=None) -> None:
        self.ssh = ssh
        # O manual da NtechLab proibe reiniciar container durante a
        # limpeza de eventos: causa erro no banco. O painel recusa.
        self.limpeza = limpeza
        # Opcional de proposito: se nao vier, cai no padrao. Servico que
        # quebra por falta de configuracao seria pior que o valor fixo.
        self.config = config
        # host_id -> "docker compose" | "docker-compose"
        self._bin_cache: dict[int, str] = {}
        # host_id -> nome do projeto compose. O nome sai de um `docker ps
        # -a` e NÃO muda enquanto a instalação for a mesma — mas era
        # perguntado a cada `list_services`, ou seja, a cada ciclo do
        # monitor, em cada host. Num servidor com ~80 containers esse
        # `docker ps -a` faz o dockerd varrer todos, de minuto em minuto,
        # para reler uma resposta que já se sabia.
        self._projeto_cache: dict[int, str] = {}

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
                f"'{host.name}'. O Face Detect está instalado aqui?"
            )
        self._bin_cache[host.id] = binario
        return binario

    async def detectar_instalacao(self, host) -> dict:
        """
        Descobre onde o Face Detect REALMENTE está, perguntando ao Docker.

        Assumir `/opt/findface-multi` é errado: em instalação distribuída o
        caminho muda, e num servidor encontrado em campo o diretório sequer
        existia — o backup procuraria `configs/` no lugar errado e falharia
        com mensagem que não ajuda ninguém.

        Os rótulos que o compose grava em cada container têm a verdade:
        `project.working_dir` e `project.config_files`. O que muda aqui, em
        relação à primeira versão, é ONDE procurar: ela inspecionava o
        primeiro container da máquina, qualquer que fosse ele. Num servidor
        com mais de um projeto no Docker isso devolve um container sem o
        rótulo, e a detecção desistia — dizendo que não achou numa máquina
        onde o Face Detect estava rodando.

        Ordem: containers do Face Detect primeiro; depois qualquer container
        com o rótulo; depois o `compose ls` do v2; por último os caminhos
        conhecidos, confirmados com `test -f`.
        """
        sudo = await self.ssh.docker_needs_sudo(host)

        script = r"""
set +e
formato='{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.project.config_files"}}|{{index .Config.Labels "com.docker.compose.project.working_dir"}}|{{.HostConfig.LogConfig.Type}}'

# 1. Containers com cara de Face Detect, um a um, ate achar rotulo util.
for c in $(docker ps --format '{{.Names}}' 2>/dev/null | grep -i -E 'findface|ffmulti' | grep -v -i faceops | head -8); do
  linha="$(docker inspect "$c" --format "$formato" 2>/dev/null)"
  case "$linha" in
    *"|"*"|/"*) echo "ACHADO|$linha"; exit 0 ;;
  esac
done

# 2. Qualquer container que tenha o rotulo de working_dir.
for c in $(docker ps --format '{{.Names}}' 2>/dev/null | head -20); do
  linha="$(docker inspect "$c" --format "$formato" 2>/dev/null)"
  case "$linha" in
    *"|"*"|/"*) echo "TALVEZ|$linha" ;;
  esac
done | grep -i -E 'findface|ffmulti' | grep -v -i faceops | head -1

# 3. Compose v2 lista projeto e arquivo de configuracao.
docker compose ls --format json 2>/dev/null | head -c 4000

# 4. Caminhos conhecidos, confirmados.
for d in /opt/findface-multi /opt/ffmulti /opt/findface /srv/findface-multi /media/STORAGE/findface-multi; do
  for f in docker-compose.yaml docker-compose.yml; do
    [ -f "$d/$f" ] && echo "CAMINHO||$d/$f|$d|"
  done
done
"""

        r = await self.ssh.run(host, script, sudo=sudo, timeout=60)
        saida = r.stdout or ""

        # Rótulo do compose, que é a resposta autoritativa.
        for linha in saida.splitlines():
            if linha.startswith(("ACHADO|", "TALVEZ|")):
                partes = linha.split("|", 4)[1:]
                if len(partes) >= 3 and partes[2].startswith("/"):
                    return {
                        "projeto": partes[0].strip(),
                        "compose_file": partes[1].split(",")[0].strip(),
                        "working_dir": partes[2].strip(),
                        "log_driver": partes[3].strip() if len(partes) > 3 else "",
                        "origem": "rótulo do compose",
                    }

        # `compose ls` do v2: JSON com Name e ConfigFiles.
        if '"ConfigFiles"' in saida or '"Name"' in saida:
            import json as _json
            import re as _re

            bruto = saida[saida.find("[") : saida.rfind("]") + 1]
            try:
                projetos = _json.loads(bruto) if bruto else []
            except _json.JSONDecodeError:
                projetos = []
            for p in projetos if isinstance(projetos, list) else []:
                nome = str(p.get("Name", ""))
                arquivos = str(p.get("ConfigFiles", ""))
                juntos = nome + arquivos
                if not _re.search(r"findface|ffmulti", juntos, _re.I):
                    continue
                if _re.search(r"faceops", juntos, _re.I):
                    # O painel nao e o Face Detect: onde os dois rodam na mesma
                    # maquina, casar por nome achava /opt/.faceops.
                    continue
                primeiro = arquivos.split(",")[0].strip()
                if primeiro.startswith("/"):
                    from pathlib import PurePosixPath

                    return {
                        "projeto": nome,
                        "compose_file": primeiro,
                        "working_dir": str(PurePosixPath(primeiro).parent),
                        "log_driver": "",
                        "origem": "docker compose ls",
                    }

        # Caminho conhecido, já confirmado no servidor pelo `test -f`.
        for linha in saida.splitlines():
            if linha.startswith("CAMINHO|"):
                partes = linha.split("|")
                if len(partes) >= 4 and partes[3].startswith("/"):
                    return {
                        "projeto": "",
                        "compose_file": partes[2].strip(),
                        "working_dir": partes[3].strip(),
                        "log_driver": "",
                        "origem": "caminho conhecido",
                    }

        return {}

    async def _projeto(self, host) -> str:
        """
        Nome do projeto compose. O Docker deriva do nome do diretório, mas
        um `COMPOSE_PROJECT_NAME` no .env muda isso — então perguntamos ao
        próprio Docker em vez de adivinhar pelo caminho.

        Memoizado por host, como o `compose_bin` ao lado: a resposta só
        muda se a instalação mudar de lugar, e aí o painel é reiniciado
        ou o host é reeditado — os dois esvaziam o cache.
        """
        if host.id in self._projeto_cache:
            return self._projeto_cache[host.id]

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
                nome = parte.split("=", 1)[1].strip()
                if nome:
                    self._projeto_cache[host.id] = nome
                return nome

        # Nenhum container de pé — cai no padrão do Docker: nome do
        # diretório. NÃO entra no cache: "nenhum container de pé" é um
        # estado passageiro, e memoizar o palpite faria o painel seguir
        # usando o nome adivinhado depois de o stack subir.
        r2 = await self.ssh.run(host, f"basename {diretorio}", timeout=20)
        nome = r2.stdout.strip().lower()
        return re.sub(r"[^a-z0-9_-]", "", nome) or "findface-multi"

    # ── Leitura ────────────────────────────────────────────────────────

    async def list_services(self, host) -> dict:
        """
        Lista os containers do Face Detect com estado, saúde e reinícios.

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


        # Um job só é problema se terminou com erro; um serviço contínuo é
        # problema se não está de pé, está unhealthy, ou morreu por falta
        # de memória.
        #
        # `reinicios` NÃO entra neste critério de propósito: o
        # `RestartCount` do Docker é acumulado desde a criação do
        # container, então um worker com 7 reinícios em três meses viraria
        # problema permanente. Reinício em laço é detectado pela VARIAÇÃO
        # da contagem dentro de uma janela, no `IncidenteService` — que é
        # quem tem memória entre ciclos.
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
            # Só os problemáticos, já filtrados — o monitor contínuo usa
            # isto para abrir/fechar incidente sem reprocessar a lista
            # inteira a cada ciclo.
            "doentes": doentes,
            # Contagem de reinício de cada serviço contínuo. Vai crua: quem
            # decide o que é laço é o IncidenteService, comparando com o
            # que viu nos ciclos anteriores.
            "reinicios": {s["servico"]: s["reinicios"] for s in continuos},
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
        Recusa agir em container que não seja do projeto do Face Detect.

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
                f"projeto do Face Detect ('{projeto}'). O painel só age no stack "
                "do Face Detect."
            )

    # ── Ações ──────────────────────────────────────────────────────────

    def _recusar_se_limpando(self, host) -> None:
        if self.limpeza is not None and self.limpeza.em_andamento(host.id):
            raise StackError(
                f"há uma limpeza de eventos em andamento em '{host.name}'. "
                "O manual da NtechLab é explícito: reiniciar container "
                "durante a limpeza corrompe o banco. Espere terminar."
            )

    # Verbo do Docker e o quanto esperar por cada um. `stop` dá o prazo
    # de parada graciosa ao container e só então mata; `start` não espera
    # nada além de subir o processo.
    VERBOS = {
        "restart": ("restart", True),
        "stop": ("stop", True),
        "start": ("start", False),
    }

    async def container_action(
        self, host, container: str, acao: str = "restart", timeout_s: int = 60
    ) -> dict:
        """
        Reinicia, para ou sobe UM container do projeto do Face Detect.

        Os três verbos vivem na mesma função de propósito: a cerca
        (`_garantir_do_projeto`), a recusa durante limpeza de eventos e a
        leitura do estado final são idênticas nos três casos. Em funções
        separadas, a próxima correção de cerca entraria em uma e faltaria
        nas outras duas.
        """
        verbo_info = self.VERBOS.get(acao)
        if verbo_info is None:
            raise StackError(f"ação inválida: {acao}")
        verbo, usa_timeout = verbo_info

        self._recusar_se_limpando(host)
        _validar_nome(container)
        # Vale para `start` também: `docker inspect` responde sobre
        # container parado, então a cerca continua de pé para subir algo.
        await self._garantir_do_projeto(host, container)

        prazo = f" -t {int(timeout_s)}" if usa_timeout else ""
        r = await self.ssh.run(
            host,
            f"docker {verbo}{prazo} {shlex.quote(container)}",
            sudo=True,
            timeout=timeout_s + 60,
        )
        if not r.ok:
            raise StackError(
                f"falha ao executar '{acao}' em '{container}': "
                f"{(r.stderr or r.stdout)[:400]}"
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
            "acao": acao,
            "estado": partes[0] if partes else "desconhecido",
            "saude": partes[1] if len(partes) > 1 and partes[1] != "<no value>" else None,
            "duracao_ms": r.duration_ms,
        }

    async def restart_container(self, host, container: str, timeout_s: int = 60) -> dict:
        """Reinicia um container. Ação de menor risco — resolve a maioria."""
        return await self.container_action(
            host, container, acao="restart", timeout_s=timeout_s
        )

    async def stack_action(self, host, acao: str) -> dict:
        """
        Para/sobe o stack inteiro. Derruba o reconhecimento facial —
        a rota que chama isto exige dupla confirmação.
        """
        if acao not in ("stop", "up", "restart"):
            raise StackError(f"ação inválida: {acao}")
        self._recusar_se_limpando(host)

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
                "servicos_doentes": [],
                "reinicios": {},
                "containers": {},
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
            # Detalhe de quem está doente, para o monitor abrir incidente
            # sem precisar de outra ida ao servidor.
            "servicos_doentes": [
                {
                    "servico": s["servico"],
                    "estado": s["estado"],
                    "saude": s["saude"],
                    "reinicios": s["reinicios"],
                    "exit_code": s["exit_code"],
                    "oom_killed": s["oom_killed"],
                }
                for s in dados.get("doentes", [])
            ],
            "reinicios": dados.get("reinicios", {}),
            # Serviço do compose -> nome real do container. São coisas
            # diferentes ("findface-video-worker" vs
            # "findface-multi-findface-video-worker-1"), e `docker logs`
            # só aceita o segundo — sem este mapa, ler log a partir de um
            # incidente falharia em silêncio.
            "containers": {s["servico"]: s["nome"] for s in dados.get("servicos", [])},
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
