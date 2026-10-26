"""Фабрика FastAPI-приложения: одинаковый каркас для всех сервисов.

Что даёт каждому сервису:
- проверку токенов Keycloak;
- ресурсы процесса (HTTP-клиент, БД, Redis) — ``Resources``;
- фоновые задачи (ретранслятор outbox, потребитель событий), которые
  стартуют и останавливаются вместе с приложением;
- пробы ``/health`` (процесс жив) и ``/ready`` (зависимости доступны,
  фоновые задачи работают).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

import httpx2
from fastapi import APIRouter, Depends, FastAPI, Request, Response, status
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from am_common.auth import JwksProvider, TokenVerifier
from am_common.db import Database
from am_common.settings import ServiceSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Resources:
    """Ресурсы процесса: создаются при старте, освобождаются при остановке."""

    settings: ServiceSettings
    http: httpx2.AsyncClient
    database: Database | None
    redis: Redis | None

    def require_database(self) -> Database:
        if self.database is None:
            raise RuntimeError("database is not configured for this service")
        return self.database

    def require_redis(self) -> Redis:
        if self.redis is None:
            raise RuntimeError("redis is not configured for this service")
        return self.redis


# Долгоживущая фоновая задача; останавливается отменой.
Worker = Callable[[Resources], Awaitable[None]]


def create_app(
    settings: ServiceSettings,
    *,
    routers: Sequence[APIRouter] = (),
    workers: Sequence[Worker] = (),
    title: str | None = None,
    http_transport: httpx2.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Собирает приложение сервиса.

    ``http_transport`` подменяет HTTP-транспорт для исходящих запросов
    (Keycloak) — это точка для тестов, в работе не используется.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx2.AsyncClient(transport=http_transport, timeout=5.0) as http:
            resources = Resources(
                settings=settings,
                http=http,
                database=_database(settings),
                redis=_redis(settings),
            )
            app.state.resources = resources
            app.state.token_verifier = TokenVerifier(
                JwksProvider(http, settings.auth_jwks_url),
                issuer=settings.auth_issuer,
                audience=settings.auth_audience,
            )
            tasks = [_start(worker, resources) for worker in workers]
            app.state.workers = tasks
            try:
                yield
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if resources.redis is not None:
                    await resources.redis.aclose()
                if resources.database is not None:
                    await resources.database.dispose()

    app = FastAPI(title=title or settings.service_name, lifespan=lifespan)
    app.include_router(_probes())
    for router in routers:
        app.include_router(router)
    return app


def get_resources(request: Request) -> Resources:
    resources: Resources = request.app.state.resources
    return resources


AppResources = Annotated[Resources, Depends(get_resources)]


async def get_session(resources: AppResources) -> AsyncIterator[AsyncSession]:
    """Сессия БД на запрос. Транзакцию открывает сам обработчик: ``session.begin()``."""
    async with resources.require_database().session() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]


def _database(settings: ServiceSettings) -> Database | None:
    url, schema = settings.database_url, settings.database_schema
    if url is None or schema is None:
        return None
    return Database(url.get_secret_value(), schema)


def _redis(settings: ServiceSettings) -> Redis | None:
    if settings.redis_url is None:
        return None
    return Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)


def _start(worker: Worker, resources: Resources) -> asyncio.Task[None]:
    async def run() -> None:
        await worker(resources)

    task = asyncio.create_task(run(), name=getattr(worker, "__name__", "worker"))
    task.add_done_callback(_report_stopped_worker)
    return task


def _report_stopped_worker(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    # Фоновая задача не должна завершаться сама: это ошибка, /ready её покажет.
    logger.error("background worker %s stopped: %r", task.get_name(), exc)


def _probes() -> APIRouter:
    router = APIRouter(tags=["probes"])

    @router.get("/health")
    async def health() -> dict[str, str]:
        """Процесс жив и отвечает."""
        return {"status": "ok"}

    @router.get("/ready")
    async def ready(request: Request, response: Response) -> dict[str, object]:
        """Зависимости доступны и фоновые задачи работают."""
        resources: Resources = request.app.state.resources
        workers: list[asyncio.Task[None]] = request.app.state.workers
        failed: list[str] = []
        if resources.database is not None:
            try:
                await resources.database.ping()
            except (OSError, SQLAlchemyError) as exc:
                logger.warning("database is not ready: %s", exc)
                failed.append("database")
        if resources.redis is not None:
            try:
                await resources.redis.ping()
            except (OSError, RedisError) as exc:
                logger.warning("redis is not ready: %s", exc)
                failed.append("redis")
        failed.extend(task.get_name() for task in workers if task.done())
        if failed:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "unavailable", "failed": failed}
        return {"status": "ok"}

    return router
