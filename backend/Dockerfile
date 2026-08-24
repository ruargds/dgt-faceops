# Build a partir da RAIZ do repositório:
#   docker build -f backend/Dockerfile -t faceops-backend .
# O contexto precisa ser a raiz porque scripts/ é enviado aos hosts remotos.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Sao_Paulo

# rclone atende o destino Google Drive; openssh-client/rsync atendem o
# envio dos artefatos de backup a partir dos hosts remotos.
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-client rsync curl ca-certificates unzip \
    && curl -fsSL https://downloads.rclone.org/rclone-current-linux-amd64.zip -o /tmp/rclone.zip \
    && unzip -q -j /tmp/rclone.zip '*/rclone' -d /usr/local/bin \
    && chmod +x /usr/local/bin/rclone \
    && rm -rf /tmp/rclone.zip /var/lib/apt/lists/*

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
