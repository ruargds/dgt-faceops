#!/usr/bin/env bash
# ============================================================================
# DGT FaceOps — endurecimento de servidor do FindFace Multi
#
# Contém o crescimento de log que enche o disco raiz. Escrito depois de
# encontrar, num servidor real, 99 GB de /var/log num disco de 123 GB —
# gerado pelo log de acesso HTTP do próprio FindFace, ~8 GB/dia, em
# operação NORMAL (não era erro).
#
#   bash endurecer_servidor_ff.sh              # SIMULA, nao altera nada
#   bash endurecer_servidor_ff.sh --aplicar    # aplica
#
# O que faz:
#   1. Limita o journald (2 GB)
#   2. Rotaciona o syslog por TAMANHO, nao so por data
#   3. Tira o log de container do /var/log/syslog (sem downtime)
#   4. Escreve limite de log do Docker — SEM reiniciar o dockerd
#
# O que NAO faz:
#   - Nao apaga log nenhum
#   - Nao reinicia o Docker (isso derruba o FindFace; fica para janela)
#   - Nao mexe em nada do FindFace
# ============================================================================
set -uo pipefail

APLICAR=0
[ "${1:-}" = "--aplicar" ] && APLICAR=1

C_OK=$'\033[32m'; C_AV=$'\033[33m'; C_ER=$'\033[31m'; C_0=$'\033[0m'
ok(){   echo "  ${C_OK}[ok]${C_0}   $*"; }
aviso(){ echo "  ${C_AV}[!]${C_0}    $*"; }
erro(){ echo "  ${C_ER}[x]${C_0}    $*"; }
acao(){ [ "$APLICAR" = "1" ] && echo "  ${C_OK}[APLICA]${C_0} $*" || echo "  [simula] $*"; }
titulo(){ echo; echo "── $* ────────────────────────────────────"; }

escrever(){
  # $1 = caminho, stdin = conteúdo
  local alvo="$1"; local conteudo; conteudo="$(cat)"
  if [ -f "$alvo" ]; then
    if [ "$conteudo" = "$(cat "$alvo" 2>/dev/null)" ]; then
      ok "$alvo já está como queremos"
      return 0
    fi
    aviso "$alvo já existe com outro conteúdo — copia de seguranca sera feita"
  fi
  acao "escrever $alvo"
  if [ "$APLICAR" = "1" ]; then
    [ -f "$alvo" ] && sudo cp -a "$alvo" "$alvo.faceops-bak-$(date +%Y%m%d%H%M%S)"
    echo "$conteudo" | sudo tee "$alvo" > /dev/null
  else
    echo "        --- conteudo ---"
    echo "$conteudo" | sed 's/^/        /'
  fi
}

echo "════════════════════════════════════════════════════"
echo "  Endurecimento de $(hostname) — $(date '+%F %T')"
[ "$APLICAR" = "1" ] && echo "  MODO: APLICANDO alteracoes" \
                     || echo "  MODO: SIMULACAO (nada sera alterado)"
echo "════════════════════════════════════════════════════"

# ── Situação atual ─────────────────────────────────────────────────────
titulo "Situacao atual"
df -h / | tail -1
echo "  /var/log total: $(sudo du -sh /var/log 2>/dev/null | cut -f1)"
echo "  maiores arquivos:"
sudo ls -lhS /var/log/ 2>/dev/null | head -6 | tail -5 | awk '{printf "    %8s  %s\n",$5,$9}'

DRIVER="$(sudo docker info --format '{{.LoggingDriver}}' 2>/dev/null || echo desconhecido)"
echo "  log-driver do Docker: $DRIVER"

# ── 1. journald ────────────────────────────────────────────────────────
titulo "1. Limite do journald"
ATUAL="$(sudo journalctl --disk-usage 2>/dev/null)"
echo "  hoje: $ATUAL"
escrever /etc/systemd/journald.conf.d/faceops-limite.conf <<'EOF'
# DGT FaceOps — teto do journal.
# Sem isto o journald cresce ate 10% do disco; com dezenas de containers
# logando, passa muito disso antes de alguem perceber.
[Journal]
SystemMaxUse=2G
SystemMaxFileSize=200M
MaxRetentionSec=2week
EOF
if [ "$APLICAR" = "1" ]; then
  acao "systemctl restart systemd-journald (instantaneo, nao afeta containers)"
  sudo systemctl restart systemd-journald && ok "journald reiniciado"
fi

# ── 2. logrotate do syslog por tamanho ─────────────────────────────────
titulo "2. Rotacao do syslog por TAMANHO"
aviso "a rotacao padrao do Ubuntu e semanal — a 8 GB/dia isso nao segura"
escrever /etc/logrotate.d/faceops-syslog <<'EOF'
# DGT FaceOps — rotaciona o syslog por tamanho, nao so por data.
# `maxsize` gira assim que passa do limite, independente do dia.
# `compress` reduz o arquivado; `delaycompress` evita mexer no que
# o rsyslog ainda pode ter aberto.
/var/log/syslog
{
    rotate 7
    daily
    maxsize 500M
    missingok
    notifempty
    compress
    delaycompress
    sharedscripts
    postrotate
        /usr/lib/rsyslog/rsyslog-rotate 2>/dev/null || true
    endscript
}
EOF
if [ "$APLICAR" = "1" ]; then
  acao "validar a regra (logrotate -d, nao executa)"
  sudo logrotate -d /etc/logrotate.d/faceops-syslog 2>&1 | tail -5
