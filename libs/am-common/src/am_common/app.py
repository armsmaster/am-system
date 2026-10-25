"""Фабрика FastAPI-приложения: одинаковый каркас для всех сервисов.

Что даёт каждому сервису:
- проверку токенов Keycloak (``app.state.token_verifier``);
- подключение к своей схеме БД, если она настроена (``app.state.database``);
- пробы ``/health`` (процесс жив) и ``/ready`` (зависимости доступны).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import httpx2
from fastapi import APIRouter, FastAPI, Request, Response, status
from sqlalchemy.exc import SQLAlchemyError

from am_common.auth import JwksProvider, TokenVerifier
from am_common.db import Database
from am_common.settings import ServiceSettings

logger = logging.getLogger(__name__)


def create_app(
    settings: ServiceSettings,
    *,
    routers: Sequence[APIRouter] = (),
    title: str | None = None,
    http_transport: httpx2.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Собирает приложение сервиса.

    ``http_transport`` подменяет HTTP-транспорт для запросов к Keycloak —
    это точка для тестов, в работе не используется.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx2.AsyncClient(transport=http_transport, timeout=5.0) as http:
            app.state.token_verifier = TokenVerifier(
                JwksProvider(http, settings.auth_jwks_url),
                issuer=settings.auth_issuer,
                audience=settings.auth_audience,
            )
            database = _database(settings)
            app.state.database = database
            try:
                yield
            finally:
                if database is not None:
                    await database.dispose()

    app = FastAPI(title=title or settings.service_name, lifespan=lifespan)
    app.include_router(_probes())
    for router in routers:
        app.include_router(router)
    return app


def _database(settings: ServiceSettings) -> Database | None:
    url, schema = settings.database_url, settings.database_schema
    if url is None or schema is None:
        return None
    return Database(url.get_secret_value(), schema)


def _probes() -> APIRouter:
    router = APIRouter(tags=["probes"])

    @router.get("/health")
    async def health() -> dict[str, str]:
        """Процесс жив и отвечает."""
        return {"status": "ok"}

    @router.get("/ready")
    async def ready(request: Request, response: Response) -> dict[str, str]:
        """Зависимости доступны — можно принимать трафик."""
        database: Database | None = request.app.state.database
        if database is not None:
            try:
                await database.ping()
            except (OSError, SQLAlchemyError) as exc:
                logger.warning("database is not ready: %s", exc)
                response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
                return {"status": "unavailable"}
        return {"status": "ok"}

    return router
