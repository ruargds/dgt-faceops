"""
Coleta de RAM, GPU, disco e carga — direto da máquina, sem Zabbix.

A coleta é **sob demanda** (botão "Atualizar" na UI). Não há polling em
segundo plano: o painel roda no vm-appserver, junto com o FindFace, e
ficar batendo SSH de minuto em minuto só rouba CPU de quem importa.

Toda a leitura sai de UMA execução remota. Quatro hosts vezes seis
comandos daria 24 handshakes; assim é um por host. O script remoto emite
seções delimitadas em vez de montar JSON no bash — gerar JSON com `echo`
quebra no primeiro valor com aspas ou acento.
"""
import json
import re
import shlex

from app.services.ssh_service import CommandResult, SSHError, SSHService

# Marcador de seção. Improvável de aparecer na saída de qualquer comando.
SEP = "###FACEOPS:"

COLLECT_SCRIPT = r"""
set +e
# CPU de verdade precisa de DUAS leituras de /proc/stat e da diferença
# entre elas — /proc/stat é contador acumulado desde o boot, não taxa.
# A primeira sai aqui, a segunda no fim do script: a janela é o próprio
# tempo da coleta (segundos, na prática), sem pagar sleep por isso. O
# sleep no fim é só o piso, para o caso de a máquina não ter docker e o
# script voar.
echo "###FACEOPS:STAT1"
grep '^cpu' /proc/stat 2>/dev/null
echo "###FACEOPS:UPTIME"
cat /proc/uptime 2>/dev/null
echo "###FACEOPS:LOADAVG"
cat /proc/loadavg 2>/dev/null
echo "###FACEOPS:NPROC"
nproc 2>/dev/null
echo "###FACEOPS:MEMINFO"
cat /proc/meminfo 2>/dev/null
echo "###FACEOPS:DISK"
df -B1 -P -x tmpfs -x devtmpfs -x overlay -x squashfs 2>/dev/null
echo "###FACEOPS:DISKINODE"
df -i -P -x tmpfs -x devtmpfs -x overlay -x squashfs 2>/dev/null
echo "###FACEOPS:GPU"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit \
             --format=csv,noheader,nounits 2>/dev/null
fi
echo "###FACEOPS:GPUPROC"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits 2>/dev/null
fi
echo "###FACEOPS:DOCKERSTATS"
if command -v docker >/dev/null 2>&1; then
  timeout 25 docker stats --no-stream --format '{{json .}}' 2>/dev/null
fi
echo "###FACEOPS:SWAP"
cat /proc/swaps 2>/dev/null
sleep 0.3
echo "###FACEOPS:STAT2"
grep '^cpu' /proc/stat 2>/dev/null
echo "###FACEOPS:END"
"""


def _split_sections(saida: str) -> dict[str, str]:
    """Quebra a saída do script nas seções delimitadas."""
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


def _parse_stat(texto: str) -> dict[str, list[int]]:
    """
    Linhas `cpu`/`cpuN` de /proc/stat viram listas de contadores.

    Campos, em ordem: user, nice, system, idle, iowait, irq, softirq,
    steal, guest, guest_nice. Instalação antiga tem menos campos — o
    parser aceita o que vier e o cálculo soma o que existe.
    """
    saida: dict[str, list[int]] = {}
    for linha in texto.splitlines():
        partes = linha.split()
        if len(partes) < 5 or not partes[0].startswith("cpu"):
            continue
        try:
            saida[partes[0]] = [int(p) for p in partes[1:]]
        except ValueError:
            continue
    return saida


def _uso_cpu(antes: list[int], depois: list[int]) -> dict | None:
    """
    Ocupação entre duas leituras do MESMO cpu.

    `idle` e `iowait` não contam como uso: um processo esperando disco não
    está gastando CPU, e somar os dois é o erro que faz alguém trocar de
    servidor quando o problema era o disco. `steal` conta à parte — é CPU
    que o hipervisor tomou, e num Azure isso explica lentidão que não
    aparece em processo nenhum.
    """
    if not antes or not depois:
        return None
    n = min(len(antes), len(depois))
    delta = [depois[i] - antes[i] for i in range(n)]
    if any(d < 0 for d in delta):
        # Contador reiniciou (reboot entre as leituras). Sem número honesto.
        return None
    total = sum(delta)
    if total <= 0:
        return None

    def pct(indice: int) -> float:
        if indice >= n:
            return 0.0
        return round(delta[indice] / total * 100, 1)

    ocioso = pct(3) + pct(4)  # idle + iowait
    return {
        "uso_pct": round(max(0.0, 100.0 - ocioso), 1),
        "usuario": round(pct(0) + pct(1), 1),   # user + nice
        "sistema": round(pct(2) + pct(5) + pct(6), 1),  # system + irq + softirq
        "espera_io": pct(4),
        "roubado": pct(7),
        "ocioso": pct(3),
        "amostras_jiffies": total,
    }


