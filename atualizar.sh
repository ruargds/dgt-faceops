#!/usr/bin/env bash
# ============================================================================
# DGT FaceOps — atualização segura
#
#     bash atualizar.sh              # verifica, confirma e atualiza
#     bash atualizar.sh --verificar  # só diz se há versão nova, não altera
#     bash atualizar.sh --forcar     # atualiza mesmo com trabalho em curso
#     bash atualizar.sh --sem-build  # só código Python, sem reconstruir
#
# Três garantias:
#
#   1. NÃO toca no FindFace. Os containers do reconhecimento facial não
#      são vistos nem reiniciados por este script — o painel vive no seu
#      próprio projeto compose.
#
#   2. NÃO interrompe trabalho em curso. Consulta /api/saude antes de
#      qualquer coisa e recusa se houver backup rodando, terminal aberto
#      ou log sendo acompanhado. Reiniciar no meio de um backup mata a
#      execução depois de ela já ter copiado dezenas de GB.
#
#   3. NÃO compete por CPU. O build roda com prioridade mínima (nice +
#      ionice), e o script recusa começar se a máquina já estiver com
#      carga alta — o reconhecimento facial vem primeiro.
#
# Se a versão nova não subir, volta sozinho para a anterior.
# ============================================================================
set -uo pipefail

cd "$(dirname "$0")"

V=$'\033[32m'; A=$'\033[33m'; R=$'\033[31m'; C=$'\033[36m'; Z=$'\033[0m'
ok()    { echo "  ${V}ok${Z}    $*"; }
aviso() { echo "  ${A}!${Z}     $*"; }
erro()  { echo "  ${R}x${Z}     $*"; }
passo() { echo; echo "${C}[$1]${Z} $2"; }

MODO="atualizar"
FORCAR=0
BUILD=1
for arg in "$@"; do
    case "$arg" in
        --verificar) MODO="verificar" ;;
        --forcar)    FORCAR=1 ;;
        --sem-build) BUILD=0 ;;
        *) erro "opção desconhecida: $arg"; exit 1 ;;
    esac
done

command -v docker >/dev/null 2>&1 || { erro "docker não encontrado"; exit 1; }
SUDO=""; [ "$(id -u)" != "0" ] && SUDO="sudo"

PORTA="$(grep -E '^PORTA_HTTP=' .env 2>/dev/null | cut -d= -f2)"
PORTA="${PORTA:-8080}"
BASE="http://localhost:${PORTA}"

echo "════════════════════════════════════════════════════"
echo "  DGT FaceOps — atualização"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════════════════"

# ── 1. Há versão nova? ─────────────────────────────────────────────────
passo "1/6" "Verificando versão..."

if [ ! -d .git ]; then
    aviso "sem repositório git — só posso reconstruir o código local"
    ATUAL="local"; NOVA="local"
else
    git fetch --quiet origin 2>/dev/null || aviso "não consegui falar com o remoto"
    ATUAL="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
    NOVA="$(git rev-parse --short origin/main 2>/dev/null || echo "$ATUAL")"
    echo "  instalada: $ATUAL"
    echo "  disponível: $NOVA"

    if [ "$ATUAL" = "$NOVA" ]; then
        ok "já está na versão mais recente"
        [ "$MODO" = "verificar" ] && exit 0
        if [ "$BUILD" = "0" ]; then
            echo; echo "  Nada a fazer."; exit 0
        fi
        aviso "nada novo no git — vou apenas reconstruir o que já está aqui"
    else
        QUANTOS="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo '?')"
        ok "$QUANTOS commit(s) novo(s)"
        echo
        git log --oneline HEAD..origin/main 2>/dev/null | head -10 | sed 's/^/      /'
    fi
fi

if [ "$MODO" = "verificar" ]; then
    echo; echo "  Modo verificação — nada foi alterado."
    exit 0
fi

