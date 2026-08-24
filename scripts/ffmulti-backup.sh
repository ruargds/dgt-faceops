#!/usr/bin/env bash
# ============================================================================
# DGT FaceOps — backup do FindFace Multi 2.4.1
#
# Roda NO SERVIDOR alvo, enviado pelo painel via SSH. Não deixa rastro:
# grava só no diretório de staging, que o painel busca e limpa depois.
#
# Perfis
#   config     configs/ + docker-compose.yaml + licença.
#              Segundos, alguns MB, zero downtime.
#
#   essencial  config + pg_dump de todos os bancos + snapshot do Tarantool.
#              Minutos, alguns GB, zero downtime. É o backup diário:
#              recupera cadastros, usuários, câmeras, dossiês e os vetores
#              faciais. NÃO leva as fotos originais de evento.
#
#   completo   Procedimento oficial da NtechLab: para o stack e tara
#              configs/ + data/ inteiros. Horas, centenas de GB, COM
#              downtime. Mensal ou sob demanda, em janela.
#
# Saída: o script imprime linhas "FACEOPS:<chave>=<valor>" que o painel lê
# para montar o manifesto. Tudo mais vai para o log.
# ============================================================================
set -uo pipefail

FF_DIR="${FF_DIR:-/opt/findface-multi}"
PROFILE="${PROFILE:-config}"
STAGING="${STAGING:-/var/backups/faceops}"
LABEL="${LABEL:-$(date +%Y-%m-%d_%H-%M-%S)}"
COMPOSE_FILE="${COMPOSE_FILE:-$FF_DIR/docker-compose.yaml}"

WORK="$STAGING/$LABEL"
ARTIFACT="$STAGING/faceops_${PROFILE}_${LABEL}.tar.gz"

DOWNTIME_START=0
DOWNTIME_TOTAL=0

log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
emit() { echo "FACEOPS:$1=$2"; }
die()  { log "ERRO: $*"; emit status "falha"; emit erro "$*"; cleanup_stack; exit 1; }

# ── Descoberta do ambiente ─────────────────────────────────────────────────

compose_bin() {
  if docker compose version >/dev/null 2>&1; then echo "docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose"
  else echo ""; fi
}

projeto_compose() {
  # Pergunta ao Docker em vez de adivinhar pelo nome do diretório —
  # COMPOSE_PROJECT_NAME no .env muda o padrão.
  docker ps -a --filter "label=com.docker.compose.project.config_files=$COMPOSE_FILE" \
    --format '{{index .Labels}}' 2>/dev/null | head -1 \
    | tr ',' '\n' | grep '^com.docker.compose.project=' | cut -d= -f2 \
    || basename "$FF_DIR"
}

container_do_servico() {
  # $1 = nome do serviço compose. Retorna o container (vazio se não existe).
  docker ps -a --filter "label=com.docker.compose.project=$PROJETO" \
              --filter "label=com.docker.compose.service=$1" \
              --format '{{.Names}}' 2>/dev/null | head -1
}

containers_por_padrao() {
  # $1 = trecho do nome do serviço. Retorna todos que casam.
  docker ps --filter "label=com.docker.compose.project=$PROJETO" \
            --format '{{.Names}}\t{{.Label "com.docker.compose.service"}}' 2>/dev/null \
    | awk -v p="$1" '$2 ~ p {print $1}'
}

# ── Controle de stack (só no perfil completo) ──────────────────────────────

STACK_PARADO=0

parar_stack() {
  local bin; bin="$(compose_bin)"
  [ -z "$bin" ] && die "docker compose não encontrado"
  log "Parando o stack do FindFace Multi (haverá downtime)..."
  DOWNTIME_START=$(date +%s)
  (cd "$FF_DIR" && $bin -f "$COMPOSE_FILE" stop) || die "falha ao parar o stack"
  STACK_PARADO=1
  log "Stack parado."
}

subir_stack() {
  local bin; bin="$(compose_bin)"
  log "Subindo o stack de volta..."
  (cd "$FF_DIR" && $bin -f "$COMPOSE_FILE" up -d) || log "AVISO: falha ao subir o stack — INTERVENÇÃO MANUAL NECESSÁRIA"
  STACK_PARADO=0
  if [ "$DOWNTIME_START" -gt 0 ]; then
    DOWNTIME_TOTAL=$(( $(date +%s) - DOWNTIME_START ))
    log "Downtime total: ${DOWNTIME_TOTAL}s"
  fi
}

