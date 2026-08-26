"""
Descoberta — inventário do que existe em cada servidor.

O painel nasceu de campo: a topologia dos servidores do FindFace foi
levantada sondando, não lendo documentação. Esta tela transforma aquela
sondagem manual em botão. Numa varredura por SSH ela responde as
perguntas que todo o resto do painel precisa saber e que ninguém quer
descobrir na mão:

* onde roda o **PostgreSQL** (guarda as câmeras) e o **Tarantool** (os
  vetores faciais);
* quais containers existem, com imagem, estado e portas;
* que portas o servidor está de fato escutando na rede;
* se tem **GPU**, quanto de **memória e disco**, qual **SO**;
* onde o **FindFace Multi** está instalado.

Funciona igual nos dois mundos: FindFace espalhado por vários servidores
ou **tudo num servidor só**. Não assume distribuição — apenas relata o
que achou naquele host. Se o banco estiver na mesma máquina, aparece ali;
se estiver em outra, aparece na descoberta da outra.

Tudo numa única ida ao servidor — sondar em dez comandos separados
multiplicaria a latência do SSH por dez. O script marca cada seção com um
separador e o Python reparte no retorno.
"""
import asyncio
import json
import logging
import re

from app.services.ssh_service import SSHService

log = logging.getLogger("faceops.descoberta")

SEP = "###FACEOPS:"

# Como reconhecer cada serviço de dados por imagem/nome. A ordem importa:
# timescaledb casa antes de postgres genérico.
ASSINATURAS = [
    ("timescaledb", re.compile(r"timescale", re.I), "Banco de séries (TimescaleDB)"),
    ("postgresql", re.compile(r"postgres|pgsql", re.I), "PostgreSQL — câmeras, usuários, dossiês"),
    ("pgbouncer", re.compile(r"pgbouncer", re.I), "Pool de conexões do PostgreSQL"),
    ("tarantool", re.compile(r"tarantool", re.I), "Tarantool — vetores faciais"),
    ("mongodb", re.compile(r"mongo", re.I), "MongoDB"),
    ("redis", re.compile(r"redis", re.I), "Redis — cache/fila"),
    ("etcd", re.compile(r"etcd", re.I), "etcd — coordenação"),
    ("rabbitmq", re.compile(r"rabbitmq", re.I), "RabbitMQ — mensageria"),
    ("nats", re.compile(r"nats", re.I), "NATS — mensageria"),
    ("nginx", re.compile(r"nginx", re.I), "Nginx — proxy/web"),
]


# ── Modelo de camadas do FindFace distribuído ─────────────────────────
#
# O FindFace Multi reparte o trabalho entre componentes, e o fornecedor
# espalha esses componentes por várias máquinas para balancear carga
# (GPU numa, banco noutra, app noutra). O painel precisa entender essa
# dinâmica como uma cadeia de dependências: câmera → vídeo → extração →
# busca → {vetores, dados} → app. Cada camada roda em um ou mais
# servidores, e é isso que a Topologia desenha.
#
# A ordem da lista é a ordem do fluxo, da origem ao app.
CAMADAS = [
    {"chave": "cameras", "nome": "Câmeras", "desc": "Origem do vídeo", "externo": True, "gpu": False},
    {"chave": "video", "nome": "Vídeo", "desc": "Captura e decodifica os streams", "externo": False, "gpu": True},
    {"chave": "extracao", "nome": "Extração", "desc": "Gera os vetores faciais", "externo": False, "gpu": True},
    {"chave": "busca", "nome": "Busca", "desc": "Compara vetores (matching)", "externo": False, "gpu": False},
    {"chave": "vetores", "nome": "Vetores", "desc": "Tarantool — galeria de faces", "externo": False, "gpu": False},
    {"chave": "dados", "nome": "Dados", "desc": "PostgreSQL — câmeras, usuários, eventos", "externo": False, "gpu": False},
    {"chave": "midia", "nome": "Mídia", "desc": "Fotos de evento (upload)", "externo": False, "gpu": False},
    {"chave": "app", "nome": "Aplicação", "desc": "Web, API e licença", "externo": False, "gpu": False},
]

