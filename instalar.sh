#!/usr/bin/env bash
# ============================================================================
# DGT FaceOps — instalação completa em Ubuntu
#
# Um comando, do zero ao painel no ar:
#
#     bash instalar.sh
#
# Faz tudo: pré-requisitos, Docker, timezone, NTP, .env com chaves geradas,
# diretórios, build, subida e verificação. É idempotente — rodar de novo
# não quebra nada e não sobrescreve o .env existente.
#
# Testado em Ubuntu 22.04 e 24.04.
# ============================================================================
set -uo pipefail

cd "$(dirname "$0")"
RAIZ="$(pwd)"

V=$'\033[32m'; A=$'\033[33m'; R=$'\033[31m'; C=$'\033[36m'; Z=$'\033[0m'
ok()    { echo "  ${V}ok${Z}    $*"; }
aviso() { echo "  ${A}!${Z}     $*"; }
falha() { echo "  ${R}x${Z}     $*"; echo; exit 1; }
passo() { echo; echo "${C}[$1]${Z} $2"; }

echo "════════════════════════════════════════════════════"
echo "  DGT FaceOps — instalação"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════════════════"

# ── 1. Pré-requisitos ──────────────────────────────────────────────────
passo "1/9" "Verificando o sistema..."

if [ ! -f /etc/os-release ]; then
    falha "não consegui identificar a distribuição. Este instalador é para Ubuntu."
fi
. /etc/os-release
echo "  sistema: $PRETTY_NAME"
case "${ID:-}" in
    ubuntu|debian) ok "distribuição suportada" ;;
    *) aviso "testado em Ubuntu; em $ID pode precisar de ajuste" ;;
esac

if [ "$(id -u)" = "0" ]; then
    aviso "rodando como root — o Docker ficará acessível só ao root"
    SUDO=""
else
    if ! sudo -n true 2>/dev/null && ! sudo -v; then
        falha "este instalador precisa de sudo"
    fi
    SUDO="sudo"
fi
ok "privilégios confirmados"

# ── 2. Pacotes ─────────────────────────────────────────────────────────
passo "2/9" "Instalando utilitários..."
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq curl ca-certificates git python3 jq >/dev/null 2>&1 \
    && ok "curl, git, python3, jq" \
    || aviso "alguns pacotes falharam — seguindo"

# ── 3. Timezone e NTP ──────────────────────────────────────────────────
# Relógio errado quebra TLS, desalinha o cron dos agendamentos e datilografa
# backup com data errada — e falha em silêncio, que é o pior tipo de falha.
passo "3/9" "Timezone e sincronização de relógio..."
$SUDO timedatectl set-timezone America/Sao_Paulo 2>/dev/null && ok "America/Sao_Paulo"

if systemctl list-unit-files systemd-timesyncd.service >/dev/null 2>&1 &&
   [ "$($SUDO systemctl is-enabled systemd-timesyncd 2>/dev/null)" != "masked" ]; then
    $SUDO mkdir -p /etc/systemd/timesyncd.conf.d
    printf '[Time]\nNTP=a.ntp.br b.ntp.br c.ntp.br\nFallbackNTP=gps.ntp.br pool.ntp.br\n' \
        | $SUDO tee /etc/systemd/timesyncd.conf.d/brasil.conf >/dev/null
    $SUDO timedatectl set-ntp true 2>/dev/null
    $SUDO systemctl restart systemd-timesyncd 2>/dev/null
    ok "NTP brasileiro (systemd-timesyncd)"
else
    $SUDO apt-get install -y -qq chrony >/dev/null 2>&1
    if ! grep -q "a.ntp.br" /etc/chrony/chrony.conf 2>/dev/null; then
        printf '\n# DGT FaceOps\nserver a.ntp.br iburst\nserver b.ntp.br iburst\n' \
            | $SUDO tee -a /etc/chrony/chrony.conf >/dev/null
    fi
    $SUDO systemctl enable --now chrony >/dev/null 2>&1
    ok "NTP brasileiro (chrony)"
fi
sleep 2
[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ] \
    && ok "relógio sincronizado" \
    || aviso "relógio ainda não sincronizou — confira com 'timedatectl' em alguns minutos"

# ── 4. Docker ──────────────────────────────────────────────────────────
passo "4/9" "Docker..."
if ! command -v docker >/dev/null 2>&1; then
    echo "  instalando (leva 1-2 min)..."
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    $SUDO sh /tmp/get-docker.sh >/dev/null 2>&1
    rm -f /tmp/get-docker.sh
    [ -n "$SUDO" ] && $SUDO usermod -aG docker "$USER"
    ok "Docker instalado"
    RELOGAR=1
