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
#   essencial  config + todos os bancos do projeto compose:
#              PostgreSQL/TimescaleDB, Tarantool, MongoDB, etcd, Redis e
#              Grafana. Minutos, alguns GB, zero downtime. E o backup
#              diario: recupera cadastros, usuarios, cameras, dossies e os
#              vetores faciais. NAO leva as fotos originais de evento.
#
#              Funciona tambem em host que NAO e FindFace (aplicacao de
#              integracao, coletor, dashboards): sem configs/, arquiva os
#              arquivos de configuracao do projeto, e faz dump do que
#              encontrar.
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
# Margem de disco exigida no perfil completo, configuravel no painel
MARGEM_PCT="${MARGEM_PCT:-60}"

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
  log "Copiando configuracao..."
  mkdir -p "$WORK/config"

  # Instalacao do FindFace tem configs/. Um host com outra aplicacao
  # (ponte de integracao, coletor, dashboards) nao tem — e mesmo assim
  # precisa do compose e dos arquivos de configuracao salvos.
  if [ -d "$FF_DIR/configs" ]; then
    tar -czf "$WORK/config/configs.tar.gz" -C "$FF_DIR" configs \
      || die "falha ao arquivar configs/"
    emit configs_bytes "$(stat -c %s "$WORK/config/configs.tar.gz")"
    log "  configs/ arquivado"
  else
    log "  sem configs/ — arquivando os arquivos de configuracao do projeto"
    # Tudo que NAO for dado: yaml, json, conf, env, sql, sh, certificado.
    # O -size limita para nao arrastar dump ou midia por engano, e o
    # -not -path exclui data/ e volumes, que sao dado, nao configuracao.
    find "$FF_DIR" -maxdepth 3 -type f \
         \( -name "*.y*ml" -o -name "*.json" -o -name "*.conf" -o -name "*.env" \
            -o -name ".env" -o -name "*.ini" -o -name "*.toml" -o -name "*.sql" \
            -o -name "*.sh" -o -name "*.pem" -o -name "*.crt" -o -name "*.key" \) \
         -size -20M \
         -not -path "*/data/*" -not -path "*/volumes/*" -not -path "*/node_modules/*" \
         -print0 2>/dev/null \
      | tar --null -czf "$WORK/config/projeto-config.tar.gz" -T - 2>/dev/null
    if [ -s "$WORK/config/projeto-config.tar.gz" ]; then
      emit configs_bytes "$(stat -c %s "$WORK/config/projeto-config.tar.gz")"
      log "  configuracao do projeto arquivada"
    else
      log "  AVISO: nenhum arquivo de configuracao encontrado em $FF_DIR"
    fi
  fi

  [ -f "$COMPOSE_FILE" ] && cp "$COMPOSE_FILE" "$WORK/config/"
  [ -f "$FF_DIR/.env" ] && cp "$FF_DIR/.env" "$WORK/config/env.bak"

  # Licenca do ntls — sem ela o FindFace restaurado nao sobe. Em host
  # que nao e FindFace simplesmente nao acha nada, e tudo bem.
  find "$FF_DIR" -maxdepth 3 \( -name "*.key" -o -name "license*" -o -name "*.lic" \) \
       -size -10M 2>/dev/null | while read -r f; do
    mkdir -p "$WORK/config/licenca"
    cp "$f" "$WORK/config/licenca/" 2>/dev/null
  done
  log "Configuracao concluida."
}

