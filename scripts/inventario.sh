#!/usr/bin/env bash
# ============================================================================
# DGT FaceOps — inventário de servidor, SOMENTE LEITURA
#
# Levanta tudo que existe na máquina antes de qualquer alteração. Não
# escreve, não apaga, não reinicia nada — a única coisa criada é a saída,
# se você redirecionar.
#
# Roda no servidor, ou de fora sem copiar arquivo:
#   ssh usuario@ip 'bash -s' < scripts/inventario.sh | tee inventario-ip.txt
#
# As seções que mais decidem coisa: COMPOSE (onde o FindFace realmente
# está), ONDE OS DADOS MORAM (se o disco certo está sendo usado) e
# DOCKER (qual driver de log, que define se a correção tem downtime).
# ============================================================================
set -uo pipefail

S(){ echo; echo "=============== $* ==============="; }
Q(){ command -v "$1" >/dev/null 2>&1; }
D="docker"; docker ps >/dev/null 2>&1 || D="sudo docker"

S "MAQUINA"
hostname -f 2>/dev/null || hostname
echo "IPs.....: $(hostname -I)"
echo "SO......: $(. /etc/os-release; echo "$PRETTY_NAME")"
echo "Kernel..: $(uname -r)"
echo "CPU.....: $(nproc) nucleos —$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2-)"
echo "RAM.....: $(awk '/MemTotal/{printf "%.1f GB",$2/1048576}' /proc/meminfo)"
echo "Uptime..: $(uptime -p 2>/dev/null)"
echo "Usuario.: $(id -un) — grupos: $(id -Gn | tr ' ' ',')"
printf "sudo....: "; sudo -n true 2>/dev/null && echo NOPASSWD || echo "com senha ou ausente"
printf "docker..: "; docker ps >/dev/null 2>&1 && echo "grupo docker OK" || echo "exige sudo"

S "DISCOS — layout"
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT,MODEL 2>/dev/null
echo; df -hT -x tmpfs -x devtmpfs
echo; echo "--- fstab ---"
grep -v '^#' /etc/fstab 2>/dev/null | grep -v '^$'

S "VAR/LOG — arquivos, sem recursao"
sudo ls -lhS /var/log/ 2>/dev/null | head -20
echo; echo "--- rotacao configurada ---"
ls /etc/logrotate.d/ 2>/dev/null | tr '\n' ' '; echo
echo "--- regra do syslog ---"
sudo sed -n '1,40p' /etc/logrotate.d/rsyslog 2>/dev/null
echo "--- journald ---"
sudo journalctl --disk-usage 2>/dev/null
grep -hv '^#' /etc/systemd/journald.conf /etc/systemd/journald.conf.d/*.conf 2>/dev/null | grep -v '^$'

S "DISCOS SECUNDARIOS — o que ja esta la"
for m in $(df --output=target -x tmpfs -x devtmpfs 2>/dev/null | tail -n +2 | grep -vE '^/$|^/boot'); do
  echo "--- $m ---"
  ls -la "$m" 2>/dev/null | head -15
  timeout 120 sudo du -h -d1 "$m" 2>/dev/null | sort -rh | head -10
done

S "DOCKER — daemon"
$D version --format 'client {{.Client.Version}} / server {{.Server.Version}}' 2>/dev/null
$D info --format 'log-driver...: {{.LoggingDriver}}
storage-driver: {{.Driver}}
docker-root..: {{.DockerRootDir}}
containers...: {{.Containers}} ({{.ContainersRunning}} rodando)
imagens......: {{.Images}}' 2>/dev/null
echo "--- daemon.json ---"
cat /etc/docker/daemon.json 2>/dev/null || echo "(nao existe — sem limite de log)"

S "CONTAINERS — nome real, servico, imagem"
$D ps -a --format '{{.Names}}|{{.Label "com.docker.compose.service"}}|{{.Image}}|{{.State}}|{{.Status}}' 2>/dev/null | sort

S "COMPOSE — projeto e caminho REAL"
PRIM=$($D ps -q 2>/dev/null | head -1)
if [ -n "$PRIM" ]; then
  $D inspect "$PRIM" --format \
'projeto: {{index .Config.Labels "com.docker.compose.project"}}
compose: {{index .Config.Labels "com.docker.compose.project.config_files"}}
workdir: {{index .Config.Labels "com.docker.compose.project.working_dir"}}
logpath: {{.LogPath}}
driver.: {{.HostConfig.LogConfig.Type}}' 2>/dev/null
else
  echo "(nenhum container rodando)"
fi

S "ONDE OS DADOS MORAM — bind mounts"
for c in $($D ps -q 2>/dev/null); do
  $D inspect "$c" --format '{{range .Mounts}}{{$.Name}} :: {{.Type}} {{.Source}} -> {{.Destination}}
{{end}}' 2>/dev/null | sed 's|^/||' | grep -v '^$'
done | sort -u | head -60

S "VOLUMES nomeados"
$D volume ls 2>/dev/null | head -30

S "GPU"
Q nvidia-smi && nvidia-smi || echo "sem nvidia-smi neste host"

S "REDE — portas ouvindo"
sudo ss -tulnp 2>/dev/null | head -35

S "REDE — quem conversa com quem"
sudo ss -tnp 2>/dev/null | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | sort | uniq -c | sort -rn | head -15
echo "--- rotas ---"; ip route

S "ZABBIX"
Q zabbix_agentd && zabbix_agentd -V 2>/dev/null | head -2
systemctl is-active zabbix-agent zabbix-agent2 2>/dev/null
grep -hE '^(Server|ServerActive|Hostname)=' /etc/zabbix/zabbix_agent*.conf 2>/dev/null

S "AGENDAMENTOS existentes"
echo "--- crontab do usuario ---"; crontab -l 2>/dev/null || echo "(vazio)"
echo "--- cron do root ---";      sudo crontab -l 2>/dev/null || echo "(vazio)"
echo "--- /etc/cron.d ---";       ls /etc/cron.d/ 2>/dev/null | tr '\n' ' '; echo
echo "--- timers systemd ---";    systemctl list-timers --no-pager 2>/dev/null | head -12

S "SERVICOS systemd habilitados (fora do padrao)"
systemctl list-unit-files --state=enabled --no-pager 2>/dev/null \
  | grep -viE 'systemd-|dbus|getty|cron|ssh|snap|apparmor|ufw|multipath|open-iscsi|lvm|networkd|resolved|timesync|udev|unattended|apport|rsyslog|polkit|irqbalance|chrony|walinux|azure' \
  | head -25

S "MEMORIA e CARGA agora"
free -h; echo; cat /proc/loadavg; echo
$D stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}' 2>/dev/null | head -20

S "FIM — $(date '+%F %T')"