def _parse_meminfo(texto: str) -> dict:
    """
    /proc/meminfo é a fonte certa — `free -h` já vem arredondado e muda
    de formato entre versões do procps.
    """
    campos: dict[str, int] = {}
    for linha in texto.splitlines():
        partes = linha.split(":")
        if len(partes) != 2:
            continue
        chave = partes[0].strip()
        valor = partes[1].strip().split()
        if not valor:
            continue
        try:
            # /proc/meminfo vem em kB
            campos[chave] = int(valor[0]) * 1024
        except ValueError:
            continue

    total = campos.get("MemTotal", 0)
    disponivel = campos.get("MemAvailable", 0)
    livre = campos.get("MemFree", 0)
    buffers = campos.get("Buffers", 0)
    cache = campos.get("Cached", 0)

    # "Usado" real = total - disponível. Contar buffers/cache como uso é o
    # erro clássico que faz o gestor achar que a máquina está estourando.
    usado = max(total - disponivel, 0) if total else 0

    swap_total = campos.get("SwapTotal", 0)
    swap_livre = campos.get("SwapFree", 0)

    return {
        "total_bytes": total,
        "disponivel_bytes": disponivel,
        "usado_bytes": usado,
        "livre_bytes": livre,
        "buffers_bytes": buffers,
        "cache_bytes": cache,
        "percentual": round(usado / total * 100, 1) if total else 0.0,
        "swap_total_bytes": swap_total,
        "swap_usado_bytes": max(swap_total - swap_livre, 0),
        "swap_percentual": (
            round((swap_total - swap_livre) / swap_total * 100, 1) if swap_total else 0.0
        ),
    }


def _parse_df(texto: str, inodes: bool = False) -> list[dict]:
    """Parseia `df -P`. O -P garante uma linha por sistema de arquivos."""
    montagens: list[dict] = []
    linhas = texto.strip().splitlines()
    for linha in linhas[1:]:  # pula o cabeçalho
        partes = linha.split()
        # df -P: Filesystem 1024-blocks Used Available Capacity Mounted-on
        # O ponto de montagem pode ter espaço; junta o resto.
        if len(partes) < 6:
            continue
        dispositivo = partes[0]
        try:
            total = int(partes[1])
            usado = int(partes[2])
            livre = int(partes[3])
        except ValueError:
            continue
        ponto = " ".join(partes[5:])

        item = {
            "dispositivo": dispositivo,
            "ponto": ponto,
            "percentual": round(usado / total * 100, 1) if total else 0.0,
        }
        if inodes:
            item.update({"inodes_total": total, "inodes_usado": usado, "inodes_livre": livre})
        else:
            item.update({"total_bytes": total, "usado_bytes": usado, "livre_bytes": livre})
        montagens.append(item)
    return montagens


def _to_float(valor: str) -> float | None:
    valor = valor.strip()
    if not valor or valor.upper() in {"N/A", "[N/A]", "[NOT SUPPORTED]"}:
        return None
    try:
        return float(valor)
    except ValueError:
        return None


def _parse_gpu(texto: str) -> list[dict]:
    """
    Parseia o CSV do nvidia-smi.

    Campos podem vir "[N/A]" em GPU virtualizada do Azure (NV-series com
    GRID), então nada aqui pode assumir número.
    """
    gpus: list[dict] = []
    for linha in texto.strip().splitlines():
        if not linha.strip():
            continue
        partes = [p.strip() for p in linha.split(",")]
        if len(partes) < 6:
            continue
        mem_usada = _to_float(partes[3])
        mem_total = _to_float(partes[4])
        gpus.append({
            "indice": partes[0],
            "nome": partes[1],
            "utilizacao_pct": _to_float(partes[2]),
            # nvidia-smi devolve MiB
            "memoria_usada_bytes": int(mem_usada * 1024 * 1024) if mem_usada else 0,
            "memoria_total_bytes": int(mem_total * 1024 * 1024) if mem_total else 0,
            "memoria_pct": (
                round(mem_usada / mem_total * 100, 1) if mem_usada and mem_total else 0.0
            ),
            "temperatura_c": _to_float(partes[5]),
            "potencia_w": _to_float(partes[6]) if len(partes) > 6 else None,
            "potencia_limite_w": _to_float(partes[7]) if len(partes) > 7 else None,
        })
    return gpus


