#!/usr/bin/env bash
# ============================================================================
# DGT FaceOps — certificado TLS autoassinado
#
#     bash scripts/gerar_certificado.sh          # gera se não existir
#     bash scripts/gerar_certificado.sh --forcar # regera, substituindo
#
# Para que serve: impedir que usuário e senha trafeguem em claro na rede
# interna. Não é para provar identidade ao mundo — o painel não fica
# exposto na internet.
#
# Por que autoassinado serve aqui: o objetivo é CIFRAR o tráfego, não
# obter confiança de terceiros. Um certificado emitido por CA pública
# exigiria domínio válido e porta 80 acessível de fora — exatamente o que
# não queremos.
#
# O navegador vai avisar que o certificado não é confiável na primeira
# visita. É esperado. Aceitar uma vez basta, e a partir daí a conexão é
# cifrada do mesmo jeito. Para tirar o aviso de vez, instale o .crt nas
# autoridades confiáveis das máquinas que acessam — instruções no fim.
#
# Validade de 10 anos, de propósito: certificado interno que vence sozinho
# vira incidente numa manhã de segunda, sem ninguém lembrar por quê.
# ============================================================================
set -uo pipefail

cd "$(dirname "$0")/.."

DIR="tls"
CRT="$DIR/faceops.crt"
CHAVE="$DIR/faceops.key"
DIAS=3650

FORCAR=0
[ "${1:-}" = "--forcar" ] && FORCAR=1

V=$'\033[32m'; A=$'\033[33m'; Z=$'\033[0m'

if [ -f "$CRT" ] && [ -f "$CHAVE" ] && [ "$FORCAR" != "1" ]; then
    VENCE="$(openssl x509 -enddate -noout -in "$CRT" 2>/dev/null | cut -d= -f2)"
    echo "  ${V}ok${Z}    certificado já existe, válido até: ${VENCE:-?}"
    echo "        para regerar: bash scripts/gerar_certificado.sh --forcar"
    exit 0
fi

command -v openssl >/dev/null 2>&1 || {
    echo "  openssl não encontrado. Instale com: sudo apt install -y openssl"
    exit 1
}

mkdir -p "$DIR"

# Nomes e IPs pelos quais o painel será acessado. Sem eles no SAN, o
# navegador reclama de "nome não confere" ALÉM de reclamar da autoridade
# — dois avisos onde deveria haver um.
NOME="$(hostname -s 2>/dev/null || echo faceops)"
FQDN="$(hostname -f 2>/dev/null || echo "$NOME")"

SAN="DNS:localhost,DNS:$NOME"
[ "$FQDN" != "$NOME" ] && SAN="$SAN,DNS:$FQDN"
SAN="$SAN,IP:127.0.0.1"

for ip in $(hostname -I 2>/dev/null); do
    case "$ip" in
        *:*) continue ;;          # ignora IPv6
        127.*) continue ;;
        172.1[7-9].*|172.2[0-9].*|172.3[0-1].*) continue ;;  # rede do docker
    esac
    SAN="$SAN,IP:$ip"
done

echo "  gerando certificado autoassinado..."
echo "  válido para: $SAN"

# Arquivo de configuração em vez de `-subj` e `-addext`. Duas razões:
# `-addext` não existe em openssl antigo, e `-subj "/C=BR/..."` é
# convertido em caminho do Windows quando o script roda em Git Bash.
# Config file funciona igual nos dois.
CONF="$(mktemp)"
cat > "$CONF" <<CFGEOF
[req]
distinguished_name = dn
x509_extensions    = ext
prompt             = no

[dn]
C  = BR
O  = DGT
CN = $FQDN

[ext]
subjectAltName         = $SAN
basicConstraints       = critical,CA:FALSE
keyUsage               = critical,digitalSignature,keyEncipherment
extendedKeyUsage       = serverAuth
CFGEOF

if ! openssl req -x509 -nodes -newkey rsa:2048 -days "$DIAS"         -keyout "$CHAVE" -out "$CRT" -config "$CONF" 2>/dev/null; then
    rm -f "$CONF"
    echo "  falha ao gerar o certificado."
    echo "  openssl: $(openssl version 2>/dev/null || echo 'não encontrado')"
    exit 1
fi
rm -f "$CONF"

# A chave privada não pode ser legível por qualquer um na máquina.
chmod 600 "$CHAVE"
chmod 644 "$CRT"

IMPRESSAO="$(openssl x509 -noout -fingerprint -sha256 -in "$CRT" 2>/dev/null | cut -d= -f2)"
VENCE="$(openssl x509 -enddate -noout -in "$CRT" 2>/dev/null | cut -d= -f2)"

echo
echo "  ${V}ok${Z}    certificado gerado"
echo "        arquivo..: $CRT"
echo "        válido até: $VENCE"
echo "        impressão : $IMPRESSAO"
echo
echo "  ${A}O navegador vai avisar que o certificado não é confiável.${Z}"
echo "  É esperado — ele é autoassinado. A conexão é cifrada do mesmo jeito."
echo "  Confira a impressão acima na primeira visita e aceite."
echo
echo "  Para tirar o aviso nas máquinas que acessam:"
echo "    Windows: duplo clique no .crt -> Instalar -> Máquina local ->"
echo "             Autoridades de Certificação Raiz Confiáveis"
echo "    Linux:   sudo cp $CRT /usr/local/share/ca-certificates/ &&"
echo "             sudo update-ca-certificates"
echo
echo "  Para usar um certificado próprio no lugar deste, substitua os dois"
echo "  arquivos em $DIR/ mantendo os mesmos nomes e rode: bash deploy.sh"