fi

# ── 3. tirar log de container do syslog ────────────────────────────────
titulo "3. Log de container fora do /var/log/syslog"
case "$DRIVER" in
  journald|syslog)
    ok "driver '$DRIVER' — os containers alimentam o rsyslog; da para filtrar sem downtime"
    escrever /etc/rsyslog.d/30-faceops-docker.conf <<'EOF'
# DGT FaceOps — nao gravar log de container no /var/log/syslog.
#
# O log chega com o ID curto do container (12 hex) como programname.
# Descartar daqui NAO perde nada: continua acessivel por `docker logs`
# e por `journalctl`. Sem isto, o log de acesso HTTP do FindFace enche
# vários GB por dia no disco raiz.
if re_match($programname, "^[0-9a-f]{12}$") then stop
EOF
    if [ "$APLICAR" = "1" ]; then
      acao "systemctl restart rsyslog (instantaneo, nao toca nos containers)"
      sudo systemctl restart rsyslog && ok "rsyslog reiniciado"
    fi
    ;;
  json-file)
    aviso "driver 'json-file' — o log NAO passa pelo rsyslog; nada a filtrar aqui"
    aviso "o crescimento e por container, em /var/lib/docker/containers/*/*-json.log"
    aviso "o limite vai na secao 4, e exige reiniciar o dockerd"
    ;;
  *)
    aviso "driver '$DRIVER' desconhecido — pulando esta secao"
    ;;
esac

# ── 4. limite de log do Docker (SEM reiniciar) ─────────────────────────
titulo "4. Limite de log do Docker"
if [ -f /etc/docker/daemon.json ]; then
  aviso "/etc/docker/daemon.json ja existe:"
  sed 's/^/        /' /etc/docker/daemon.json
  aviso "NAO vou sobrescrever. Adicione manualmente as chaves de log-opts."
else
  escrever /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "3"
  }
}
EOF
fi
echo
erro "ATENCAO: esta secao NAO tem efeito ate o dockerd reiniciar."
erro "         'systemctl restart docker' DERRUBA todos os containers"
erro "         do FindFace. Isso e janela de manutencao, nao automacao."
erro "         O script NAO faz isso, de proposito."
echo "         Alem disso, o driver so muda para containers RECRIADOS —"
echo "         os atuais mantem o driver ate um 'compose up' recria-los."

# ── Espaço recuperavel sem apagar nada ─────────────────────────────────
titulo "Espaco recuperavel — sem apagar"
GRANDES="$(sudo find /var/log -xdev -maxdepth 1 -type f -size +1G 2>/dev/null)"
if [ -n "$GRANDES" ]; then
  echo "  arquivos acima de 1 GB em /var/log:"
  echo "$GRANDES" | while read -r f; do
    echo "    $(sudo du -h "$f" 2>/dev/null | cut -f1)  $f"
  done
  echo
  echo "  Para liberar PRESERVANDO tudo, mova para um disco com folga:"
  echo "    DESTINO=/media/STORAGE/logs-arquivados   # ajuste"
  echo "    sudo mkdir -p \$DESTINO"
  echo "    # rotacionados: mover e seguro, ninguem os tem abertos"
  echo "    sudo mv /var/log/syslog.[0-9] \$DESTINO/ 2>/dev/null"
  echo "    # o ATIVO: copiar e depois zerar (truncate, nunca rm)"
  echo "    sudo cp /var/log/syslog \$DESTINO/syslog-\$(date +%F)"
  echo "    sudo truncate -s 0 /var/log/syslog"
  echo
  echo "  Por que truncate e nao rm no arquivo ativo: o rsyslog o mantem"
  echo "  aberto. Apagar libera o nome, nao o espaco — o inode so e"
  echo "  devolvido quando o processo fecha. Voce ficaria sem o log E"
  echo "  sem o espaco."
else
  ok "nenhum arquivo acima de 1 GB em /var/log"
fi

# ── Fecho ──────────────────────────────────────────────────────────────
echo
echo "════════════════════════════════════════════════════"
if [ "$APLICAR" = "1" ]; then
  echo "  Aplicado. Situacao agora:"
  df -h / | tail -1
  echo
  echo "  Pendente de janela de manutencao:"
  echo "    - reiniciar o dockerd para o limite de log valer"
else
  echo "  SIMULACAO — nada foi alterado."
  echo "  Para aplicar:  bash $0 --aplicar"
fi
echo "════════════════════════════════════════════════════"
