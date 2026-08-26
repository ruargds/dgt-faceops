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

# Selo do build: entra no bundle e aparece no rodape da barra lateral
# quando nao bate com a revisao do backend. Serve para descobrir em um
# olhar que o navegador esta com index.html em cache apontando para o
# bundle antigo — a causa mais chata de "corrigi / nao resolveu".
ARG BUILD_STAMP=desenvolvimento
ENV REACT_APP_BUILD_STAMP=$BUILD_STAMP

# CI=false porque o CRA trata aviso como erro quando CI=true.
# INLINE_RUNTIME_CHUNK=false tira o script inline do index.html — e o que
# permite a CSP usar script-src 'self' sem unsafe-inline.
RUN CI=false INLINE_RUNTIME_CHUNK=false npm run build

FROM nginx:1.27-alpine
# Template: a imagem do nginx roda envsubst nele na subida, para a
# porta externa do HTTPS entrar no redirecionamento.
COPY frontend/nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/build /usr/share/nginx/html
EXPOSE 80
