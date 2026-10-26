# syntax=docker/dockerfile:1
# Один Dockerfile для всех Python-сервисов. Сборка из корня репозитория:
#   docker build -f services/Dockerfile --build-arg SERVICE=portfolio .
ARG SERVICE

FROM python:3.13-slim AS build
ARG SERVICE

RUN --mount=type=cache,target=/root/.cache/pip pip install uv==0.11.20
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY libs ./libs
COPY services/${SERVICE} ./services/${SERVICE}
# Версии пакетов — строго из uv.lock (--frozen).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package ${SERVICE}


FROM python:3.13-slim
ARG SERVICE
ENV SERVICE=${SERVICE} \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1

RUN useradd --system --uid 10001 --no-create-home app
COPY --from=build /app/.venv /app/.venv

USER app
EXPOSE 8000
# При старте: миграции схемы сервиса, затем сервер.
# --root-path: шлюз публикует сервис под /api/<сервис> (см. infra/traefik),
# по нему FastAPI строит ссылки документации API.
CMD ["sh", "-c", "python -m \"$SERVICE.migrate\" && exec uvicorn --factory \"$SERVICE.main:serve\" --host 0.0.0.0 --port 8000 --root-path \"/api/$SERVICE\" --proxy-headers --forwarded-allow-ips '*'"]