backup_postgres() {
  # Numa instalacao real ha MAIS DE UM banco PostgreSQL: o `postgresql`
  # principal e o `timescaledb` (series temporais, tambem PostgreSQL).
  # Fazer dump so do primeiro deixaria o TimescaleDB de fora sem avisar.
  log "Dump dos bancos PostgreSQL..."
  mkdir -p "$WORK/postgres"

  local conts
  conts="$(docker ps --filter "label=com.docker.compose.project=$PROJETO"       --format '{{.Names}}	{{.Label "com.docker.compose.service"}}' 2>/dev/null       | awk '$2 ~ /postgres|timescale/ {print $1"|"$2}')"

  if [ -z "$conts" ]; then
    log "AVISO: nenhum container PostgreSQL/TimescaleDB ativo — pulando"
    emit postgres "ausente"
    return 0
  fi

  local total=0
  local instancias=""

  for par in $conts; do
    local c="${par%%|*}"; local svc="${par##*|}"
    log "  instancia: $svc ($c)"
    mkdir -p "$WORK/postgres/$svc"

    # Usuario vem do ambiente do proprio container — nao adivinhar
    local PGUSER
    PGUSER="$(docker exec "$c" sh -c 'echo -n "$POSTGRES_USER"' 2>/dev/null)"
    [ -z "$PGUSER" ] && PGUSER="postgres"

    # Papeis e permissoes: pg_dump por banco NAO leva isso, e sem eles o
    # restore falha com erro de permissao que parece corrupcao.
    docker exec "$c" pg_dumpall -U "$PGUSER" --globals-only       > "$WORK/postgres/$svc/globals.sql" 2>"$WORK/postgres/$svc/globals.err"       || log "    AVISO: pg_dumpall --globals-only falhou"

    # Enumera os bancos em vez de chutar nomes: a lista muda conforme os
    # modulos habilitados do FindFace.
    local bancos
    bancos="$(docker exec "$c" psql -U "$PGUSER" -tAc       "SELECT datname FROM pg_database WHERE datistemplate = false AND datname <> 'postgres';" 2>/dev/null)"

    if [ -z "$bancos" ]; then
      log "    AVISO: nenhum banco listado em $svc"
      continue
    fi

    for db in $bancos; do
      if docker exec "$c" pg_dump -U "$PGUSER" -Fc --no-password "$db"            > "$WORK/postgres/$svc/${db}.dump" 2>>"$WORK/postgres/$svc/dump.err"; then
        local sz; sz="$(stat -c %s "$WORK/postgres/$svc/${db}.dump")"
        if [ "$sz" -lt 100 ]; then
          log "    AVISO: dump de $db saiu vazio ($sz bytes)"
          rm -f "$WORK/postgres/$svc/${db}.dump"
        else
          total=$(( total + sz ))
          log "    ok: $db ($(numfmt --to=iec "$sz" 2>/dev/null || echo "$sz")B)"
        fi
      else
        log "    AVISO: pg_dump de $db falhou"
      fi
    done
    instancias="$instancias $svc"
  done

  emit postgres_bytes "$total"
  emit postgres_instancias "$instancias"
  log "PostgreSQL concluido."
}

backup_mongodb() {
  # O MongoDB guarda os metadados dos trechos de video (Video Recorder).
  # Sem ele, o video gravado fica no disco mas o sistema nao sabe indexar.
  log "Dump do MongoDB..."
  local c; c="$(container_do_servico mongodb)"
  [ -z "$c" ] && c="$(docker ps --filter "label=com.docker.compose.project=$PROJETO"                       --format '{{.Names}}' | grep -i mongo | head -1)"
  if [ -z "$c" ]; then
    log "  MongoDB nao encontrado neste servidor — pulando"
    emit mongodb "ausente"
    return 0
  fi

  mkdir -p "$WORK/mongodb"
  local UZ PZ ARGS=""
  UZ="$(docker exec "$c" sh -c 'echo -n "$MONGO_INITDB_ROOT_USERNAME"' 2>/dev/null)"
  PZ="$(docker exec "$c" sh -c 'echo -n "$MONGO_INITDB_ROOT_PASSWORD"' 2>/dev/null)"
  if [ -n "$UZ" ]; then
    ARGS="-u $UZ -p $PZ --authenticationDatabase admin"
    log "  usando credencial do proprio container"
  fi

  # --archive --gzip sai por stdout: nao precisa de espaco temporario
  # dentro do container, que pode ter volume pequeno.
  if docker exec "$c" sh -c "mongodump $ARGS --archive --gzip"        > "$WORK/mongodb/mongodump.gz" 2>"$WORK/mongodb/mongodump.err"; then
    local sz; sz="$(stat -c %s "$WORK/mongodb/mongodump.gz")"
    if [ "$sz" -lt 100 ]; then
      log "  AVISO: dump vazio — ver mongodump.err"
      emit mongodb "vazio"
    else
      emit mongodb_bytes "$sz"
      log "  ok ($(numfmt --to=iec "$sz" 2>/dev/null || echo "$sz")B)"
    fi
  else
    log "  AVISO: mongodump falhou — ver mongodump.err"
    emit mongodb "falhou"
  fi
}

