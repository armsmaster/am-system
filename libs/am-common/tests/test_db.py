"""Проверки с настоящим PostgreSQL в контейнере."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

from am_common.app import create_app
from am_common.db import Database
from am_common.settings import ServiceSettings
from am_common.testing import TokenFactory

type SettingsFactory = Callable[..., ServiceSettings]

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    # Тот же образ, что в compose.yaml.
    with PostgresContainer("postgres:17.11-alpine", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


async def test_connections_use_service_schema(postgres_url: str) -> None:
    database = Database(postgres_url, "refdata")
    try:
        async with database.engine.begin() as connection:
            await connection.execute(text("CREATE SCHEMA IF NOT EXISTS refdata"))
        async with database.session() as session:
            schema = (await session.execute(text("SELECT current_schema()"))).scalar_one()
        await database.ping()
    finally:
        await database.dispose()

    assert schema == "refdata"


def test_ready_when_database_is_reachable(
    postgres_url: str, tokens: TokenFactory, make_settings: SettingsFactory
) -> None:
    settings = make_settings(database_url=SecretStr(postgres_url), database_schema="public")
    app = create_app(settings, http_transport=tokens.transport())

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
