# syntax=docker/dockerfile:1
# Сборка из каталога frontend:
#   docker build -f packages/mfe-refdata/Dockerfile .
FROM node:24-alpine AS build
WORKDIR /src
COPY . .
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
RUN npm run build -w @am/mfe-refdata

FROM nginx:1.30.6-alpine
COPY packages/mfe-refdata/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/packages/mfe-refdata/dist /usr/share/nginx/html
