#!/usr/bin/env bash
# ============================================================================
# FaceOps — provisionamento da VM do painel, do zero
#
# Roda na VM Linux (Ubuntu 22.04/24.04) hospedada no Windows Server, FORA
# do ambiente facial. Configura tudo que fica fora do Docker.
#
#   bash provision_painel.sh
# ============================================================================
set -euo pipefail

echo "════════════════════════════════════════════════════"
echo "  FaceOps — provisionamento da VM do painel"
echo "════════════════════════════════════════════════════"
echo ""

# ── 1. Sistema ─────────────────────────────────────────────────────────
echo "[1/8] Atualizando pacotes..."
sudo apt update && sudo apt upgrade -y

echo "[2/8] Instalando utilitários..."
sudo apt install -y -qq curl git ca-certificates jq asciinema

# ── 3. Timezone ────────────────────────────────────────────────────────
echo "[3/8] Timezone America/Sao_Paulo..."
sudo timedatectl set-timezone America/Sao_Paulo

# ── 4. NTP brasileiro ──────────────────────────────────────────────────
# Relógio errado quebra TLS, agendamento cron e a datação dos backups —
# e falha em silêncio, o que é pior.
echo "[4/8] NTP (a.ntp.br)..."
if systemctl list-unit-files systemd-timesyncd.service &>/dev/null &&
   [ "$(systemctl is-enabled systemd-timesyncd 2>/dev/null)" != "masked" ]; then
    sudo mkdir -p /etc/systemd/timesyncd.conf.d
    cat << 'NTPEOF' | sudo tee /etc/systemd/timesyncd.conf.d/brasil.conf > /dev/null
[Time]
NTP=a.ntp.br b.ntp.br c.ntp.br
FallbackNTP=gps.ntp.br pool.ntp.br
NTPEOF
    sudo timedatectl set-ntp true
    sudo systemctl restart systemd-timesyncd
else
    echo "  systemd-timesyncd indisponível — usando chrony..."
    sudo apt install -y -qq chrony
    if ! grep -q "a.ntp.br" /etc/chrony/chrony.conf 2>/dev/null; then
        cat << 'CHRONYEOF' | sudo tee -a /etc/chrony/chrony.conf > /dev/null
# FaceOps — NTP brasileiro
server a.ntp.br iburst
server b.ntp.br iburst
server c.ntp.br iburst
CHRONYEOF
    fi
    sudo systemctl enable chrony && sudo systemctl restart chrony
fi
sleep 2
timedatectl | grep -E "Time zone|synchronized" || true

# ── 5. Docker ──────────────────────────────────────────────────────────
echo "[5/8] Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    sudo sh /tmp/get-docker.sh
    sudo usermod -aG docker "$USER"
    rm -f /tmp/get-docker.sh
    echo "  Instalado. Relogue a sessão para usar docker sem sudo."
else
    echo "  Já instalado."
fi
sudo systemctl enable docker

# ── 6. Limites de log do Docker ────────────────────────────────────────
# Sem isto, o log de um container em loop de reinício enche o disco da VM
# e derruba o painel justo quando ele é mais necessário.
echo "[6/8] Limitando log do Docker..."
sudo mkdir -p /etc/docker
if [ ! -f /etc/docker/daemon.json ]; then
    cat << 'DOCKEREOF' | sudo tee /etc/docker/daemon.json > /dev/null
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "3" }
}
DOCKEREOF
    sudo systemctl restart docker
else
    echo "  /etc/docker/daemon.json já existe — não vou sobrescrever."
fi

# ── 7. Firewall ────────────────────────────────────────────────────────
echo "[7/8] Firewall (ufw)..."
if command -v ufw &> /dev/null; then
    sudo ufw allow 22/tcp comment 'SSH'
    sudo ufw allow 30333/tcp comment 'FaceOps painel'
    sudo ufw --force enable
    sudo ufw status numbered
else
    echo "  ufw não instalado — pulando."
fi

# ── 8. Verificação de alcance aos servidores do FindFace ───────────────
echo "[8/8] Alcance aos servidores do FindFace..."
echo ""
echo "  Informe os IPs das VMs do Azure (separados por espaço), ou Enter"
echo "  para pular. Sem alcance na porta 22, o painel não faz nada."
read -r -p "  IPs: " IPS || true

if [ -n "${IPS:-}" ]; then
    for ip in $IPS; do
        if timeout 5 bash -c "echo > /dev/tcp/$ip/22" 2>/dev/null; then
            echo "    OK       $ip:22 alcançável"
        else
            echo "    FALHOU   $ip:22 inalcançável"
            echo "             Libere o NSG do Azure para o IP de saída desta VM,"
            echo "             ou publique a rota por VPN / Azure Bastion."
        fi
    done
fi

echo ""
echo "════════════════════════════════════════════════════"
echo "  VM provisionada."
echo ""
echo "  Próximos passos:"
echo "    git clone <repo> && cd dgt-faceops"
echo "    cp .env.example .env"
echo "    python3 -c \"import secrets; print(secrets.token_urlsafe(64))\""
echo "      -> cole em SECRET_KEY no .env"
echo "    bash deploy.sh --build"
echo "════════════════════════════════════════════════════"
