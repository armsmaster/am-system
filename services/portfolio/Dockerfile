# syntax=docker/dockerfile:1
# Сборка из корня репозитория:
#   docker build -f services/portfolio/Dockerfile .
FROM python:3.13-slim AS build

RUN --mount=type=cache,target=/root/.cache/pip pip install uv==0.11.20
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY libs ./libs
COPY services/portfolio ./services/portfolio
# Версии пакетов — строго из uv.lock (--frozen).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package portfolio


FROM python:3.13-slim

RUN useradd --system --uid 10001 --no-create-home app
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1

USER app
EXPOSE 8000
# --root-path: шлюз публикует сервис под /api/portfolio (см. infra/traefik),
# по нему FastAPI строит ссылки документации API.
CMD ["uvicorn", "--factory", "portfolio.main:serve", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--root-path", "/api/portfolio", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
