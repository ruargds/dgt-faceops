"""
Dispositivos (câmeras) do FindFace — quantos, quando falaram, quanto geram.

Lê direto do PostgreSQL do FindFace, via a mesma ponte SSH+docker do
resto do painel. A API HTTP da NtechLab seria a via "oficial", mas exige
credencial própria e a documentação pública não fixa os caminhos dos
endpoints — construir em cima disso seria construir sobre suposição.

**O esquema é descoberto em tempo de execução.** Nomes de tabela e coluna
mudam entre versões do FindFace, e uma consulta com nome chutado falha
inteira. Aqui o serviço pergunta ao `information_schema` o que existe,
casa com os padrões conhecidos, e diz claramente quando não encontrou.

Sobre não onerar: contar evento em tabela grande custa. Por isso a
contagem é **sob demanda**, com janela de tempo limitada e usando a
coluna de data — que é indexada em qualquer instalação sã. Nada disso
entra no coletor contínuo.
"""
import logging
import re

from app.core.config import settings
from app.services.ssh_service import SSHError, SSHService

log = logging.getLogger("faceops.dispositivos")

SEP = "###FACEOPS:"

# Padrões de nome observados nas versões 2.x. A descoberta casa por
# padrão, então uma instalação com prefixo diferente ainda funciona.
PADRAO_CAMERA = re.compile(r"(^|_)cameras?$")
PADRAO_GRUPO = re.compile(r"camera_?group")
PADRAO_EVENTO = re.compile(r"(face|body|car)_?events?$")

# Tabelas do banco do PRÓPRIO painel. Se a busca cair nele (acontece
# quando o painel roda no mesmo servidor), reconhecemos e recusamos com
# mensagem clara — em vez de listar "amostras, hosts, users..." como se
# fossem tabelas do FindFace.
TABELAS_PAINEL = frozenset({
    "amostras", "audit_logs", "backup_runs", "configuracoes", "destinos",
    "hosts", "schedules", "terminal_sessions", "users", "visoes_log",
})

PERIODOS = {
    "hora": ("1 hour", "última hora"),
    "dia": ("24 hours", "últimas 24 horas"),
    "semana": ("7 days", "últimos 7 dias"),
    "mes": ("30 days", "últimos 30 dias"),
}


class DispositivosError(Exception):
    pass


def _split(saida: str) -> dict[str, str]:
    secoes: dict[str, str] = {}
    atual: str | None = None
    buf: list[str] = []
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


def _linhas(texto: str) -> list[list[str]]:
    """Saída do psql com `-F'|'` vira lista de campos."""
    saida = []
    for linha in texto.strip().splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("("):
            continue
        saida.append([c.strip() for c in linha.split("|")])
    return saida


