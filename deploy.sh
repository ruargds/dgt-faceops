#!/usr/bin/env bash
# ============================================================================
# DGT FaceOps — deploy
#
#   bash deploy.sh            # sobe/reinicia sem rebuild (código Python)
#   bash deploy.sh --build    # rebuild (requirements, Dockerfile, frontend)
#
# Regra prática, igual à do InfraCore: mexeu em requirements.txt, Dockerfile
# ou qualquer coisa em frontend/ → precisa de --build. Mexeu só em código
# Python do backend → deploy simples resolve.
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")"

BUILD=""
[ "${1:-}" = "--build" ] && BUILD="--build"

echo "════════════════════════════════════════════════════"
echo "  DGT FaceOps — deploy${BUILD:+ (com rebuild)}"
echo "════════════════════════════════════════════════════"

# ── Pré-condições ──────────────────────────────────────────────────────
if [ ! -f .env ]; then
    echo "ERRO: .env não existe."
    echo "      cp .env.example .env  e preencha antes de subir."
    exit 1
fi

# Falha cedo e com mensagem clara: SECRET_KEY no padrão significa cofre
# aberto — a chave PEM dos servidores estaria cifrada com segredo público.
if grep -q "^SECRET_KEY=troque-esta-chave" .env; then
    echo "ERRO: SECRET_KEY ainda é o valor de exemplo."
    echo "      Gere uma:  python3 -c \"import secrets; print(secrets.token_urlsafe(64))\""
    echo "      Sem isso, o cofre que guarda as chaves SSH não vale nada."
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    if command -v docker-compose >/dev/null 2>&1; then
        COMPOSE="docker-compose"
    else
        echo "ERRO: docker compose não encontrado."
        exit 1
    fi
else
    COMPOSE="docker compose"
fi

# ── Certificado TLS ────────────────────────────────────────────────────
# Sem ele o nginx nao sobe. Gerar aqui torna o deploy tolerante a quem
# clonou o repositorio sem passar pelo instalador.
if [ ! -f tls/faceops.crt ]; then
    echo "[0/4] Gerando certificado TLS..."
    bash scripts/gerar_certificado.sh || {
        echo "ERRO: nao consegui gerar o certificado."
        exit 1
    }
fi

# ── Diretórios de dados ────────────────────────────────────────────────
mkdir -p data/backups data/sessions data/marca rclone
if [ ! -f rclone/rclone.conf ]; then
    # O compose monta este diretório como read-only; sem o arquivo o
    # container do rclone reclama, e o destino Google Drive fica mudo.
    touch rclone/rclone.conf
fi

# ── Git ────────────────────────────────────────────────────────────────
if [ -d .git ] && git remote get-url origin >/dev/null 2>&1; then
    echo "[1/4] Atualizando o código..."
    git pull --ff-only || echo "  aviso: git pull falhou, seguindo com o código local"
else
    echo "[1/4] Sem remote configurado — usando o código local"
fi

# ── Containers fantasma ────────────────────────────────────────────────
# "container name already in use" acontece quando um deploy anterior
# morreu no meio. Remover na força é mais rápido que investigar.
echo "[2/4] Removendo containers antigos..."
docker rm -f faceops_backend faceops_frontend >/dev/null 2>&1 || true

echo "[3/4] Subindo os serviços..."
$COMPOSE up -d $BUILD --remove-orphans

echo "[4/4] Aguardando o backend responder..."
PORTA="$(grep -E '^PORTA_HTTP=' .env | cut -d= -f2 || true)"
PORTA="${PORTA:-8080}"

for i in $(seq 1 30); do
    if curl -fsS "http://localhost:${PORTA}/api/saude" >/dev/null 2>&1; then
        echo ""
        echo "════════════════════════════════════════════════════"
        echo "  Pronto — http://localhost:${PORTA}"
        echo "  Primeiro acesso: admin / admin123 (troque a senha)"
        echo "════════════════════════════════════════════════════"
        exit 0
    fi
    sleep 2
done

echo ""
echo "AVISO: o backend não respondeu em 60s. Veja o log:"
echo "  $COMPOSE logs --tail 60 backend"
exit 1
