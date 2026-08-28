# Build a partir da RAIZ do repositório:
#   docker build -f backend/Dockerfile -t faceops-backend .
# O contexto precisa ser a raiz porque scripts/ é enviado aos hosts remotos.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Sao_Paulo

# rclone atende o destino Google Drive; openssh-client/rsync atendem o
# envio dos artefatos de backup a partir dos hosts remotos.
#
# `postgresql-client-16` vem do repositório da PGDG, e não do Debian: o
# Debian 12 traz o cliente 15, e `pg_dump` 15 recusa falar com servidor 16
# ("server version mismatch"). O banco do painel é postgres:16-alpine.
# Sem isso, o backup do painel morria em `FileNotFoundError: 'pg_dump'` —
# justamente o backup que protege cadastro, credenciais e auditoria.
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-client rsync curl ca-certificates unzip gnupg \
    && curl -fsSL https://downloads.rclone.org/rclone-current-linux-amd64.zip -o /tmp/rclone.zip \
    && unzip -q -j /tmp/rclone.zip '*/rclone' -d /usr/local/bin \
    && chmod +x /usr/local/bin/rclone \
    && rm -rf /tmp/rclone.zip /var/lib/apt/lists/*

# Cliente do PostgreSQL 16, quando a rede permitir. Ele NAO e obrigatorio:
# o backup do painel exporta o banco por conta propria quando `pg_dump` nao
# existe. A tentativa fica isolada e termina em `|| true` de proposito --
# num ambiente de rede fechada, que e o normal aqui, o build nao pode
# depender de alcancar apt.postgresql.org.
#
# (O cliente do Debian nao serve: e o 15, e pg_dump 15 recusa falar com
# servidor 16, que e a versao do banco do painel.)
RUN set -e; \
    ( apt-get update \
      && apt-get install -y --no-install-recommends gnupg \
      && install -d /usr/share/postgresql-common/pgdg \
      && curl -fsSL --max-time 20 https://www.postgresql.org/media/keys/ACCC4CF8.asc \
           -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
      && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
           > /etc/apt/sources.list.d/pgdg.list \
      && apt-get update \
      && apt-get install -y --no-install-recommends postgresql-client-16 \
    ) || echo "AVISO: sem cliente PostgreSQL; o painel exporta o banco por conta propria"; \
    apt-get purge -y --auto-remove gnupg 2>/dev/null || true; \
    rm -rf /var/lib/apt/lists/* /etc/apt/sources.list.d/pgdg.list

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY scripts /opt/faceops-scripts

EXPOSE 8000

# 1 worker é obrigatório: o APScheduler e as sessões do InTerminal vivem
# em memória no processo. Com 2+ workers os agendamentos duplicariam e o
# WebSocket do terminal cairia em processo sem a sessão.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