# Dependências entre camadas (origem -> destino do fluxo de dados).
CAMADA_ARESTAS = [
    ("cameras", "video"),
    ("video", "extracao"),
    ("extracao", "busca"),
    ("busca", "vetores"),
    ("busca", "dados"),
    ("app", "busca"),
    ("app", "dados"),
    ("app", "midia"),
]

# Como classificar um componente do FindFace numa camada, pelo nome do
# serviço/container/imagem. A ordem importa: o específico vem antes do
# genérico (senão 'findface-multi' engoliria tudo em 'app').
CLASSIFICADOR = [
    (re.compile(r"video-worker|video-manager|video-streamer|findface-video", re.I), "video"),
    (re.compile(r"extraction-api|liveness", re.I), "extracao"),
    (re.compile(r"sf-api|findface-sf", re.I), "busca"),
    (re.compile(r"tarantool", re.I), "vetores"),
    (re.compile(r"postgres|pgbouncer|pgsql|timescale", re.I), "dados"),
    (re.compile(r"upload", re.I), "midia"),
    (re.compile(r"multi-legacy|multi-ui|multi-mobile|multi-admin|ntls|findface-counter|findface-jager|findface-multi|nginx", re.I), "app"),
]


def _camadas_do_host(containers: list) -> list:
    """Que camadas do FindFace este servidor executa, pelos containers."""
    achadas: dict = {}
    for c in containers:
        alvo = f"{c.get('servico','')} {c.get('nome','')} {c.get('imagem','')}"
        for padrao, camada in CLASSIFICADOR:
            if padrao.search(alvo):
                achadas.setdefault(camada, 0)
                achadas[camada] += 1
                break
    # devolve na ordem do fluxo
    ordem = [c["chave"] for c in CAMADAS]
    return sorted(achadas.keys(), key=lambda k: ordem.index(k) if k in ordem else 99)


class DescobertaError(Exception):
    pass


def _split(saida: str) -> dict:
    secoes: dict = {}
    atual = None
    buf: list = []
    for linha in saida.splitlines():
        if linha.startswith(SEP):
            if atual is not None:
                secoes[atual] = "\n".join(buf)
            atual = linha[len(SEP):].strip()
            buf = []
        elif atual is not None:
            buf.append(linha)
    if atual is not None:
        secoes[atual] = "\n".join(buf)
    return secoes


def _um(texto: str) -> str:
    t = (texto or "").strip()
    return t.splitlines()[0].strip() if t else ""


def _int(texto: str, padrao: int = 0) -> int:
    try:
        return int((texto or "").strip().split()[0])
    except (ValueError, IndexError):
        return padrao