backup_etcd() {
  # O etcd coordena o cluster e guarda configuracao de runtime.
  # `snapshot save` gera um arquivo consistente; copiar o diretorio nao.
  log "Snapshot do etcd..."
  local c; c="$(container_do_servico etcd)"
  [ -z "$c" ] && c="$(docker ps --filter "label=com.docker.compose.project=$PROJETO"                       --format '{{.Names}}' | grep -i etcd | head -1)"
  if [ -z "$c" ]; then
    log "  etcd nao encontrado — pulando"
    emit etcd "ausente"
    return 0
  fi

  mkdir -p "$WORK/etcd"
  if docker exec -e ETCDCTL_API=3 "$c"        etcdctl snapshot save /tmp/faceops-etcd.db >/dev/null 2>&1      && docker cp "$c:/tmp/faceops-etcd.db" "$WORK/etcd/snapshot.db" >/dev/null 2>&1; then
    docker exec "$c" rm -f /tmp/faceops-etcd.db 2>/dev/null
    local sz; sz="$(stat -c %s "$WORK/etcd/snapshot.db" 2>/dev/null || echo 0)"
    emit etcd_bytes "$sz"
    log "  ok ($(numfmt --to=iec "$sz" 2>/dev/null || echo "$sz")B)"
  else
    log "  AVISO: etcdctl snapshot falhou — copiando o diretorio de dados"
    if [ -d "$FF_DIR/data/etcd" ]; then
      tar -czf "$WORK/etcd/etcd-data.tar.gz" -C "$FF_DIR/data" etcd 2>/dev/null         && emit etcd_bytes "$(stat -c %s "$WORK/etcd/etcd-data.tar.gz")"
      log "  copia direta feita (consistencia nao garantida)"
    fi
    emit etcd_metodo "copia-direta"
  fi
}

backup_tarantool() {
  # O Tarantool guarda os vetores faciais. Sem ele, o sistema restaurado
  # tem os cadastros mas nao reconhece ninguem.
  #
  # Descoberto em campo: esta instalacao usa porta TCP (CFG_LISTEN_PORT,
  # padrao 8001) para admin, nao socket .control — entao console via
  # socket nao funciona.
  #
  # Isso NAO e problema. O Tarantool foi desenhado para crash recovery:
  # o par .snap + .xlog que ele grava sozinho e SEMPRE um estado
  # recuperavel. Copiar o diretorio inteiro (o que este backup faz) e um
  # backup consistente POR DESIGN do Tarantool. O box.snapshot() manual
  # abaixo apenas reduz quantos xlogs sao reaplicados no restore — e
  # otimizacao, nao requisito de consistencia. A estrategia:
  #   1. Tentar forcar um .snap fresco via `tarantoolctl eval` (nao precisa
  #      de socket — avalia direto no processo).
  #   2. De qualquer forma, copiar o diretorio inteiro, que ja contem
  #      .snap consistentes.
  #   3. Registrar a idade do .snap mais recente, para o operador saber o
  #      quao fresco esta o backup dos vetores.
  log "Snapshot do Tarantool (vetores faciais)..."
  mkdir -p "$WORK/tarantool"

  local conts; conts="$(containers_por_padrao 'tarantool')"
  local qtd; qtd="$(echo "$conts" | grep -c . )"
  if [ -z "$conts" ]; then
    log "AVISO: nenhum container Tarantool ativo — pulando"
    emit tarantool "ausente"
    return 0
  fi
  log "  $qtd instancia(s) Tarantool"

  local metodo="copia-direta"
  local disparados=0

  for c in $conts; do
    # box.snapshot() forca um .snap consistente AGORA. tarantoolctl eval
    # roda o codigo no processo sem depender de socket de console.
    if docker exec -i "$c" tarantoolctl eval /dev/stdin >/dev/null 2>&1 <<'LUAEOF'
box.snapshot()
return true
LUAEOF
    then
      disparados=$(( disparados + 1 ))
      metodo="tarantoolctl-eval"
    elif docker exec "$c" sh -c 'echo "box.snapshot()" | tarantool' >/dev/null 2>&1; then
      disparados=$(( disparados + 1 ))
      metodo="tarantool-stdin"
    fi
  done

  if [ "$disparados" -gt 0 ]; then
    log "  box.snapshot() disparado em $disparados de $qtd instancia(s) ($metodo)"
  else
    log "  AVISO: nao consegui disparar box.snapshot(). Copiando os .snap"
    log "  existentes, que o Tarantool grava periodicamente e sao"
    log "  consistentes por construcao — mas podem ter algumas horas."
  fi

  # Copia os arquivos de estado do host. O diretorio pai reune todos os
  # shards e replicas, cada um em sua subpasta.
  local tnt_dir="$FF_DIR/data/findface-tarantool-server"
  local total=0
  if [ -d "$tnt_dir" ]; then
    tar -czf "$WORK/tarantool/tarantool-data.tar.gz"         -C "$FF_DIR/data" findface-tarantool-server 2>/dev/null       && total="$(stat -c %s "$WORK/tarantool/tarantool-data.tar.gz")"
    log "  dados copiados ($(numfmt --to=iec "$total" 2>/dev/null || echo "$total")B)"

    # Idade do .snap mais recente = quao fresco esta o backup dos vetores
    local mais_novo idade_s
    mais_novo="$(find "$tnt_dir" -name '*.snap' -printf '%T@
' 2>/dev/null | sort -rn | head -1 | cut -d. -f1)"
    if [ -n "$mais_novo" ]; then
      idade_s=$(( $(date +%s) - mais_novo ))
      log "  .snap mais recente tem $(( idade_s / 60 )) minuto(s)"
      emit tarantool_snap_idade_s "$idade_s"
    fi
  else
    log "  AVISO: $tnt_dir nao existe"
  fi

  emit tarantool_metodo "$metodo"
  emit tarantool_instancias "$qtd"
  emit tarantool_bytes "$total"
  log "Tarantool concluido."
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
    local minimo=$(( necessario * MARGEM_PCT / 100 ))
    if [ "$livre" -lt "$minimo" ]; then
      die "espaço insuficiente em $STAGING: preciso de ~$(numfmt --to=iec $minimo 2>/dev/null || echo $minimo)B, tem $(numfmt --to=iec "$livre" 2>/dev/null || echo "$livre")B"
    fi
  fi

  tar -czf "$WORK/completo/data.tar.gz" -C "$FF_DIR" data \
    || die "falha ao arquivar data/"
  emit data_bytes "$(stat -c %s "$WORK/completo/data.tar.gz")"
  log "data/ concluído."
}

