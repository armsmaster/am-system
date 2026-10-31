# syntax=docker/dockerfile:1
# Сборка из каталога frontend:
#   docker build -f packages/mfe-booking/Dockerfile .
FROM node:24-alpine AS build
WORKDIR /src
COPY . .
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
RUN npm run build -w @am/mfe-booking

FROM nginx:1.30.6-alpine
COPY packages/mfe-booking/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/packages/mfe-booking/dist /usr/share/nginx/html