def _parse_gpu_procs(texto: str) -> list[dict]:
    procs: list[dict] = []
    for linha in texto.strip().splitlines():
        if not linha.strip():
            continue
        partes = [p.strip() for p in linha.split(",")]
        if len(partes) < 3:
            continue
        mem = _to_float(partes[2])
        procs.append({
            "pid": partes[0],
            "processo": partes[1],
            "memoria_bytes": int(mem * 1024 * 1024) if mem else 0,
        })
    return procs


_PCT = re.compile(r"([\d.]+)\s*%")


def _parse_size(texto: str) -> int:
    """
    Converte o formato do `docker stats` ("1.5GiB", "512MiB", "0B").
    """
    texto = texto.strip()
    m = re.match(r"^([\d.]+)\s*([KMGTP]?i?B)$", texto, re.IGNORECASE)
    if not m:
        return 0
    valor = float(m.group(1))
    unidade = m.group(2).upper().replace("I", "").replace("B", "")
    fatores = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}
    return int(valor * fatores.get(unidade, 1))


def _parse_docker_stats(texto: str) -> list[dict]:
    """Uma linha JSON por container (docker stats --format '{{json .}}')."""
    containers: list[dict] = []
    for linha in texto.strip().splitlines():
        linha = linha.strip()
        if not linha.startswith("{"):
            continue
        try:
            bruto = json.loads(linha)
        except json.JSONDecodeError:
            continue

        mem_uso = bruto.get("MemUsage", "")
        usado_txt, _, limite_txt = mem_uso.partition("/")
        cpu = _PCT.search(bruto.get("CPUPerc", "") or "")
        mem_pct = _PCT.search(bruto.get("MemPerc", "") or "")

        containers.append({
            "nome": bruto.get("Name", ""),
            "id": bruto.get("ID", ""),
            "cpu_pct": float(cpu.group(1)) if cpu else 0.0,
            "memoria_bytes": _parse_size(usado_txt),
            "memoria_limite_bytes": _parse_size(limite_txt),
            "memoria_pct": float(mem_pct.group(1)) if mem_pct else 0.0,
            "pids": bruto.get("PIDs", ""),
        })
    containers.sort(key=lambda c: c["memoria_bytes"], reverse=True)
    return containers