backup_redis() {
  # O Redis costuma ser fila descartavel, mas em algumas aplicacoes
  # guarda estado que nao esta em outro lugar. BGSAVE e barato; salvar
  # e mais seguro do que descobrir depois que fazia falta.
  local conts; conts="$(containers_por_padrao 'redis')"
  [ -z "$conts" ] && { emit redis "ausente"; return 0; }

  log "Snapshot do Redis..."
  mkdir -p "$WORK/redis"
  local total=0

  for c in $conts; do
    log "  container: $c"
    # BGSAVE grava em segundo plano; esperamos o rdb_bgsave_in_progress
    # voltar a 0 antes de copiar, senao pegamos arquivo pela metade.
    if docker exec "$c" redis-cli BGSAVE >/dev/null 2>&1; then
      local espera=0
      while [ "$espera" -lt 60 ]; do
        if docker exec "$c" redis-cli INFO persistence 2>/dev/null \
             | grep -q "rdb_bgsave_in_progress:0"; then
          break
        fi
        sleep 2
        espera=$(( espera + 2 ))
      done

      local rdb
      rdb="$(docker exec "$c" sh -c 'ls /data/*.rdb 2>/dev/null | head -1')"
      if [ -n "$rdb" ] && docker cp "$c:$rdb" "$WORK/redis/${c}.rdb" >/dev/null 2>&1; then
        local sz; sz="$(stat -c %s "$WORK/redis/${c}.rdb" 2>/dev/null || echo 0)"
        total=$(( total + sz ))
        log "    ok ($(numfmt --to=iec "$sz" 2>/dev/null || echo "$sz")B)"
      else
        log "    AVISO: nao encontrei o .rdb dentro do container"
      fi
    else
      log "    AVISO: BGSAVE falhou (Redis sem persistencia habilitada?)"
    fi
  done

  emit redis_bytes "$total"
}

