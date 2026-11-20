# syntax=docker/dockerfile:1
# Portal: build apps/web-portal in a node stage, serve with nginx (proxy /api -> api). Context = repo root.
FROM node:22-alpine AS build
WORKDIR /src/apps/web-portal
COPY apps/web-portal/package.json apps/web-portal/package-lock.json ./
RUN --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca; fi; npm ci --no-audit --no-fund
COPY apps/web-portal ./
# same-origin API (/api/v1): do not set VITE_API_BASE
RUN npm run build

FROM nginx:1.27-alpine
COPY infra/portal/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/apps/web-portal/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
