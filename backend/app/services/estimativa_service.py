"""
Quanto vai ocupar, antes de disparar.

A pergunta certa antes de um backup é "cabe?", e ela era respondida
descobrindo. O perfil completo num servidor de reconhecimento facial pode
passar de centenas de GB — descobrir isso no meio da cópia significa disco
cheio em produção, que é justamente o incidente que este painel existe para
evitar.

Duas fontes, e a segunda vale mais que a primeira:

1. **Medição no servidor.** Tamanho de `configs/`, do diretório de dados e
   dos bancos, mais o espaço livre onde o artefato é montado.
2. **Histórico real.** O tamanho que as execuções anteriores DAQUELE perfil
   naquele servidor realmente produziram. Nenhuma estimativa de compressão
   ganha de um número observado.

Sobre não onerar: uma execução SSH, com `du` limitado por `timeout`. `du`
numa árvore com milhões de fotos de evento é caro — se não terminar no
prazo, a resposta é "não medido", e não um número inventado.
"""
import logging
import re
import shlex

from app.services.ssh_service import SSHError, SSHService

log = logging.getLogger("faceops.estimativa")

SEP = "###FACEOPS:"

# `du` na árvore de dados pode levar minutos num servidor real. O teto
# existe para a tela responder rápido; estourar o teto é informação, não
# falha.
SEGUNDOS_DU = 25


class EstimativaError(Exception):
    pass


def _secoes(saida: str) -> dict[str, str]:
    secoes: dict[str, str] = {}
    atual = None
    buf: list[str] = []
    for linha in saida.splitlines():
        if linha.startswith(SEP):
            if atual is not None:
                secoes[atual] = "\n".join(buf).strip()
            atual = linha[len(SEP):].strip()
            buf = []
        elif atual is not None:
            buf.append(linha)
    if atual is not None:
        secoes[atual] = "\n".join(buf).strip()
    return secoes


def _bytes(texto: str) -> int | None:
    """Primeiro número da saída do `du -sb` / `df -B1`."""
    m = re.search(r"\d+", texto or "")
    return int(m.group(0)) if m else None


class EstimativaService:
    def __init__(self, ssh: SSHService) -> None:
        self.ssh = ssh

    async def medir(self, host, stack, staging: str) -> dict:
        ff_dir = host.ffmulti_dir or "/opt/findface-multi"
        q_ff = shlex.quote(ff_dir)
        q_staging = shlex.quote(staging)

        script = f"""
set +e
echo "{SEP}CONFIGS"
timeout {SEGUNDOS_DU} du -sb {q_ff}/configs 2>/dev/null
echo "{SEP}DATA"
timeout {SEGUNDOS_DU} du -sb {q_ff}/data 2>/dev/null
echo "{SEP}LIVRE_FF"
df -B1 --output=avail {q_ff} 2>/dev/null | tail -1
echo "{SEP}LIVRE_STAGING"
mkdir -p {q_staging} 2>/dev/null
df -B1 --output=avail {q_staging} 2>/dev/null | tail -1
echo "{SEP}BANCOS"
for c in $(docker ps --format '{{{{.Names}}}}' 2>/dev/null | grep -i -E 'postgres|timescale' | head -4); do
  soma="$(docker exec "$c" psql -U ffsecurity -tA -c \\
    "SELECT COALESCE(SUM(pg_database_size(datname)),0) FROM pg_database WHERE datistemplate = false" 2>/dev/null)"
  [ -n "$soma" ] && echo "$c|$soma"
done
echo "{SEP}TARANTOOL"
timeout {SEGUNDOS_DU} du -sb {q_ff}/data/findface-tarantool-server 2>/dev/null
echo "{SEP}FIM"
"""

        try:
            r = await self.ssh.run_script(host, script, sudo=True, timeout=180)
        except SSHError as exc:
            raise EstimativaError(f"não consegui medir em '{host.name}': {exc}") from exc

        s = _secoes(r.stdout or "")

        configs = _bytes(s.get("CONFIGS", ""))
        data = _bytes(s.get("DATA", ""))
        tarantool = _bytes(s.get("TARANTOOL", ""))
        livre_ff = _bytes(s.get("LIVRE_FF", ""))
        livre_staging = _bytes(s.get("LIVRE_STAGING", ""))

        bancos = 0
        instancias = []
        for linha in (s.get("BANCOS") or "").splitlines():
            if "|" not in linha:
                continue
            nome, _, valor = linha.partition("|")
            tamanho = _bytes(valor)
            if tamanho:
                bancos += tamanho
                instancias.append({"instancia": nome.strip(), "bytes": tamanho})

        return {
            "ff_dir": ff_dir,
            "configs_bytes": configs,
            "data_bytes": data,
            "tarantool_bytes": tarantool,
            "bancos_bytes": bancos or None,
            "instancias": instancias,
            "livre_no_servidor": livre_ff,
            "livre_no_staging": livre_staging,
            "du_expirou": data is None,
            "medido_ms": r.duration_ms,
        }

    @staticmethod
    def estimar(medicao: dict, historico: dict) -> list[dict]:
        """
        Junta medição e histórico num número por perfil.

        A compressão de `configs/` e de dump de banco fica tipicamente entre
        um terço e a metade do tamanho cru — mas onde existe execução
        anterior do mesmo perfil no mesmo servidor, o tamanho REAL dela
        substitui a conta. Número observado ganha de fator estimado, sempre.
        """
        configs = medicao.get("configs_bytes") or 0
        bancos = medicao.get("bancos_bytes") or 0
        tarantool = medicao.get("tarantool_bytes") or 0
        data = medicao.get("data_bytes")

        # Fator conservador: assume compressão fraca, para o aviso de
        # espaço errar para o lado seguro.
        cru = {
            "config": configs,
            "essencial": configs + bancos + tarantool,
            "completo": configs + (data or 0),
        }
        fator = {"config": 0.5, "essencial": 0.5, "completo": 0.9}

        saida = []
        for perfil in ("config", "essencial", "completo"):
            real = historico.get(perfil)
            bruto = cru[perfil]
            estimado = int(bruto * fator[perfil]) if bruto else None
            incerto = perfil == "completo" and data is None
            saida.append({
                "perfil": perfil,
                # Incerto e sem historico = SEM numero. Mostrar 63 KB para o
                # perfil completo, so porque o `du` do diretorio de dados
                # nao terminou, seria pior que nao mostrar nada: alguem
                # dispararia achando que cabe.
                "estimado_bytes": real or (None if incerto else estimado),
                "origem": "execução anterior" if real else "medição do servidor",
                "cru_bytes": bruto or None,
                "ultima_execucao_bytes": real,
                "incerto": incerto,
                "observacao": (
                    "o diretório de dados não terminou de ser medido no prazo — "
                    "num servidor com milhões de fotos de evento isso é normal, "
                    "e o perfil completo pode passar de centenas de GB"
                    if incerto
                    else ""
                ),
            })
        return saida
