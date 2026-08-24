#!/usr/bin/env bash
# ============================================================================
# DGT FaceOps — descoberta de topologia
#
# Roda em CADA servidor do FindFace e responde: o que está instalado aqui?
# A saída determina qual perfil de backup faz sentido em qual servidor.
#
# Numa instalação distribuída (app / banco / extração / arquivos), rodar o
# perfil "essencial" no servidor errado gera artefato quase vazio — o script
# de backup avisa, mas é melhor saber antes.
#
# Uso, de dentro do InTerminal ou por SSH:
#   bash descobrir_topologia.sh
#
# Ou direto, sem copiar arquivo:
#   ssh usuario@ip 'bash -s' < scripts/descobrir_topologia.sh
# ============================================================================
set -uo pipefail

FF_DIR="${FF_DIR:-/opt/findface-multi}"

linha() { printf '%s\n' "────────────────────────────────────────────────────"; }
titulo() { echo; linha; echo "  $*"; linha; }

echo "===================================================="
echo " FaceOps — topologia de $(hostname -f 2>/dev/null || hostname)"
echo " $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "===================================================="

# ── Identidade e recursos ──────────────────────────────────────────────
titulo "Máquina"
echo "Hostname....: $(hostname -f 2>/dev/null || hostname)"
echo "IPs.........: $(hostname -I 2>/dev/null | tr -s ' ')"
echo "SO..........: $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"
echo "Kernel......: $(uname -r)"
echo "Núcleos.....: $(nproc 2>/dev/null)"
echo "RAM total...: $(awk '/MemTotal/ {printf "%.1f GB", $2/1048576}' /proc/meminfo 2>/dev/null)"
echo "Usuário.....: $(id -un) (grupos: $(id -Gn | tr ' ' ','))"

printf "sudo........: "
if sudo -n true 2>/dev/null; then echo "sim, sem senha (NOPASSWD)"
elif sudo -v 2>/dev/null; then echo "sim, com senha"
else echo "NAO — backup e restart de container nao vao funcionar"; fi

printf "docker......: "
if docker ps >/dev/null 2>&1; then echo "sim, sem sudo"
elif sudo -n docker ps >/dev/null 2>&1; then echo "so com sudo — adicione o usuario ao grupo 'docker'"
else echo "NAO acessivel"; fi

printf "GPU.........: "
if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | paste -sd '; '
else
    echo "sem nvidia-smi"
fi

# ── FindFace instalado? ────────────────────────────────────────────────
titulo "FindFace Multi"
if [ ! -d "$FF_DIR" ]; then
    echo "$FF_DIR NAO existe."
    echo
    echo "Procurando em outros caminhos..."
    for alt in /opt/findface* /opt/ntech* /srv/findface* /usr/local/findface*; do
        [ -d "$alt" ] && echo "  encontrado: $alt"
    done
    echo
    echo ">>> CONCLUSAO: este servidor NAO parece hospedar o FindFace Multi."
    echo "    Cadastre-o no painel de todo jeito (metricas e terminal funcionam),"
    echo "    mas nao agende backup aqui."
    exit 0
fi

echo "Diretório...: $FF_DIR"
COMPOSE=""
for c in "$FF_DIR/docker-compose.yaml" "$FF_DIR/docker-compose.yml"; do
    [ -f "$c" ] && COMPOSE="$c" && break
done
echo "Compose.....: ${COMPOSE:-NAO ENCONTRADO}"

if [ -f "$FF_DIR/VERSION" ]; then
    echo "Versão......: $(cat "$FF_DIR/VERSION" 2>/dev/null)"
else
    # A versão costuma estar na tag das imagens
    echo "Versão......: $(grep -oE 'image:.*findface-multi-legacy:[^ ]+' "$COMPOSE" 2>/dev/null | head -1 | sed 's/.*://')"
fi

printf "Binário.....: "
if docker compose version >/dev/null 2>&1; then echo "docker compose (plugin v2)"
elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose (v1)"
else echo "NENHUM"; fi

# ── Projeto compose e containers ───────────────────────────────────────
titulo "Containers deste servidor"
PROJETO="$(docker ps -a --filter "label=com.docker.compose.project.config_files=$COMPOSE" \
  --format '{{index .Labels}}' 2>/dev/null | head -1 \
  | tr ',' '\n' | grep '^com.docker.compose.project=' | cut -d= -f2)"
[ -z "$PROJETO" ] && PROJETO="$(basename "$FF_DIR")"
echo "Projeto compose: $PROJETO"
echo

docker ps -a --filter "label=com.docker.compose.project=$PROJETO" \
  --format 'table {{.Label "com.docker.compose.service"}}\t{{.State}}\t{{.Status}}' 2>/dev/null \
  || echo "(nao consegui listar — problema de permissao no docker?)"

# ── Componentes que importam para o backup ─────────────────────────────
titulo "Componentes de dados presentes AQUI"

achou_algum=0

