#!/usr/bin/env bash
# ============================================================================
# DGT FaceOps — empacota o mínimo necessário para rodar
#
#     bash empacotar.sh            # gera faceops-<revisao>.tar.gz
#     bash empacotar.sh --com-docs # inclui a pasta docs/
#
# Leva SÓ o que o painel precisa para subir. Fica de fora:
#
#   docs/               documentação (150 KB, útil mas não roda)
#   windows/            instaladores .bat — não servem no Ubuntu
#   Identidade_visual/  arte de origem; os logos usados já estão
#                       em frontend/public/logos
#   README, SCOPE       leitura, não execução
#   scripts de campo    inventario, topologia, endurecimento — hoje são
#                       telas do painel; ficam no repositório
#
# O `scripts/ffmulti-backup.sh` VAI, e é obrigatório: a imagem do backend
# o copia para /opt/faceops-scripts e o envia aos servidores pela entrada
# padrão do bash a cada execução.
#
# O pacote resultante não contém segredo nenhum: sem .env, sem chave, sem
# credencial. Só código.
# ============================================================================
set -uo pipefail

cd "$(dirname "$0")"

COM_DOCS=0
[ "${1:-}" = "--com-docs" ] && COM_DOCS=1

REV="$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d)"
NOME="faceops-${REV}"
SAIDA="/tmp/${NOME}"

V=$'\033[32m'; A=$'\033[33m'; Z=$'\033[0m'

echo "════════════════════════════════════════════════════"
echo "  Empacotando DGT FaceOps — revisão ${REV}"
echo "════════════════════════════════════════════════════"

rm -rf "$SAIDA"
mkdir -p "$SAIDA"

# ── O que vai ──────────────────────────────────────────────────────────
copiar() {
    if [ -e "$1" ]; then
        mkdir -p "$SAIDA/$(dirname "$1")"
        cp -r "$1" "$SAIDA/$(dirname "$1")/"
        echo "  ${V}+${Z} $1"
    else
        echo "  ${A}!${Z} $1 não encontrado"
    fi
}

echo
echo "Raiz:"
copiar docker-compose.yml
copiar .env.example
copiar .gitattributes
copiar instalar.sh
copiar deploy.sh
copiar atualizar.sh

echo
echo "Backend:"
copiar backend/Dockerfile
copiar backend/requirements.txt
copiar backend/app

echo
echo "Frontend:"
copiar frontend/Dockerfile
copiar frontend/nginx.conf
copiar frontend/package.json
copiar frontend/public
copiar frontend/src

echo
echo "Scripts (só o que o painel executa nos servidores):"
copiar scripts/ffmulti-backup.sh

if [ "$COM_DOCS" = "1" ]; then
    echo
    echo "Documentação:"
    copiar docs
    copiar README.md
fi

# ── Limpeza do que não pode viajar ─────────────────────────────────────
echo
echo "Removendo o que não deve ir:"
for lixo in \
    "$SAIDA/backend/app/__pycache__" \
    "$SAIDA/frontend/node_modules" \
    "$SAIDA/frontend/build" \
    "$SAIDA/.env"
do
    [ -e "$lixo" ] && rm -rf "$lixo" && echo "  - $(basename "$lixo")"
done
find "$SAIDA" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
find "$SAIDA" -name "*.pyc" -delete 2>/dev/null
find "$SAIDA" -name ".DS_Store" -delete 2>/dev/null
echo "  - __pycache__, *.pyc"

# ── Fim de linha em LF, sempre ─────────────────────────────────────────
# O pacote pode ser gerado no Windows. Um .sh com CRLF faz o bash do
# Ubuntu morrer com "$'\r': command not found", erro que não diz nada
# sobre a causa real.
CORRIGIDOS=0
while IFS= read -r -d '' f; do
    if grep -q $'\r' "$f" 2>/dev/null; then
        sed -i 's/\r$//' "$f"
        CORRIGIDOS=$(( CORRIGIDOS + 1 ))
    fi
done < <(find "$SAIDA" \( -name "*.sh" -o -name "*.yml" -o -name "*.yaml" \
         -o -name "Dockerfile" -o -name "*.conf" -o -name ".env.example" \) -print0)
echo "  fim de linha normalizado ($CORRIGIDOS arquivo(s) ajustado(s))"

chmod +x "$SAIDA"/*.sh "$SAIDA"/scripts/*.sh 2>/dev/null

# ── Marca a revisão, para o painel saber o que está no ar ──────────────
echo "$REV" > "$SAIDA/REVISAO"
cat > "$SAIDA/LEIA-ME.txt" <<LEIAME
DGT FaceOps — pacote mínimo
Revisão: $REV
Gerado:  $(date '+%Y-%m-%d %H:%M:%S')

INSTALAR
  bash instalar.sh

  Pergunta a porta e onde guardar backups. O resto é automático.
  Primeiro acesso: admin / admin123 — troque a senha.

ATUALIZAR
  Este pacote não tem histórico git, então 'atualizar.sh' só reconstrói
  o que está aqui. Para trazer versão nova, gere um pacote novo na
  máquina de trabalho e substitua os arquivos, ou clone o repositório
  com um token de leitura.

DOCUMENTAÇÃO
  Fica no repositório: github.com/ruargds/dgt-faceops (privado)
  O guia completo é docs/15_SOLUCAO_PRONTA.md

NÃO CONTÉM
  Nenhum segredo. Sem .env, sem chave, sem credencial.
  A SECRET_KEY é gerada pelo instalar.sh na primeira execução.
LEIAME

# ── Tarball ────────────────────────────────────────────────────────────
echo
echo "Compactando..."
tar -czf "/tmp/${NOME}.tar.gz" -C /tmp "$NOME"
TAMANHO="$(du -h "/tmp/${NOME}.tar.gz" | cut -f1)"
ARQUIVOS="$(find "$SAIDA" -type f | wc -l)"
rm -rf "$SAIDA"

echo
echo "════════════════════════════════════════════════════"
echo "  ${V}Pacote pronto${Z}"
echo
echo "  Arquivo..: /tmp/${NOME}.tar.gz"
echo "  Tamanho..: $TAMANHO"
echo "  Arquivos.: $ARQUIVOS"
echo "════════════════════════════════════════════════════"
echo
echo "  No Ubuntu de destino:"
echo "    sudo mkdir -p /opt/.faceops && sudo chown \$USER:\$USER /opt/.faceops"
echo "    tar -xzf ${NOME}.tar.gz --strip-components=1 -C /opt/.faceops"
echo "    cd /opt/.faceops && bash instalar.sh"
echo
