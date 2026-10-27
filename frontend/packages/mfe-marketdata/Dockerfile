# syntax=docker/dockerfile:1
# Сборка из каталога frontend:
#   docker build -f packages/mfe-marketdata/Dockerfile .
FROM node:24-alpine AS build
WORKDIR /src
COPY . .
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
RUN npm run build -w @am/mfe-marketdata

FROM nginx:1.30.6-alpine
COPY packages/mfe-marketdata/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/packages/mfe-marketdata/dist /usr/share/nginx/html