verificar() {
    # $1 = padrão do nome do serviço, $2 = rótulo humano, $3 = o que implica
    local encontrados
    encontrados="$(docker ps -a --filter "label=com.docker.compose.project=$PROJETO" \
        --format '{{.Label "com.docker.compose.service"}}' 2>/dev/null | grep -i "$1" | sort -u | paste -sd ', ')"
    if [ -n "$encontrados" ]; then
        printf '  [X] %-14s %s\n' "$2" "$encontrados"
        echo "         -> $3"
        achou_algum=1
    else
        printf '  [ ] %-14s ausente\n' "$2"
    fi
}

verificar 'postgres'    "PostgreSQL"  "o perfil ESSENCIAL faz pg_dump aqui"
verificar 'tarantool'   "Tarantool"   "os VETORES FACIAIS estao aqui — perfil ESSENCIAL obrigatorio"
verificar 'mongo'       "MongoDB"     "metadados do Video Recorder"
verificar 'upload'      "Upload"      "FOTOS DE EVENTO — so o perfil COMPLETO leva"
verificar 'extraction'  "Extração"    "usa GPU; nada para backup"
verificar 'video-worker' "VideoWorker" "usa GPU; nada para backup"
verificar 'legacy'      "App legacy"  "aplicacao principal; configs/ importa"

# ── Tarantool: o snapshot vai funcionar? ───────────────────────────────
TNT="$(docker ps --filter "label=com.docker.compose.project=$PROJETO" \
  --format '{{.Names}}' 2>/dev/null | grep -i tarantool | head -1)"

if [ -n "$TNT" ]; then
    titulo "Tarantool — via de snapshot"
    echo "Container: $TNT"
    echo
    echo "Sockets de console encontrados:"
    docker exec "$TNT" sh -c 'ls -la /var/run/tarantool/ 2>/dev/null' 2>/dev/null \
        || echo "  (nao consegui listar /var/run/tarantool)"
    echo
    printf "tarantoolctl no container: "
    docker exec "$TNT" sh -c 'command -v tarantoolctl' 2>/dev/null || echo "AUSENTE"
    printf "tarantool no container...: "
    docker exec "$TNT" sh -c 'command -v tarantool' 2>/dev/null || echo "AUSENTE"
    echo
    echo "Teste real de box.snapshot() (sem gravar nada permanente):"
    if docker exec -i "$TNT" sh -c \
        'sock=$(ls /var/run/tarantool/*.control 2>/dev/null | head -1); \
         [ -n "$sock" ] && echo "box.info.status" | tarantoolctl connect "$sock"' 2>&1 | head -5; then
        echo "  -> via tarantoolctl: FUNCIONA"
    else
        echo "  -> via tarantoolctl: nao funcionou"
    fi
fi

# ── Espaço em disco ────────────────────────────────────────────────────
titulo "Disco"
df -h -x tmpfs -x devtmpfs -x overlay 2>/dev/null

echo
echo "Ocupação de $FF_DIR (nivel 1, pode levar minutos):"
sudo du -sh "$FF_DIR"/* 2>/dev/null | sort -rh | head -12 \
    || du -sh "$FF_DIR"/* 2>/dev/null | sort -rh | head -12 \
    || echo "  (sem permissao — rode com sudo)"

echo
echo "Ocupação de $FF_DIR/data (o que decide o perfil COMPLETO):"
sudo du -sh "$FF_DIR/data"/* 2>/dev/null | sort -rh | head -12 \
    || echo "  (sem permissao — rode com sudo)"

# ── Veredito ───────────────────────────────────────────────────────────
titulo "Veredito para este servidor"

if [ "$achou_algum" = "1" ]; then
    echo "Este servidor hospeda componentes do FindFace Multi."
    echo
    echo "Perfis de backup que fazem sentido aqui:"
    echo "  - config     : sempre (configs/ + compose + licenca)"
    docker ps -a --filter "label=com.docker.compose.project=$PROJETO" \
      --format '{{.Label "com.docker.compose.service"}}' 2>/dev/null \
      | grep -qiE 'postgres|tarantool' \
      && echo "  - essencial  : SIM — tem banco e/ou vetores faciais aqui" \
      || echo "  - essencial  : pouco util — nao ha banco nem Tarantool aqui"
    docker ps -a --filter "label=com.docker.compose.project=$PROJETO" \
      --format '{{.Label "com.docker.compose.service"}}' 2>/dev/null \
      | grep -qi 'upload' \
      && echo "  - completo   : e o UNICO que salva as fotos de evento (em janela)" \
      || echo "  - completo   : as fotos de evento nao estao neste servidor"
else
    echo "Nenhum componente de dados do FindFace encontrado ativo aqui."
    echo "Pode ser servidor de apoio (FTP, arquivos) ou o stack esta parado."
    echo "Cadastre no painel para metricas e terminal; backup so o perfil config."
fi

echo
echo "===================================================="
echo " Fim. Envie esta saida inteira para montar o plano."
echo "===================================================="