class MetricsService:
    def __init__(self, ssh: SSHService) -> None:
        self.ssh = ssh

    async def collect(self, host) -> dict:
        """
        Coleta o retrato atual do host. Uma execução SSH, sem sudo —
        tudo aqui é legível por usuário comum (docker exige o grupo
        `docker`, que o usuário de deploy já tem).
        """
        # Se o docker deste host exige sudo (usuário fora do grupo `docker`,
        # que é o padrão da instalação do FindFace), a coleta inteira roda
        # como root. Ler /proc, df e nvidia-smi como root não muda nada;
        # sem isso a seção de containers viria vazia, sem erro visível.
        precisa_sudo = await self.ssh.docker_needs_sudo(host)

        resultado: CommandResult = await self.ssh.run_script(
            host, COLLECT_SCRIPT, sudo=precisa_sudo, timeout=60
        )
        if not resultado.stdout.strip():
            raise SSHError(
                f"coleta vazia em '{host.name}'. stderr: {resultado.stderr[:400]}"
            )

        secoes = _split_sections(resultado.stdout)

        uptime_s = 0.0
        if secoes.get("UPTIME", "").strip():
            try:
                uptime_s = float(secoes["UPTIME"].split()[0])
            except (ValueError, IndexError):
                uptime_s = 0.0

        carga = [0.0, 0.0, 0.0]
        if secoes.get("LOADAVG", "").strip():
            partes = secoes["LOADAVG"].split()
            try:
                carga = [float(p) for p in partes[:3]]
            except ValueError:
                pass

        try:
            nucleos = int(secoes.get("NPROC", "0").strip() or 0)
        except ValueError:
            nucleos = 0

        gpus = _parse_gpu(secoes.get("GPU", ""))
        discos = _parse_df(secoes.get("DISK", ""))

        # Uso real de CPU. Carga e uso respondem perguntas diferentes:
        # carga é fila (quantos querem CPU), uso é ocupação (quanto da CPU
        # foi gasta). Uma máquina pode estar 100% ocupada com carga 1,0, e
        # pode estar com carga 4,0 gastando 20% de CPU esperando disco.
        s1 = _parse_stat(secoes.get("STAT1", ""))
        s2 = _parse_stat(secoes.get("STAT2", ""))
        uso = _uso_cpu(s1.get("cpu", []), s2.get("cpu", []))
        por_nucleo = []
        for nome in sorted(k for k in s1 if k != "cpu"):
            n = _uso_cpu(s1.get(nome, []), s2.get(nome, []))
            if n is not None:
                por_nucleo.append({"nucleo": nome.replace("cpu", ""), "uso_pct": n["uso_pct"]})

        return {
            "host_id": host.id,
            "host": host.name,
            "coletado_em": None,  # preenchido pela rota, com o fuso do painel
            "uptime_segundos": int(uptime_s),
            "cpu": {
                "nucleos": nucleos,
                # None quando a leitura não deu para calcular (contador
                # reiniciado, janela nula). A tela mostra "—" em vez de
                # inventar zero.
                "uso_pct": uso["uso_pct"] if uso else None,
                "detalhe": uso,
                "por_nucleo": por_nucleo,
                "carga_1min": carga[0],
                "carga_5min": carga[1],
                "carga_15min": carga[2],
                # Carga por núcleo é o número que realmente diz se está apertado
                "carga_por_nucleo": round(carga[0] / nucleos, 2) if nucleos else 0.0,
            },
            "memoria": _parse_meminfo(secoes.get("MEMINFO", "")),
            "discos": discos,
            "inodes": _parse_df(secoes.get("DISKINODE", ""), inodes=True),
            "gpus": gpus,
            "gpu_processos": _parse_gpu_procs(secoes.get("GPUPROC", "")),
            "containers": _parse_docker_stats(secoes.get("DOCKERSTATS", "")),
            "tem_gpu": bool(gpus),
            "coleta_ms": resultado.duration_ms,
        }

    async def storage_breakdown(self, host, base_dir: str | None = None) -> dict:
        """
        Onde o disco do FindFace está sendo gasto.

        `du` numa árvore com milhões de fotos de evento é caro, então isso
        é ação separada (nunca entra na coleta do botão Atualizar) e tem
        timeout curto. Profundidade 1 basta para apontar o vilão.
        """
        base = base_dir or host.ffmulti_dir or "/opt/findface-multi"
        base_q = shlex.quote(base)

        script = f"""
set +e
echo "{SEP}BASE"
echo {base_q}
echo "{SEP}TOTAL"
timeout 240 du -sb {base_q} 2>/dev/null
echo "{SEP}NIVEL1"
timeout 240 du -b --max-depth=2 {base_q}/data 2>/dev/null | sort -rn | head -40
echo "{SEP}MOUNT"
df -B1 -P {base_q} 2>/dev/null
echo "{SEP}END"
"""
        resultado = await self.ssh.run_script(host, script, sudo=True, timeout=300)
        secoes = _split_sections(resultado.stdout)

        def _primeiro_tamanho(txt: str) -> int:
            linha = txt.strip().splitlines()
            if not linha:
                return 0
            try:
                return int(linha[0].split()[0])
            except (ValueError, IndexError):
                return 0

        itens: list[dict] = []
        for linha in secoes.get("NIVEL1", "").strip().splitlines():
            partes = linha.split(None, 1)
            if len(partes) != 2:
                continue
            try:
                itens.append({"caminho": partes[1].strip(), "bytes": int(partes[0])})
            except ValueError:
                continue

        return {
            "base": base,
            "total_bytes": _primeiro_tamanho(secoes.get("TOTAL", "")),
            "itens": itens,
            "montagem": _parse_df(secoes.get("MOUNT", "")),
            "parcial": resultado.exit_status != 0,
        }