else
    ok "Docker já instalado ($(docker --version | cut -d, -f1))"
fi

$SUDO systemctl enable --now docker >/dev/null 2>&1

if ! $SUDO docker compose version >/dev/null 2>&1; then
    falha "'docker compose' não disponível. Atualize o Docker."
fi
ok "docker compose disponível"

# Limite de log do Docker. Sem isto, container em loop de reinício enche o
# disco e derruba o painel justo quando ele é mais necessário.
if [ ! -f /etc/docker/daemon.json ]; then
    $SUDO mkdir -p /etc/docker
    printf '{\n  "log-driver": "json-file",\n  "log-opts": { "max-size": "20m", "max-file": "3" }\n}\n' \
        | $SUDO tee /etc/docker/daemon.json >/dev/null
    $SUDO systemctl restart docker >/dev/null 2>&1
    ok "limite de log do Docker configurado"
else
    ok "/etc/docker/daemon.json já existe — não vou sobrescrever"
fi

# ── 5. Configuração ────────────────────────────────────────────────────
passo "5/9" "Configuração (.env)..."

if [ -f .env ]; then
    ok ".env já existe — mantido como está"
    if grep -q "^SECRET_KEY=troque-esta-chave" .env; then
        falha "a SECRET_KEY ainda é o valor de exemplo. Gere uma:
        python3 -c \"import secrets; print(secrets.token_urlsafe(64))\"
        e edite o .env. Sem isso, o cofre que guarda as chaves SSH não vale nada."
    fi
else
    [ -f .env.example ] || falha ".env.example não encontrado. Repositório incompleto?"

    CHAVE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')"
    SENHA_BD="$(python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(24)))')"

    echo
    echo "  O painel responde em HTTPS. O HTTP so redireciona para ele —"
    echo "  assim usuario e senha nao trafegam em claro na rede."
    read -r -p "  Porta HTTPS [30333]: " PORTA_S
    PORTA_S="${PORTA_S:-30333}"
    read -r -p "  Porta HTTP (redireciona) [8080]: " PORTA
    PORTA="${PORTA:-8080}"

    echo
    echo "  Onde guardar os artefatos de backup?"
    echo "  Use um disco com folga — o perfil completo gera centenas de GB."
    read -r -p "  Caminho [$RAIZ/data/backups]: " DISCO
    DISCO="${DISCO:-$RAIZ/data/backups}"

    sed -e "s|^SECRET_KEY=.*|SECRET_KEY=$CHAVE|" \
        -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$SENHA_BD|" \
        -e "s|^PORTA_HTTP=.*|PORTA_HTTP=$PORTA|" \
        -e "s|^PORTA_HTTPS=.*|PORTA_HTTPS=$PORTA_S|" \
        .env.example > .env
    chmod 600 .env

    ok "SECRET_KEY gerada (64 bytes aleatórios)"
    ok "senha do banco gerada"
    ok "painel em HTTPS na porta $PORTA_S (HTTP $PORTA redireciona)"

    if [ "$DISCO" != "$RAIZ/data/backups" ]; then
        $SUDO mkdir -p "$DISCO"
        $SUDO chown "$(id -u):$(id -g)" "$DISCO" 2>/dev/null || true
        # No .env, nunca no docker-compose.yml: editar arquivo versionado
        # faria o git pull da próxima atualização falhar.
        sed -i "s|^DIR_BACKUPS=.*|DIR_BACKUPS=$DISCO|" .env
        ok "backups em $DISCO"
    fi
fi

PORTA="$(grep -E '^PORTA_HTTP=' .env | cut -d= -f2)"
PORTA="${PORTA:-8080}"
PORTA_S="$(grep -E '^PORTA_HTTPS=' .env | cut -d= -f2)"
PORTA_S="${PORTA_S:-30333}"

# ── 6. Diretórios e fim de linha ───────────────────────────────────────
passo "6/9" "Diretórios e scripts..."
mkdir -p data/backups data/sessions data/marca rclone
[ -f rclone/rclone.conf ] || touch rclone/rclone.conf
ok "data/backups, data/sessions, data/marca, rclone"