# ── 2. Tem alguém trabalhando? ─────────────────────────────────────────
passo "2/6" "Conferindo se há trabalho em curso..."

SAUDE="$(curl -fsS --max-time 8 "${BASE}/api/saude" 2>/dev/null)"
if [ -z "$SAUDE" ]; then
    aviso "o painel não respondeu — pode estar parado. Seguindo."
else
    le() { echo "$SAUDE" | grep -oE "\"$1\": *[0-9-]+" | grep -oE '[0-9-]+$' | head -1; }
    BKP="$(le backups_executando)"; BKP="${BKP:-0}"
    TERM="$(le terminais_ativos)"; TERM="${TERM:-0}"
    LOGS="$(le logs_ativos)";      LOGS="${LOGS:-0}"

    echo "  backups em execução: $BKP"
    echo "  terminais abertos..: $TERM"
    echo "  logs acompanhados..: $LOGS"

    if [ "$BKP" -gt 0 ] || [ "$TERM" -gt 0 ] || [ "$LOGS" -gt 0 ]; then
        echo
        if [ "$FORCAR" = "1" ]; then
            aviso "há trabalho em curso, mas --forcar foi usado"
            [ "$BKP" -gt 0 ] && erro "ATENÇÃO: $BKP backup(s) serão INTERROMPIDOS"
            sleep 3
        else
            erro "há trabalho em curso — atualização adiada"
            echo
            [ "$BKP" -gt 0 ] && echo "      Um backup interrompido perde tudo que já copiou."
            [ "$TERM" -gt 0 ] && echo "      Alguém está com terminal aberto num servidor."
            echo
            echo "      Tente de novo mais tarde, ou:"
            echo "        bash atualizar.sh --forcar"
            exit 2
        fi
    else
        ok "nada em execução — seguro atualizar"
    fi
fi

# ── 3. A máquina aguenta? ──────────────────────────────────────────────
passo "3/6" "Conferindo a carga da máquina..."

NUCLEOS="$(nproc 2>/dev/null || echo 1)"
CARGA="$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || echo 0)"
POR_NUCLEO="$(awk -v c="$CARGA" -v n="$NUCLEOS" 'BEGIN{printf "%.2f", c/n}')"
echo "  carga: $CARGA em $NUCLEOS núcleos = $POR_NUCLEO por núcleo"

APERTADO="$(awk -v p="$POR_NUCLEO" 'BEGIN{print (p>0.8)?1:0}')"
if [ "$APERTADO" = "1" ] && [ "$BUILD" = "1" ] && [ "$FORCAR" != "1" ]; then
    erro "a máquina está com carga alta"
    echo
    echo "      Reconstruir agora competiria por CPU com o que já roda aqui."
    echo "      Opções:"
    echo "        bash atualizar.sh --sem-build   (se mudou só código Python)"
    echo "        bash atualizar.sh --forcar      (assumindo o impacto)"
    echo "        ou tente num horário de menor movimento"
    exit 3
fi
ok "carga aceitável"

# ── 4. Atualizar o código ──────────────────────────────────────────────
passo "4/6" "Atualizando o código..."

if [ -f .env ]; then
    cp -a .env ".env.backup-$(date +%Y%m%d-%H%M%S)"
    ok "cópia de segurança do .env"
fi

REVERTER_PARA="$ATUAL"
if [ -d .git ] && [ "$ATUAL" != "$NOVA" ]; then
    if git pull --ff-only --quiet origin main 2>/dev/null; then
        ok "código atualizado para $NOVA"
    else
        erro "git pull falhou — há alteração local não commitada?"
        echo "      git status"
        exit 4
    fi
fi