cleanup_stack() {
  # Rede de segurança: se o script morrer no meio do perfil completo, o
  # stack NÃO pode ficar parado. Reconhecimento facial fora do ar por um
  # backup que falhou é pior do que não ter backup.
  if [ "$STACK_PARADO" = "1" ]; then
    log "Script interrompido com o stack parado — subindo de volta."
    subir_stack
  fi
}
trap cleanup_stack EXIT INT TERM

# ── Componentes do backup ──────────────────────────────────────────────────

backup_configs() {
  log "Copiando configs/ e docker-compose..."
  mkdir -p "$WORK/config"
  if [ -d "$FF_DIR/configs" ]; then
    tar -czf "$WORK/config/configs.tar.gz" -C "$FF_DIR" configs \
      || die "falha ao arquivar configs/"
    emit configs_bytes "$(stat -c %s "$WORK/config/configs.tar.gz")"
  else
    log "AVISO: $FF_DIR/configs não existe"
  fi

  [ -f "$COMPOSE_FILE" ] && cp "$COMPOSE_FILE" "$WORK/config/"
  [ -f "$FF_DIR/.env" ] && cp "$FF_DIR/.env" "$WORK/config/env.bak"

  # Licença do ntls — sem ela o sistema restaurado não sobe
  find "$FF_DIR" -maxdepth 3 \( -name "*.key" -o -name "license*" -o -name "*.lic" \) \
       -size -10M 2>/dev/null | while read -r f; do
    mkdir -p "$WORK/config/licenca"
    cp "$f" "$WORK/config/licenca/" 2>/dev/null
  done
  log "configs/ concluído."
}

backup_postgres() {
  log "Dump do PostgreSQL..."
  mkdir -p "$WORK/postgres"

  local pg; pg="$(container_do_servico postgresql)"
  [ -z "$pg" ] && pg="$(docker ps --filter "label=com.docker.compose.project=$PROJETO" \
                        --format '{{.Names}}' | grep -i postgres | head -1)"
  if [ -z "$pg" ]; then
    log "AVISO: container do PostgreSQL não encontrado — pulando"
    emit postgres "ausente"
    return 0
  fi
  log "  container: $pg"

  # Usuário vem do ambiente do próprio container — não adivinhar
  local PGUSER
  PGUSER="$(docker exec "$pg" sh -c 'echo -n "$POSTGRES_USER"' 2>/dev/null)"
  [ -z "$PGUSER" ] && PGUSER="postgres"
  log "  usuário: $PGUSER"

  # Papéis e permissões — pg_dump por banco NÃO leva isso
  docker exec "$pg" pg_dumpall -U "$PGUSER" --globals-only \
    > "$WORK/postgres/globals.sql" 2>"$WORK/postgres/globals.err" \
    || log "AVISO: pg_dumpall --globals-only falhou (ver globals.err)"

  # Enumera os bancos em vez de chutar nomes: o FindFace cria ffsecurity,
  # ffsecurity_identity_provider, multi_audit e outros conforme os módulos
  # habilitados, e a lista muda entre instalações.
  local bancos
  bancos="$(docker exec "$pg" psql -U "$PGUSER" -tAc \
    "SELECT datname FROM pg_database WHERE datistemplate = false AND datname <> 'postgres';" 2>/dev/null)"

  if [ -z "$bancos" ]; then
    log "AVISO: nenhum banco listado — verifique as credenciais do PostgreSQL"
    emit postgres "vazio"
    return 0
  fi

  local total=0
  for db in $bancos; do
    log "  dump: $db"
    # -Fc: comprimido, restaurável por objeto, é o formato do InfraCore
    if docker exec "$pg" pg_dump -U "$PGUSER" -Fc --no-password "$db" \
         > "$WORK/postgres/${db}.dump" 2>>"$WORK/postgres/dump.err"; then
      local sz; sz="$(stat -c %s "$WORK/postgres/${db}.dump")"
      # Dump válido tem cabeçalho; arquivo de 0 byte é falha silenciosa
      if [ "$sz" -lt 100 ]; then
        log "  AVISO: dump de $db saiu vazio ($sz bytes)"
        rm -f "$WORK/postgres/${db}.dump"
      else
        total=$(( total + sz ))
        log "  ok: $db ($(numfmt --to=iec "$sz" 2>/dev/null || echo "$sz")B)"
      fi
    else
      log "  AVISO: pg_dump de $db falhou"
    fi
  done
  emit postgres_bytes "$total"
  emit postgres_bancos "$(echo "$bancos" | tr '\n' ' ')"
  log "PostgreSQL concluído."
}