backup_grafana() {
  # Dashboard e fonte de dados do Grafana ficam num SQLite pequeno.
  # Refazer dashboard perdido custa mais tempo do que salvar isso.
  local c; c="$(containers_por_padrao 'grafana' | head -1)"
  [ -z "$c" ] && { emit grafana "ausente"; return 0; }

  log "Copiando Grafana..."
  mkdir -p "$WORK/grafana"
  if docker cp "$c:/var/lib/grafana/grafana.db" "$WORK/grafana/grafana.db" >/dev/null 2>&1; then
    local sz; sz="$(stat -c %s "$WORK/grafana/grafana.db" 2>/dev/null || echo 0)"
    emit grafana_bytes "$sz"
    log "  ok ($(numfmt --to=iec "$sz" 2>/dev/null || echo "$sz")B)"
  else
    log "  AVISO: grafana.db nao encontrado (provisionamento por arquivo?)"
  fi
}

# ── Manifesto ──────────────────────────────────────────────────────────────

escrever_manifesto() {
  # Versao das imagens em uso. O Tarantool NAO e compativel entre versoes
  # maiores do FindFace: restaurar a base biometrica num sistema de outra
  # versao nao funciona. Sem registrar isso aqui, alguem descobriria no
  # pior momento possivel.
  local VERSOES
  VERSOES="$(docker ps --filter "label=com.docker.compose.project=$PROJETO"     --format '{{.Image}}' 2>/dev/null | sed 's|.*/||' | sort -u | head -12 | paste -sd', ')"

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

VERSAO DAS IMAGENS
------------------
$VERSOES

  ATENCAO: a base do Tarantool (vetores faciais) NAO e compativel entre
  versoes maiores do FindFace. Restaurar este backup num sistema de outra
  versao nao vai funcionar para os vetores — os cadastros do PostgreSQL
  ate voltam, mas o reconhecimento nao. Para migrar de versao, use o
  procedimento de atualizacao do fabricante, nao o restore.

Conteúdo
--------
$(cd "$WORK" && find . -type f -not -name MANIFESTO.txt -printf '%10s  %p\n' 2>/dev/null | sort -k2)

Como restaurar
--------------
Perfil config/essencial — restore quente, ver docs/03_RESTORE.md:
  1. configs.tar.gz        -> extrair sobre $FF_DIR/
  2. postgres/<inst>/*.dump -> pg_restore --clean --if-exists por banco,
                               em CADA instancia (postgresql, timescaledb)
  3. tarantool             -> parar os shards, extrair, subir
  4. mongodb/mongodump.gz  -> mongorestore --archive --gzip
  5. etcd/snapshot.db      -> etcdctl snapshot restore
  6. subir o stack e conferir o painel do FindFace

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
# O caminho cadastrado pode estar errado -- ou o FindFace pode nem rodar
# nesta maquina, o que e comum em instalacao distribuida. Em vez de so
# dizer "nao existe", o script PROCURA: o diretorio de trabalho do compose
# de um container em execucao e a resposta autoritativa, porque e de onde a
# propria instalacao subiu.
if [ ! -d "$FF_DIR" ]; then
    ACHADO=""
    if command -v docker >/dev/null 2>&1; then
        ACHADO="$(docker ps --filter label=com.docker.compose.project             --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null             | grep -i -E 'findface|ffmulti' | head -1)"
        if [ -z "$ACHADO" ]; then
            # Sem o rotulo de working_dir (compose v1), tenta pelo nome.
            NOME_CT="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -i -E 'findface|ffmulti' | head -1)"
            if [ -n "$NOME_CT" ]; then
                ACHADO="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$NOME_CT" 2>/dev/null)"
            fi
        fi
    fi
    if [ -z "$ACHADO" ]; then
        for CANDIDATO in /opt/findface-multi /opt/ffmulti /opt/findface /srv/findface-multi; do
            [ -f "$CANDIDATO/docker-compose.yaml" ] || [ -f "$CANDIDATO/docker-compose.yml" ] || continue
            ACHADO="$CANDIDATO"
            break
        done
    fi

    if [ -n "$ACHADO" ] && [ -d "$ACHADO" ]; then
        die "$FF_DIR nao existe neste servidor, mas encontrei a instalacao do FindFace em $ACHADO. Corrija o Diretorio de instalacao em Servidores -> editar."
    fi
    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qi -E 'findface|ffmulti'; then
        die "$FF_DIR nao existe e nenhum container do FindFace roda neste servidor. Provavelmente a aplicacao esta em outra maquina -- veja em Topologia."
    fi
    die "$FF_DIR nao existe. Confira o caminho cadastrado para este servidor."
fi

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
    backup_mongodb
    backup_etcd
    backup_redis
    backup_grafana
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