class DispositivosService:
    def __init__(self, ssh: SSHService, ffapi=None) -> None:
        self.ssh = ssh
        # Cliente da API HTTP do FindFace, preferido quando o host tem
        # URL e token cadastrados — mais limpo que ler o Postgres via SSH.
        self.ffapi = ffapi
        # host_id -> esquema descoberto
        self._esquema: dict[int, dict] = {}

    # ── Descoberta ─────────────────────────────────────────────────────

    async def _container_pg(self, host, stack) -> str:
        """
        Acha o container do PostgreSQL — SEM exigir que os rótulos batam.

        O filtro por `project=X` + `service=postgresql` é frágil: numa
        instalação distribuída o serviço tem outro nome (timescaledb,
        pgbouncer na frente), o projeto detectado pode divergir, e o
        container pode nem ter rótulo de compose. Aqui tentamos do mais
        específico ao mais genérico, e só desistimos depois de procurar
        de verdade — em vez de dizer "não achei" no primeiro filtro.
        """
        sudo = await self.ssh.docker_needs_sudo(host)

        try:
            projeto = await stack._projeto(host)
        except Exception:  # noqa: BLE001 - deteccao de projeto e best-effort
            projeto = ""

        tentativas: list[str] = []
        if projeto:
            tentativas.append(
                f"docker ps --filter label=com.docker.compose.project={projeto} "
                "--filter label=com.docker.compose.service=postgresql "
                "--format '{{.Names}}' | head -1"
            )
            tentativas.append(
                f"docker ps --filter label=com.docker.compose.project={projeto} "
                "--format '{{.Names}} {{.Image}}' "
                "| grep -iE 'postgres|pgsql|timescale' | awk '{print $1}' | head -1"
            )
        # Qualquer container cujo nome OU imagem aponte para Postgres
        tentativas.append(
            "docker ps --format '{{.Names}} {{.Image}}' "
            "| grep -iE 'postgres|pgsql|timescale' | awk '{print $1}' | head -1"
        )
        # Ultimo recurso: um container que responda a pg_isready por dentro
        tentativas.append(
            "for c in $(docker ps --format '{{.Names}}'); do "
            "docker exec \"$c\" sh -c 'command -v pg_isready >/dev/null 2>&1 "
            "&& pg_isready -q' >/dev/null 2>&1 && { echo \"$c\"; break; }; done"
        )

        for comando in tentativas:
            r = await self.ssh.run(host, comando, sudo=sudo, timeout=60)
            saida = (r.stdout or "").strip()
            if saida:
                return saida.splitlines()[0].strip()

        raise DispositivosError(
            "PostgreSQL não encontrado neste servidor. Numa instalação "
            "distribuída o banco fica em UM servidor só - abra a tela "
            "Descoberta para ver onde ele roda e consulte as câmeras lá."
        )

    async def descobrir(self, host, stack, forcar: bool = False) -> dict:
        """
        Descobre banco, tabelas e colunas. Guardado em memória por host.

        Perguntar ao banco o que existe, em vez de assumir, é o que faz
        isso sobreviver a uma atualização do FindFace.
        """
        if not forcar and host.id in self._esquema:
            return self._esquema[host.id]

        container = await self._container_pg(host, stack)
        sudo = await self.ssh.docker_needs_sudo(host)

        script = f"""
set +e
C={container}
PGUSER=$(docker exec $C sh -c 'echo -n "$POSTGRES_USER"' 2>/dev/null)
[ -z "$PGUSER" ] && PGUSER=postgres

echo "{SEP}USUARIO"
echo "$PGUSER"

echo "{SEP}BANCOS"
docker exec $C psql -U "$PGUSER" -tAc \\
  "SELECT datname FROM pg_database WHERE datistemplate=false AND datname<>'postgres'" 2>/dev/null

echo "{SEP}TABELAS"
for db in $(docker exec $C psql -U "$PGUSER" -tAc \\
    "SELECT datname FROM pg_database WHERE datistemplate=false AND datname<>'postgres'" 2>/dev/null); do
  docker exec $C psql -U "$PGUSER" -d "$db" -tAc \\
    "SELECT '$db|' || table_name FROM information_schema.tables
     WHERE table_schema='public'" 2>/dev/null
done

echo "{SEP}END"
"""
        r = await self.ssh.run_script(host, script, sudo=sudo, timeout=180)
        s = _split(r.stdout)

        usuario = (s.get("USUARIO", "") or "postgres").strip() or "postgres"
        banco_painel = (settings.POSTGRES_DB or "faceops").strip()

        todas: list[tuple[str, str]] = []
        for linha in s.get("TABELAS", "").strip().splitlines():
            if "|" in linha:
                db, tab = linha.split("|", 1)
                db, tab = db.strip(), tab.strip()
                # Nunca trata o banco do próprio painel como fonte de câmeras
                if db == banco_painel:
                    continue
                todas.append((db, tab))

        if not todas:
            # Provavelmente conectamos no Postgres do próprio painel (o
            # FindFace não roda aqui) ou o banco do FindFace está noutro
            # servidor.
            raise DispositivosError(
                "não encontrei o banco do FindFace neste servidor. Se o FindFace "
                "roda em outra máquina, consulte por lá (veja em Topologia onde o "
                "PostgreSQL está). Ou cadastre a API do FindFace (URL + token) "
                "deste servidor em Servidores — o painel prefere a API quando ela "
                "existe."
            )

        cameras = [(d, t) for d, t in todas if PADRAO_CAMERA.search(t)]
        grupos = [(d, t) for d, t in todas if PADRAO_GRUPO.search(t)]
        eventos = [(d, t) for d, t in todas if PADRAO_EVENTO.search(t)]

        if not cameras:
            tabelas = sorted({t for _, t in todas})
            # Se o que sobrou é só o esquema do painel, diz isso em vez de
            # despejar a lista de tabelas internas.
            if set(tabelas).issubset(TABELAS_PAINEL):
                raise DispositivosError(
                    "este é o banco do próprio painel, não o do FindFace. O "
                    "FindFace não roda neste servidor — consulte no servidor onde "
                    "ele está (veja em Topologia), ou cadastre a API do FindFace."
                )
            raise DispositivosError(
                "nenhuma tabela de câmeras encontrada. Cadastre a API do FindFace "
                "(URL + token) em Servidores, ou confira se este é mesmo o servidor "
                "do FindFace. Tabelas vistas: " + ", ".join(tabelas[:25])
            )

        # A base do FindFace costuma ser a que tem a tabela de câmeras
        banco = cameras[0][0]

        esquema = {
            "container": container,
            "usuario": usuario,
            "banco": banco,
            "tabela_cameras": cameras[0][1],
            "tabela_grupos": next((t for d, t in grupos if d == banco), ""),
            "tabelas_eventos": [t for d, t in eventos if d == banco],
            "total_tabelas": len(todas),
        }

        # Colunas da tabela de câmeras, para saber com o que contamos
        colunas = await self._colunas(
            host, sudo, container, usuario, banco, esquema["tabela_cameras"]
        )
        esquema["colunas_cameras"] = colunas

        self._esquema[host.id] = esquema
        log.info(
            "esquema descoberto em %s: banco=%s cameras=%s eventos=%s",
            host.name, banco, esquema["tabela_cameras"],
            esquema["tabelas_eventos"],
        )
        return esquema

    async def _colunas(self, host, sudo, container, usuario, banco, tabela) -> list[str]:
        r = await self.ssh.run(
            host,
            f"docker exec {container} psql -U {usuario} -d {banco} -tAc "
            f"\"SELECT column_name FROM information_schema.columns "
            f"WHERE table_schema='public' AND table_name='{tabela}'\"",
            sudo=sudo,
            timeout=60,
        )
        return [c.strip() for c in (r.stdout or "").splitlines() if c.strip()]

    # ── Consulta ───────────────────────────────────────────────────────

    async def listar(self, host, stack, periodo: str = "dia") -> dict:
        """
        Câmeras cadastradas, última comunicação e volume de eventos.

        A contagem usa a coluna de data com filtro de janela — em qualquer
        instalação sã ela é indexada, e o custo fica proporcional ao
        período, não ao tamanho da tabela.
        """
        if periodo not in PERIODOS:
            raise DispositivosError(f"período inválido: {periodo}")

        # Preferência: API HTTP do FindFace quando cadastrada. Cai para
        # SSH+psql se não houver credencial de API ou se a API falhar.
        if self.ffapi is not None:
            from app.services.ffapi_service import FFApiError, configurado
            if configurado(host):
                try:
                    return await self.ffapi.listar(host, periodo)
                except FFApiError as exc:
                    log.warning("API do FindFace falhou em %s, caindo para SSH: %s",
                                host.name, exc)

        esq = await self.descobrir(host, stack)
        intervalo, rotulo = PERIODOS[periodo]
        sudo = await self.ssh.docker_needs_sudo(host)

        cols = esq["colunas_cameras"]
        col_nome = next((c for c in ("name", "nome", "title") if c in cols), "id")
        col_ativo = next(
            (c for c in ("active", "enabled", "is_active", "ativo") if c in cols), ""
        )
        col_grupo = next(
            (c for c in ("group_id", "camera_group_id", "group") if c in cols), ""
        )

        sel = [f"c.id", f"c.{col_nome} AS nome"]
        sel.append(f"c.{col_ativo}::text AS ativo" if col_ativo else "'?' AS ativo")
        sel.append(f"c.{col_grupo}::text AS grupo" if col_grupo else "'' AS grupo")

        # Uma consulta por tabela de evento: elas têm colunas diferentes e
        # unir tudo num UNION exigiria assumir esquema igual.
        blocos = []
        for tabela in esq["tabelas_eventos"]:
            blocos.append(f"""
echo "{SEP}EVENTOS:{tabela}"
docker exec $C psql -U $U -d $D -tA -F'|' -c "
  SELECT camera_id,
         COUNT(*) AS total,
         MAX(created_date) AS ultimo
  FROM {tabela}
  WHERE created_date >= now() - interval '{intervalo}'
  GROUP BY camera_id
" 2>/dev/null

echo "{SEP}ULTIMO:{tabela}"
docker exec $C psql -U $U -d $D -tA -F'|' -c "
  SELECT camera_id, MAX(created_date)
  FROM {tabela}
  GROUP BY camera_id
" 2>/dev/null
""")

        script = f"""
set +e
C={esq["container"]}
U={esq["usuario"]}
D={esq["banco"]}

echo "{SEP}CAMERAS"
docker exec $C psql -U $U -d $D -tA -F'|' -c "
  SELECT {', '.join(sel)} FROM {esq["tabela_cameras"]} c ORDER BY 2
" 2>/dev/null

echo "{SEP}TAMANHO"
docker exec $C psql -U $U -d $D -tA -F'|' -c "
  SELECT relname, pg_total_relation_size(relid)
  FROM pg_catalog.pg_statio_user_tables
  ORDER BY pg_total_relation_size(relid) DESC LIMIT 12
" 2>/dev/null
{''.join(blocos)}
echo "{SEP}END"
"""
        r = await self.ssh.run_script(host, script, sudo=sudo, timeout=300)
        s = _split(r.stdout)

        # ── Câmeras ────────────────────────────────────────────────────
        cameras: dict[str, dict] = {}
        for campos in _linhas(s.get("CAMERAS", "")):
            if len(campos) < 4:
                continue
            cid = campos[0]
            cameras[cid] = {
                "id": cid,
                "nome": campos[1] or f"camera {cid}",
                "ativo": campos[2] in ("t", "true", "1", "?"),
                "ativo_conhecido": campos[2] != "?",
                "grupo": campos[3],
                "eventos": 0,
                "por_tipo": {},
                "ultimo_evento": None,
            }

        # ── Eventos no período e último de todos ───────────────────────
        for chave, texto in s.items():
            if chave.startswith("EVENTOS:"):
                tipo = chave.split(":", 1)[1]
                for campos in _linhas(texto):
                    if len(campos) < 2:
                        continue
                    cid, total = campos[0], campos[1]
                    cam = cameras.get(cid)
                    if cam is None:
                        continue
                    try:
                        n = int(total)
                    except ValueError:
                        continue
                    cam["eventos"] += n
                    cam["por_tipo"][tipo] = cam["por_tipo"].get(tipo, 0) + n

            elif chave.startswith("ULTIMO:"):
                for campos in _linhas(texto):
                    if len(campos) < 2:
                        continue
                    cid, quando = campos[0], campos[1]
                    cam = cameras.get(cid)
                    if cam is None or not quando:
                        continue
                    if cam["ultimo_evento"] is None or quando > cam["ultimo_evento"]:
                        cam["ultimo_evento"] = quando

        # ── Tamanho das tabelas ────────────────────────────────────────
        tabelas = []
        for campos in _linhas(s.get("TAMANHO", "")):
            if len(campos) < 2:
                continue
            try:
                tabelas.append({"tabela": campos[0], "bytes": int(campos[1])})
            except ValueError:
                continue

        lista = sorted(cameras.values(), key=lambda c: (-c["eventos"], c["nome"]))
        total_eventos = sum(c["eventos"] for c in lista)

        # Volume por câmera é ESTIMATIVA: rateio do tamanho das tabelas de
        # evento pela participação de cada uma. Medir de verdade exigiria
        # somar o tamanho de cada foto, e a árvore de uploads não é
        # organizada por câmera.
        bytes_eventos = sum(
            t["bytes"] for t in tabelas
            if any(PADRAO_EVENTO.search(t["tabela"]) for _ in (1,))
        )
        for c in lista:
            c["fatia_pct"] = (
                round(c["eventos"] / total_eventos * 100, 1) if total_eventos else 0.0
            )
            c["bytes_estimados"] = (
                int(bytes_eventos * c["eventos"] / total_eventos) if total_eventos else 0
            )

        mudas = [c for c in lista if c["eventos"] == 0]

        return {
            "host": host.name,
            "periodo": periodo,
            "periodo_rotulo": rotulo,
            "total_cameras": len(lista),
            "cameras_com_evento": len(lista) - len(mudas),
            "cameras_mudas": len(mudas),
            "total_eventos": total_eventos,
            "cameras": lista,
            "tabelas": tabelas,
            "esquema": {
                "banco": esq["banco"],
                "tabela_cameras": esq["tabela_cameras"],
                "tabelas_eventos": esq["tabelas_eventos"],
            },
            "estimativa": bool(bytes_eventos),
        }