class DescobertaService:
    def __init__(self, ssh: SSHService) -> None:
        self.ssh = ssh

    async def inventariar(self, host) -> dict:
        """Uma varredura completa do servidor. Sob demanda, nunca contínua."""
        sudo = await self.ssh.docker_needs_sudo(host)

        script = f"""
set +e
echo "{SEP}SO"
. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME"
echo "{SEP}KERNEL"
uname -r
echo "{SEP}UPTIME"
uptime -p 2>/dev/null
echo "{SEP}CPU_MODELO"
grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2
echo "{SEP}CPUS"
nproc 2>/dev/null
echo "{SEP}MEM"
grep MemTotal /proc/meminfo 2>/dev/null | awk '{{print $2*1024}}'
echo "{SEP}GPU"
command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null
echo "{SEP}DOCKER_V"
docker --version 2>/dev/null
echo "{SEP}COMPOSE_V"
if docker compose version >/dev/null 2>&1; then docker compose version 2>/dev/null | head -1; elif command -v docker-compose >/dev/null 2>&1; then docker-compose --version 2>/dev/null; fi
echo "{SEP}PS"
docker ps -a --format '{{{{json .}}}}' 2>/dev/null
echo "{SEP}PORTAS"
(ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null) | grep LISTEN
echo "{SEP}DF"
df -B1 -P -x tmpfs -x devtmpfs -x overlay -x squashfs 2>/dev/null
echo "{SEP}CF_BIN"
command -v cloudflared >/dev/null 2>&1 && cloudflared --version 2>/dev/null | head -1
echo "{SEP}CF_SYSTEMD"
systemctl is-active cloudflared 2>/dev/null; systemctl is-enabled cloudflared 2>/dev/null
echo "{SEP}CF_DOCKER"
docker ps -a --format '{{{{.Names}}}}|{{{{.Image}}}}|{{{{.State}}}}' 2>/dev/null | grep -i cloudflare
echo "{SEP}END"
"""
        r = await self.ssh.run_script(host, script, sudo=sudo, timeout=120)
        s = _split(r.stdout)

        containers = self._containers(s.get("PS", ""))
        servicos_dados = self._servicos_dados(containers)

        return {
            "host_id": host.id,
            "host": host.name,
            "papel": host.role,
            "endereco": host.address,
            "so": _um(s.get("SO", "")) or "desconhecido",
            "kernel": _um(s.get("KERNEL", "")),
            "uptime": _um(s.get("UPTIME", "")),
            "cpu_modelo": _um(s.get("CPU_MODELO", "")),
            "cpus": _int(s.get("CPUS", ""), 0),
            "memoria_total_bytes": _int(s.get("MEM", ""), 0),
            "gpus": self._gpus(s.get("GPU", "")),
            "docker": {
                "versao": _um(s.get("DOCKER_V", "")).replace("Docker version ", ""),
                "compose": _um(s.get("COMPOSE_V", "")),
                "total_containers": len(containers),
                "rodando": sum(1 for c in containers if c["estado"] == "running"),
            },
            "projetos": self._projetos(containers),
            "containers": containers,
            "servicos_dados": servicos_dados,
            "portas_ouvindo": self._portas(s.get("PORTAS", "")),
            "findface": self._findface(containers, servicos_dados),
            "discos": self._discos(s.get("DF", "")),
            "cloudflared": self._cloudflared(s),
        }

    async def topologia(self, hosts: list) -> dict:
        """
        Mapa de dependências do FindFace distribuído entre os servidores.

        Varre cada servidor (em paralelo) e classifica os componentes em
        camadas. O resultado diz QUAL camada roda em QUAL máquina — que é
        como o fornecedor reparte a carga — e as dependências entre elas.
        Um servidor que não responde entra no mapa com o erro, não some.
        """
        async def _um(host):
            try:
                inv = await self.inventariar(host)
                return {
                    "host_id": host.id,
                    "host": host.name,
                    "papel": host.role,
                    "endereco": host.address,
                    "gpus": len(inv.get("gpus") or []),
                    "camadas": _camadas_do_host(inv.get("containers") or []),
                    "findface": inv.get("findface") or {},
                    "erro": None,
                }
            except Exception as exc:  # noqa: BLE001 - um host fora não derruba o mapa
                log.warning("topologia: %s falhou: %s", host.name, exc)
                return {
                    "host_id": host.id,
                    "host": host.name,
                    "papel": host.role,
                    "endereco": host.address,
                    "gpus": 0,
                    "camadas": [],
                    "findface": {},
                    "erro": str(exc)[:300],
                }

        servidores = await asyncio.gather(*[_um(h) for h in hosts]) if hosts else []
        servidores = list(servidores)

        # Índice inverso: quais servidores cobrem cada camada
        camada_hosts: dict = {c["chave"]: [] for c in CAMADAS}
        for sv in servidores:
            for camada in sv["camadas"]:
                camada_hosts.setdefault(camada, []).append(sv["host_id"])

        # Câmeras é camada externa: sempre presente conceitualmente se há
        # qualquer servidor de vídeo. Não tem host — é a origem.
        distribuido = sum(1 for sv in servidores if sv["camadas"]) > 1

        return {
            "camadas": CAMADAS,
            "arestas": CAMADA_ARESTAS,
            "servidores": servidores,
            "camada_hosts": camada_hosts,
            "distribuido": distribuido,
        }

    # -- Parsers ---------------------------------------------------------

    def _containers(self, texto: str) -> list:
        saida: list = []
        for linha in texto.strip().splitlines():
            linha = linha.strip()
            if not linha.startswith("{"):
                continue
            try:
                b = json.loads(linha)
            except json.JSONDecodeError:
                continue
            rotulos = b.get("Labels", "") or ""
            projeto = servico = ""
            for parte in rotulos.split(","):
                if parte.startswith("com.docker.compose.project="):
                    projeto = parte.split("=", 1)[1]
                elif parte.startswith("com.docker.compose.service="):
                    servico = parte.split("=", 1)[1]
            saida.append({
                "nome": b.get("Names", ""),
                "imagem": b.get("Image", ""),
                "estado": b.get("State", ""),
                "status": b.get("Status", ""),
                "portas": b.get("Ports", ""),
                "projeto": projeto,
                "servico": servico or b.get("Names", ""),
            })
        return sorted(saida, key=lambda c: (c["projeto"], c["servico"]))

    def _servicos_dados(self, containers: list) -> list:
        achados: list = []
        for c in containers:
            alvo = f"{c['imagem']} {c['nome']} {c['servico']}"
            for tipo, padrao, rotulo in ASSINATURAS:
                if padrao.search(alvo):
                    achados.append({
                        "tipo": tipo,
                        "rotulo": rotulo,
                        "container": c["nome"],
                        "imagem": c["imagem"],
                        "estado": c["estado"],
                        "portas": c["portas"],
                    })
                    break
        return achados

    def _projetos(self, containers: list) -> list:
        mapa: dict = {}
        for c in containers:
            if c["projeto"]:
                mapa[c["projeto"]] = mapa.get(c["projeto"], 0) + 1
        return [{"projeto": p, "containers": n} for p, n in sorted(mapa.items())]

    def _gpus(self, texto: str) -> list:
        saida: list = []
        for linha in (texto or "").strip().splitlines():
            if "," in linha:
                nome, mem = linha.split(",", 1)
                saida.append({"nome": nome.strip(), "memoria": mem.strip()})
        return saida

    def _portas(self, texto: str) -> list:
        vistas: set = set()
        saida: list = []
        for linha in (texto or "").strip().splitlines():
            m = re.search(r":(\d+)\s", linha)
            if not m:
                continue
            porta = m.group(1)
            proc = ""
            mp = re.search(r'users:\(\("([^"]+)"', linha)
            if mp:
                proc = mp.group(1)
            chave = f"{porta}/{proc}"
            if chave in vistas:
                continue
            vistas.add(chave)
            saida.append({"porta": int(porta), "processo": proc})
        return sorted(saida, key=lambda p: p["porta"])

    def _findface(self, containers: list, servicos_dados: list) -> dict:
        ff = [c for c in containers if "findface" in f"{c['imagem']} {c['nome']}".lower()]
        return {
            "presente": bool(ff),
            "containers": len(ff),
            "tem_banco": any(s["tipo"] in ("postgresql", "timescaledb") for s in servicos_dados),
            "tem_tarantool": any(s["tipo"] == "tarantool" for s in servicos_dados),
        }

    def _cloudflared(self, s: dict) -> dict:
        """
        Cloudflare Tunnel — é ele que publica o painel para fora.

        Reconhece as duas formas comuns de rodar: serviço systemd
        (`cloudflared.service`) e container Docker. Se não achar nenhuma,
        devolve instalado=False e a tela nem mostra o cartão.
        """
        versao = _um(s.get("CF_BIN", ""))
        sysd = [l.strip() for l in (s.get("CF_SYSTEMD", "") or "").splitlines() if l.strip()]
        ativo = bool(sysd) and sysd[0] == "active"
        habilitado = len(sysd) > 1 and sysd[1] == "enabled"
        systemd_presente = bool(sysd) and sysd[0] not in ("", "unknown")

        docker_presente = False
        container = estado = ""
        for linha in (s.get("CF_DOCKER", "") or "").splitlines():
            if "|" in linha:
                partes = linha.split("|")
                container = partes[0].strip()
                estado = partes[2].strip() if len(partes) > 2 else ""
                docker_presente = True
                break

        instalado = bool(versao) or systemd_presente or docker_presente
        # Como reiniciar: systemd tem prioridade (é o modo do cloudflared
        # instalado como serviço); senão, o container.
        modo = "systemd" if systemd_presente else ("docker" if docker_presente else "")

        return {
            "instalado": instalado,
            "versao": versao,
            "modo": modo,
            "ativo": ativo or (estado == "running"),
            "systemd": {
                "presente": systemd_presente,
                "ativo": ativo,
                "habilitado": habilitado,
            },
            "docker": {
                "presente": docker_presente,
                "container": container,
                "estado": estado,
            },
        }

    async def reiniciar_cloudflared(self, host) -> dict:
        """
        Reinicia o Cloudflare Tunnel — e SÓ ele.

        Alvo fixo, nunca um nome que venha de fora: isto reinicia
        exatamente `cloudflared` (serviço systemd) ou o container do
        cloudflared. Não é um "reinicie qualquer serviço"; é um botão de
        um propósito só, para não virar controle remoto irrestrito da
        máquina.
        """
        info = self._cloudflared(_split(
            (await self.ssh.run_script(host, self._script_cf(), timeout=60)).stdout
        ))
        if not info["instalado"]:
            raise DescobertaError(
                "cloudflared não encontrado neste servidor — nada a reiniciar."
            )

        if info["modo"] == "systemd":
            r = await self.ssh.run(host, "systemctl restart cloudflared", sudo=True, timeout=60)
            if not r.ok:
                raise DescobertaError(
                    f"falha ao reiniciar cloudflared: {(r.stderr or r.stdout)[:400]}"
                )
            estado = await self.ssh.run(host, "systemctl is-active cloudflared", timeout=20)
            return {"modo": "systemd", "estado": estado.stdout.strip() or "desconhecido"}

        if info["modo"] == "docker":
            container = info["docker"]["container"]
            # Confere que o nome mesmo contém cloudflare — trava contra
            # reiniciar outra coisa caso o parse tenha pego lixo.
            if "cloudflare" not in container.lower():
                raise DescobertaError("container do cloudflared não identificado com segurança.")
            sudo = await self.ssh.docker_needs_sudo(host)
            r = await self.ssh.run(host, f"docker restart {container}", sudo=sudo, timeout=90)
            if not r.ok:
                raise DescobertaError(
                    f"falha ao reiniciar container {container}: {(r.stderr or r.stdout)[:400]}"
                )
            return {"modo": "docker", "container": container, "estado": "reiniciado"}

        raise DescobertaError("cloudflared instalado mas sem modo de reinício reconhecido.")

    def _script_cf(self) -> str:
        return f"""
set +e
echo "{SEP}CF_BIN"
command -v cloudflared >/dev/null 2>&1 && cloudflared --version 2>/dev/null | head -1
echo "{SEP}CF_SYSTEMD"
systemctl is-active cloudflared 2>/dev/null; systemctl is-enabled cloudflared 2>/dev/null
echo "{SEP}CF_DOCKER"
docker ps -a --format '{{{{.Names}}}}|{{{{.Image}}}}|{{{{.State}}}}' 2>/dev/null | grep -i cloudflare
echo "{SEP}END"
"""

    def _discos(self, texto: str) -> list:
        montagens: list = []
        for linha in (texto or "").strip().splitlines()[1:]:
            partes = linha.split()
            if len(partes) < 6:
                continue
            try:
                total, usado, livre = int(partes[1]), int(partes[2]), int(partes[3])
            except ValueError:
                continue
            montagens.append({
                "ponto": " ".join(partes[5:]),
                "total_bytes": total,
                "usado_bytes": usado,
                "livre_bytes": livre,
                "percentual": round(usado / total * 100, 1) if total else 0.0,
            })
        return montagens