# Fim de linha: se o repositório veio de checkout no Windows, os .sh
# chegam com CRLF e o bash morre com "$'\r': command not found".
for f in scripts/*.sh *.sh; do
    [ -f "$f" ] && grep -q $'\r' "$f" 2>/dev/null && sed -i 's/\r$//' "$f"
done
chmod +x scripts/*.sh *.sh 2>/dev/null

export FACEOPS_REVISAO="$(git rev-parse --short HEAD 2>/dev/null || echo local)"
grep -q '^FACEOPS_REVISAO=' .env 2>/dev/null \
    && sed -i "s|^FACEOPS_REVISAO=.*|FACEOPS_REVISAO=$FACEOPS_REVISAO|" .env \
    || echo "FACEOPS_REVISAO=$FACEOPS_REVISAO" >> .env

# ── 5. Construir, com prioridade mínima ────────────────────────────────
passo "5/6" "Aplicando..."

if [ "$BUILD" = "1" ]; then
    echo "  construindo com prioridade mínima (não compete com o que já roda)..."
    # nice/ionice valem para o cliente e para o envio do contexto. O
    # trabalho pesado acontece no dockerd, então o guarda de carga do
    # passo 3 é a proteção que realmente conta.
    if ! nice -n 19 ionice -c3 $SUDO docker compose build 2>&1 | tail -20; then
        erro "o build falhou — o painel antigo continua no ar"
        echo "      Nada foi trocado."
        exit 5
    fi
    ok "imagens construídas"
fi

echo "  reiniciando apenas os containers do painel..."
$SUDO docker compose up -d --remove-orphans 2>&1 | tail -10

# ── 6. Confirmar, ou voltar atrás ──────────────────────────────────────
passo "6/6" "Confirmando..."

PRONTO=0
for i in $(seq 1 40); do
    if curl -fsS --max-time 5 "${BASE}/api/saude" >/dev/null 2>&1; then
        PRONTO=1; break
    fi
    sleep 2
    [ $(( i % 5 )) -eq 0 ] && echo "  ... $(( i * 2 ))s"
done

if [ "$PRONTO" = "1" ]; then
    REV="$(curl -fsS "${BASE}/api/saude" 2>/dev/null | grep -oE '"revisao": *"[^"]*"' | cut -d'"' -f4)"
    echo
    echo "════════════════════════════════════════════════════"
    echo "  ${V}Atualizado${Z} — revisão ${REV:-$FACEOPS_REVISAO}"
    echo "  $BASE"
    echo "════════════════════════════════════════════════════"
    echo
    echo "  O FindFace Multi não foi tocado. Os agendamentos voltaram"
    echo "  sozinhos — a tabela é a fonte de verdade, e o agendador é"
    echo "  remontado a partir dela na subida."
    echo
    exit 0
fi

# ── Reversão ───────────────────────────────────────────────────────────
echo
erro "o painel não respondeu em 80s — revertendo"

if [ -d .git ] && [ "$REVERTER_PARA" != "$FACEOPS_REVISAO" ] && [ "$REVERTER_PARA" != "?" ]; then
    echo "  voltando para $REVERTER_PARA..."
    git reset --hard "$REVERTER_PARA" --quiet
    export FACEOPS_REVISAO="$REVERTER_PARA"
    sed -i "s|^FACEOPS_REVISAO=.*|FACEOPS_REVISAO=$REVERTER_PARA|" .env 2>/dev/null
    [ "$BUILD" = "1" ] && nice -n 19 $SUDO docker compose build >/dev/null 2>&1
    $SUDO docker compose up -d >/dev/null 2>&1
    sleep 10
    if curl -fsS --max-time 5 "${BASE}/api/saude" >/dev/null 2>&1; then
        ok "revertido para $REVERTER_PARA — o painel voltou"
        echo
        echo "  A versão nova não subiu. Veja o motivo:"
        echo "    $SUDO docker compose logs --tail 80 backend"
        exit 6
    fi
fi

erro "a reversão também não respondeu. Intervenção manual necessária:"
echo "      $SUDO docker compose logs --tail 100 backend"
echo "      $SUDO docker compose ps"
exit 7