backup_tarantool() {
  # O Tarantool guarda os vetores faciais. Sem ele, o sistema restaurado
  # tem os cadastros mas não reconhece ninguém.
  log "Snapshot do Tarantool (vetores faciais)..."
  mkdir -p "$WORK/tarantool"

  local conts; conts="$(containers_por_padrao 'tarantool')"
  if [ -z "$conts" ]; then
    log "AVISO: nenhum container Tarantool ativo — pulando"
    emit tarantool "ausente"
    return 0
  fi

  local metodo="nenhum"
  local total=0

  for c in $conts; do
    log "  container: $c"

    # box.snapshot() força a gravação de um .snap consistente. Sem isso,
    # copiar o diretório pega xlogs no meio de escrita.
    if docker exec -i "$c" sh -c \
         'sock=$(ls /var/run/tarantool/*.control 2>/dev/null | head -1); \
          [ -n "$sock" ] && echo "box.snapshot()" | tarantoolctl connect "$sock"' \
         >/dev/null 2>&1; then
      metodo="tarantoolctl"
      log "    box.snapshot() ok (console)"
    elif docker exec -i "$c" sh -c 'echo "box.snapshot()" | tarantool' >/dev/null 2>&1; then
      metodo="tarantool-stdin"
      log "    box.snapshot() ok (stdin)"
    else
      log "    AVISO: box.snapshot() não pôde ser disparado — copiando assim mesmo."
      log "    O Tarantool reaplica os xlogs no restore, mas a consistência"
      log "    não fica garantida. Verifique o console do container."
      [ "$metodo" = "nenhum" ] && metodo="copia-direta"
    fi
  done

  # Copia os arquivos de estado do host (não de dentro do container —
  # o volume está montado em data/ e é mais rápido ler direto).
  local tnt_dir="$FF_DIR/data/findface-tarantool-server"
  if [ -d "$tnt_dir" ]; then
    tar -czf "$WORK/tarantool/tarantool-data.tar.gz" \
        -C "$FF_DIR/data" findface-tarantool-server 2>/dev/null \
      && total="$(stat -c %s "$WORK/tarantool/tarantool-data.tar.gz")"
    log "  dados copiados ($(numfmt --to=iec "$total" 2>/dev/null || echo "$total")B)"
  else
    log "  AVISO: $tnt_dir não existe"
  fi

  emit tarantool_metodo "$metodo"
  emit tarantool_bytes "$total"
  log "Tarantool concluído."
}

backup_data_completo() {
  # Procedimento oficial da NtechLab. Exige o stack parado.
  log "Arquivando data/ completo (procedimento oficial NtechLab)..."
  mkdir -p "$WORK/completo"

  local livre necessario
  necessario="$(du -sb "$FF_DIR/data" 2>/dev/null | cut -f1)"
  livre="$(df -B1 --output=avail "$STAGING" 2>/dev/null | tail -1)"
  log "  data/ ocupa: $(numfmt --to=iec "${necessario:-0}" 2>/dev/null || echo "${necessario:-0}")B"
  log "  livre em $STAGING: $(numfmt --to=iec "${livre:-0}" 2>/dev/null || echo "${livre:-0}")B"

  # Compressão costuma render ~50% em fotos JPEG já comprimidas: pouco.
  # Exigir 60% do tamanho bruto é a margem que evita encher o disco do
  # servidor de produção no meio da noite.
  if [ -n "$necessario" ] && [ -n "$livre" ]; then
    local minimo=$(( necessario * 6 / 10 ))
    if [ "$livre" -lt "$minimo" ]; then
      die "espaço insuficiente em $STAGING: preciso de ~$(numfmt --to=iec $minimo 2>/dev/null || echo $minimo)B, tem $(numfmt --to=iec "$livre" 2>/dev/null || echo "$livre")B"
    fi
  fi

  tar -czf "$WORK/completo/data.tar.gz" -C "$FF_DIR" data \
    || die "falha ao arquivar data/"
  emit data_bytes "$(stat -c %s "$WORK/completo/data.tar.gz")"
  log "data/ concluído."
}