# O ffmulti-backup.sh roda dentro de container Linux e nos servidores. Se
# o projeto veio de ZIP baixado no Windows, veio com CRLF e o bash morre
# com "$'\r': command not found" — erro que não aponta para a causa.
CORRIGIDOS=0
for f in scripts/*.sh deploy.sh instalar.sh; do
    [ -f "$f" ] || continue
    if grep -q $'\r' "$f" 2>/dev/null; then
        sed -i 's/\r$//' "$f"
        CORRIGIDOS=$(( CORRIGIDOS + 1 ))
    fi
done
[ "$CORRIGIDOS" -gt 0 ] && ok "$CORRIGIDOS script(s) convertido(s) para LF" || ok "scripts em LF"
chmod +x scripts/*.sh deploy.sh 2>/dev/null

# ── 6b. Certificado TLS ────────────────────────────────────────────────
passo "6b/9" "Certificado TLS..."
bash scripts/gerar_certificado.sh || falha "nao consegui gerar o certificado"

# ── 7. Build ───────────────────────────────────────────────────────────
passo "7/9" "Construindo as imagens (primeira vez leva alguns minutos)..."
$SUDO docker compose build || falha "o build falhou. Veja as mensagens acima."
ok "imagens construídas"

# ── 8. Subida ──────────────────────────────────────────────────────────
passo "8/9" "Subindo os serviços..."
$SUDO docker rm -f faceops_backend faceops_frontend >/dev/null 2>&1 || true
$SUDO docker compose up -d --remove-orphans || falha "falha ao subir os containers"

echo "  aguardando o painel responder..."
PRONTO=0
for i in $(seq 1 45); do
    if curl -fsS "http://localhost:${PORTA}/api/saude" >/dev/null 2>&1; then
        PRONTO=1
        break
    fi
    sleep 2
    [ $(( i % 5 )) -eq 0 ] && echo "  ... $(( i * 2 ))s"
done

if [ "$PRONTO" != "1" ]; then
    echo
    aviso "o painel não respondeu em 90s. Veja o log:"
    echo "      sudo docker compose logs --tail 60 backend"
    exit 1
fi
ok "painel respondendo"

# ── 9. Firewall ────────────────────────────────────────────────────────
passo "9/9" "Firewall..."
if command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -q "Status: active"; then
    $SUDO ufw allow "${PORTA}/tcp" comment 'DGT FaceOps (redireciona)' >/dev/null 2>&1
    $SUDO ufw allow "${PORTA_S}/tcp" comment 'DGT FaceOps' >/dev/null 2>&1
    ok "portas ${PORTA} e ${PORTA_S} liberadas no ufw"
else
    ok "ufw inativo — nada a fazer"
fi

# ── Fecho ──────────────────────────────────────────────────────────────
IP="$(hostname -I | awk '{print $1}')"

echo
echo "════════════════════════════════════════════════════"
echo "  ${V}Painel no ar${Z}"
echo
echo "  Endereço.........: ${C}https://${IP}:${PORTA_S}${Z}"
echo "  Primeiro acesso..: ${A}admin / admin123${Z}"
echo "════════════════════════════════════════════════════"
echo
echo "  ${A}TROQUE A SENHA no primeiro acesso.${Z} Enquanto ela for a de"
echo "  fábrica, quem alcançar esta máquina na rede entra no painel — e,"
echo "  por consequência, nos servidores do FindFace."
echo
echo "  Próximos passos:"
echo "    1. Servidores → Cadastrar servidor (tenha IP, usuário SSH e a chave PEM)"
echo "    2. Testar conexão em cada um"
echo "    3. Destinos → conferir o destino local e cadastrar o externo"
echo "    4. Backups → disparar um 'config' para validar de ponta a ponta"
echo "    5. Agendamentos → programar a recorrência"
echo
echo "  ${A}O navegador vai avisar que o certificado nao e confiavel.${Z}"
echo "  E esperado — ele e autoassinado, para cifrar o trafego na rede"
echo "  interna. Aceite uma vez. A conexao fica cifrada do mesmo jeito."
echo
echo "  Guia completo: docs/15_SOLUCAO_PRONTA.md"
echo
echo "  Comandos do dia a dia:"
echo "    sudo docker compose logs -f backend     ver o log"
echo "    bash deploy.sh                          atualizar (sem rebuild)"
echo "    bash deploy.sh --build                  atualizar (com rebuild)"
echo "    sudo docker compose stop                parar"
echo

if [ "${RELOGAR:-0}" = "1" ]; then
    echo "  ${A}Aviso:${Z} o Docker foi instalado agora e seu usuário foi adicionado"
    echo "  ao grupo 'docker'. Relogue a sessão para usar docker sem sudo."
    echo
fi

echo "  ${A}Guarde uma cópia do .env fora desta máquina.${Z} Dele deriva o cofre"
echo "  que cifra as chaves SSH — perdê-lo obriga a recadastrar tudo."
echo
