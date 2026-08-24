# Build a partir da RAIZ do repositório:
#   docker build -f frontend/Dockerfile -t faceops-frontend .
FROM node:20-alpine AS build

WORKDIR /app

COPY frontend/package.json ./
# Sem package-lock no repo: `npm install` resolve. Se um lock for commitado
# depois, trocar para `npm ci` (mais rápido e reprodutível).
RUN npm install --no-audit --no-fund

COPY frontend/src ./src
COPY frontend/public ./public

# CRA trata aviso como erro quando CI=true, e o build do Docker define CI
RUN CI=false npm run build

FROM nginx:1.27-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/build /usr/share/nginx/html
EXPOSE 80