# ── Manifesto ──────────────────────────────────────────────────────────────

escrever_manifesto() {
  cat > "$WORK/MANIFESTO.txt" <<MANIFESTO
DGT FaceOps — manifesto de backup
=================================
Perfil.............: $PROFILE
Rótulo.............: $LABEL
Servidor...........: $(hostname -f 2>/dev/null || hostname)
Data...............: $(date '+%Y-%m-%d %H:%M:%S %Z')
Diretório FindFace.: $FF_DIR
Projeto compose....: $PROJETO
Downtime...........: ${DOWNTIME_TOTAL}s

Conteúdo
--------
$(cd "$WORK" && find . -type f -not -name MANIFESTO.txt -printf '%10s  %p\n' 2>/dev/null | sort -k2)

Como restaurar
--------------
Perfil config/essencial — restore quente, ver docs/03_RESTORE.md:
  1. configs.tar.gz  -> extrair sobre $FF_DIR/
  2. postgres/*.dump -> pg_restore --clean --if-exists por banco
  3. tarantool       -> parar findface-tarantool-server, extrair, subir
  4. subir o stack e conferir o painel do FindFace

Perfil completo — procedimento oficial NtechLab:
  1. Instalar o FindFace Multi pelo .run da mesma versão (2.4.1)
  2. sudo docker-compose stop
  3. sudo rm -r $FF_DIR/configs/* && tar -xf configs.tar.gz -C $FF_DIR/
  4. sudo rm -r $FF_DIR/data/*    && tar -xf data.tar.gz    -C $FF_DIR/
  5. Conferir docker-compose.yaml salvo contra o recém-instalado
  6. sudo docker-compose up -d
MANIFESTO
}

# ── Execução ───────────────────────────────────────────────────────────────

log "════════════════════════════════════════════════════"
log " FaceOps — backup perfil '$PROFILE'"
log "════════════════════════════════════════════════════"

command -v docker >/dev/null 2>&1 || die "docker não encontrado neste servidor"
[ -d "$FF_DIR" ] || die "$FF_DIR não existe — FindFace Multi está instalado aqui?"

PROJETO="$(projeto_compose)"
log "Projeto compose: $PROJETO"

mkdir -p "$WORK" || die "não consegui criar $WORK"

case "$PROFILE" in
  config)
    backup_configs
    ;;
  essencial)
    backup_configs
    backup_postgres
    backup_tarantool
    ;;
  completo)
    backup_configs
    parar_stack
    backup_data_completo
    subir_stack
    ;;
  *)
    die "perfil desconhecido: $PROFILE (use config, essencial ou completo)"
    ;;
esac

escrever_manifesto

log "Empacotando artefato final..."
tar -czf "$ARTIFACT" -C "$STAGING" "$LABEL" || die "falha ao empacotar $ARTIFACT"
rm -rf "$WORK"

TAMANHO="$(stat -c %s "$ARTIFACT")"
log "Calculando checksum..."
CHECKSUM="$(sha256sum "$ARTIFACT" | cut -d' ' -f1)"

emit artefato   "$ARTIFACT"
emit tamanho    "$TAMANHO"
emit checksum   "$CHECKSUM"
emit downtime   "$DOWNTIME_TOTAL"
emit status     "sucesso"

log "════════════════════════════════════════════════════"
log " Concluído: $(basename "$ARTIFACT")"
log " Tamanho..: $(numfmt --to=iec "$TAMANHO" 2>/dev/null || echo "$TAMANHO")B"
log " Downtime.: ${DOWNTIME_TOTAL}s"
log "════════════════════════════════════════════════════"
exit 0
