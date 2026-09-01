"""
Processos ao vivo — um "htop" explicado, pela web.

O InTerminal já deixa rodar `htop` de verdade, mas quem opera no N1 não
lê htop: as colunas são crípticas (VIRT, RES, NI, PR) e a tela pisca
rápido demais. Aqui a mesma informação vem traduzida — "quanto a máquina
está ocupada", "quem come CPU", "quem come memória" — e o front explica
cada número em português.

Como o dado é medido, e por que assim:

* `top -bn2 -d 0.5` roda DUAS iterações com meio segundo entre elas e o
  Python usa só a SEGUNDA. A primeira iteração do top reporta %CPU desde
  o boot (inútil como "agora"); a segunda mede o intervalo real — é o
  %CPU instantâneo que o htop mostra.
* Memória vem de `free -b` (bytes exatos), não da linha do top, que muda
  de unidade (MiB/KiB) entre versões e daria número torto.
* Sob demanda: o front chama a cada poucos segundos enquanto a tela está
  aberta, e para quando ela perde o foco. Não entra no coletor contínuo.
"""
import logging

from app.services.ssh_service import SSHService

log = logging.getLogger("faceops.processos")

SEP = "###FACEOPS:"


class ProcessosError(Exception):
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


class ProcessosService:
    def __init__(self, ssh: SSHService) -> None:
        self.ssh = ssh

    async def snapshot(self, host, limite: int = 25) -> dict:
        script = f"""
set +e
echo "{SEP}LOAD"
cat /proc/loadavg 2>/dev/null
echo "{SEP}NPROC"
nproc 2>/dev/null
echo "{SEP}MEM"
free -b 2>/dev/null
echo "{SEP}TOP"
COLUMNS=220 top -bn2 -d 0.5 2>/dev/null
echo "{SEP}GPUPROC"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits 2>/dev/null
fi
echo "{SEP}CGROUP"
# Container dono de cada processo, lido do cgroup — barato porque é
# arquivo local. `docker top` por container custaria uma execução por
# container, e aqui já estamos dentro de uma leitura só.
for p in $(ls -1 /proc 2>/dev/null | grep -E '^[0-9]+$' | head -400); do
  linha="$(tr '\\0' ' ' < /proc/$p/cgroup 2>/dev/null | grep -oE '(docker[-/][0-9a-f]{{12}}|/docker/[0-9a-f]{{12}})' | head -1)"
  [ -n "$linha" ] && echo "$p|$linha"
done
echo "{SEP}CTNAMES"
if command -v docker >/dev/null 2>&1; then
  docker ps --no-trunc --format '{{{{.ID}}}}|{{{{.Names}}}}' 2>/dev/null
fi
echo "{SEP}END"
"""
        r = await self.ssh.run_script(host, script, timeout=45)
        s = _split(r.stdout)

        nucleos = self._int(s.get("NPROC", ""), 1) or 1
        load = self._load(s.get("LOAD", ""))
        mem, swap = self._free(s.get("MEM", ""))
        cpu_pct, cpu_det, tarefas, processos = self._top(s.get("TOP", ""), limite)

        # GPU por processo: MESMA leitura que a tela de Recursos já fazia,
        # com o MESMO parser (`metrics_service._parse_gpu_procs`). O dado
        # existia e não aparecia aqui — quem está olhando "quem consome a
        # máquina" precisa ver a placa junto, não em outra tela.
        from app.services.metrics_service import _parse_gpu_procs

        gpu_por_pid = {
            p["pid"]: p["memoria_bytes"]
            for p in _parse_gpu_procs(s.get("GPUPROC", ""))
        }

        # Container dono de cada processo, para o botão de reinício agir no
        # container (cercado) em vez de matar PID solto.
        curto_por_pid: dict[str, str] = {}
        for linha in s.get("CGROUP", "").strip().splitlines():
            if "|" not in linha:
                continue
            pid, bruto = linha.split("|", 1)
            achado = bruto.strip().rsplit("/", 1)[-1].replace("docker-", "")
            if achado:
                curto_por_pid[pid.strip()] = achado[:12]

        nome_por_curto: dict[str, str] = {}
        for linha in s.get("CTNAMES", "").strip().splitlines():
            if "|" not in linha:
                continue
            cid, nome = linha.split("|", 1)
            nome_por_curto[cid.strip()[:12]] = nome.strip()

        for p in processos:
            pid = str(p.get("pid", ""))
            p["gpu_bytes"] = gpu_por_pid.get(pid, 0)
            p["container"] = nome_por_curto.get(curto_por_pid.get(pid, ""), "")

        return {
            "host": host.name,
            "tem_gpu": bool(gpu_por_pid),
            "nucleos": nucleos,
            "load": load,
            # Carga por núcleo: load/nprocs. >1 = há processo esperando a vez.
            "load_por_nucleo": round(load[0] / nucleos, 2) if load else 0.0,
            "cpu_pct": cpu_pct,
            "cpu_detalhe": cpu_det,
            "tarefas": tarefas,
            "mem": mem,
            "swap": swap,
            "processos": processos,
        }

    # ── parsers ─────────────────────────────────────────────────────────

    def _int(self, texto: str, padrao: int = 0) -> int:
        try:
            return int((texto or "").strip().split()[0])
        except (ValueError, IndexError):
            return padrao

    def _load(self, texto: str) -> list:
        partes = (texto or "").strip().split()
        try:
            return [float(partes[0]), float(partes[1]), float(partes[2])]
        except (ValueError, IndexError):
            return [0.0, 0.0, 0.0]

    def _free(self, texto: str):
        """`free -b` → (mem, swap) em bytes."""
        mem = {"total": 0, "usado": 0, "livre": 0, "cache": 0, "disponivel": 0, "pct": 0.0}
        swap = {"total": 0, "usado": 0, "livre": 0, "pct": 0.0}
        for linha in (texto or "").splitlines():
            campos = linha.split()
            if not campos:
                continue
            rot = campos[0].lower().rstrip(":")
            nums = []
            for c in campos[1:]:
                try:
                    nums.append(int(c))
                except ValueError:
                    nums.append(0)
            if rot in ("mem",) and len(nums) >= 3:
                mem["total"] = nums[0]
                mem["usado"] = nums[1]
                mem["livre"] = nums[2]
                # available é a última coluna no free moderno; cache = buff/cache
                if len(nums) >= 6:
                    mem["cache"] = nums[4]
                    mem["disponivel"] = nums[5]
                mem["pct"] = round(mem["usado"] / mem["total"] * 100, 1) if mem["total"] else 0.0
            elif rot == "swap" and len(nums) >= 3:
                swap["total"] = nums[0]
                swap["usado"] = nums[1]
                swap["livre"] = nums[2]
                swap["pct"] = round(swap["usado"] / swap["total"] * 100, 1) if swap["total"] else 0.0
        return mem, swap

    def _top(self, texto: str, limite: int):
        """Usa a SEGUNDA iteração do `top -bn2`."""
        linhas = (texto or "").splitlines()
        # Cada frame começa com "top - ". Pega o índice do último frame.
        inicios = [i for i, l in enumerate(linhas) if l.startswith("top - ")]
        frame = linhas[inicios[-1]:] if inicios else linhas

        cpu_pct = 0.0
        cpu_det: dict = {}
        tarefas: dict = {}
        proc_inicio = None

        for i, l in enumerate(frame):
            ls = l.strip()
            if ls.startswith("%Cpu") or ls.startswith("Cpu(s)"):
                cpu_det = self._cpu_linha(ls)
                idle = cpu_det.get("id", 0.0)
                cpu_pct = round(max(0.0, 100.0 - idle), 1)
            elif ls.startswith("Tasks:") or ls.startswith("Threads:"):
                tarefas = self._tarefas(ls)
            elif "PID" in l and ("%CPU" in l or "COMMAND" in l):
                proc_inicio = i + 1
                break

        processos: list = []
        if proc_inicio is not None:
            for l in frame[proc_inicio:]:
                if not l.strip():
                    continue
                p = self._proc_linha(l)
                if p:
                    processos.append(p)
                if len(processos) >= limite:
                    break
        return cpu_pct, cpu_det, tarefas, processos

    def _cpu_linha(self, ls: str) -> dict:
        # "%Cpu(s):  3.2 us,  1.1 sy,  0.0 ni, 95.5 id,  0.1 wa, ..."
        corpo = ls.split(":", 1)[1] if ":" in ls else ls
        det: dict = {}
        for parte in corpo.split(","):
            parte = parte.strip()
            campos = parte.split()
            if len(campos) >= 2:
                try:
                    det[campos[1]] = float(campos[0].replace(",", "."))
                except ValueError:
                    pass
        return det

    def _tarefas(self, ls: str) -> dict:
        # "Tasks: 245 total,   1 running, 244 sleeping,   0 stopped,   0 zombie"
        corpo = ls.split(":", 1)[1] if ":" in ls else ls
        det: dict = {}
        mapa = {
            "total": "total", "running": "rodando", "sleeping": "dormindo",
            "stopped": "parado", "zombie": "zumbi",
        }
        for parte in corpo.split(","):
            campos = parte.strip().split()
            if len(campos) >= 2 and campos[1] in mapa:
                try:
                    det[mapa[campos[1]]] = int(campos[0])
                except ValueError:
                    pass
        return det

    def _proc_linha(self, l: str):
        # PID USER PR NI VIRT RES SHR S %CPU %MEM TIME+ COMMAND
        campos = l.split(None, 11)
        if len(campos) < 12:
            return None
        try:
            return {
                "pid": int(campos[0]),
                "usuario": campos[1],
                "estado": campos[7],
                "cpu": float(campos[8].replace(",", ".")),
                "mem": float(campos[9].replace(",", ".")),
                "tempo": campos[10],
                "comando": campos[11].strip(),
            }
        except (ValueError, IndexError):
            return None
