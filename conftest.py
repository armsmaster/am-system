"""Общие фикстуры тестов всех пакетов: настоящие PostgreSQL и Redis в контейнерах.

Контейнеры стартуют один раз за прогон и только если их запросил
какой-нибудь тест (обычно помеченный ``integration``).
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

# Те же образы, что в compose.yaml.
POSTGRES_IMAGE = "postgres:17.11-alpine"
REDIS_IMAGE = "redis:7.4.11-alpine"

# Docker Desktop на Windows публикует порт служебного контейнера Ryuk
# с задержкой, и testcontainers его не дожидается. Ryuk лишь подчищает
# контейнеры при аварийном завершении тестов; обычную очистку делает
# контекстный менеджер контейнера. В CI (Linux) Ryuk работает.
if sys.platform == "win32":
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer(REDIS_IMAGE) as redis:
        host, port = redis.get_container_host_ip(), redis.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture
async def schema(postgres_url: str) -> AsyncIterator[str]:
    """Пустая схема для одного теста; удаляется после него."""
    name = f"test_{uuid.uuid4().hex[:12]}"
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{name}"'))
    yield name
    async with engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{name}" CASCADE'))
    await engine.dispose()
