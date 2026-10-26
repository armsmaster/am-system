"""Подключение к БД и готовность сервиса — с настоящими PostgreSQL и Redis."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from am_common.app import DbSession, Resources, create_app
from am_common.db import Database
from am_common.settings import ServiceSettings
from am_common.testing import TokenFactory

type SettingsFactory = Callable[..., ServiceSettings]

pytestmark = pytest.mark.integration


async def test_connections_use_service_schema(postgres_url: str, schema: str) -> None:
    database = Database(postgres_url, schema)
    try:
        async with database.session() as session:
            current = (await session.execute(text("SELECT current_schema()"))).scalar_one()
        await database.ping()
    finally:
        await database.dispose()

    assert current == schema


def test_ready_when_dependencies_are_reachable(
    postgres_url: str, redis_url: str, tokens: TokenFactory, make_settings: SettingsFactory
) -> None:
    settings = make_settings(
        database_url=SecretStr(postgres_url),
        database_schema="public",
        redis_url=SecretStr(redis_url),
    )
    app = create_app(settings, http_transport=tokens.transport())

    with TestClient(app) as client:
        response = client.get("/ready")
        resources: Resources = app.state.resources
        assert resources.require_redis() is resources.redis

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_request_gets_a_session_in_service_schema(
    postgres_url: str, schema: str, tokens: TokenFactory, make_settings: SettingsFactory
) -> None:
    router = APIRouter()

    @router.get("/schema")
    async def current_schema(session: DbSession) -> dict[str, str]:
        return {"schema": (await session.execute(text("SELECT current_schema()"))).scalar_one()}

    settings = make_settings(database_url=SecretStr(postgres_url), database_schema=schema)
    app = create_app(settings, routers=[router], http_transport=tokens.transport())

    with TestClient(app) as client:
        assert client.get("/schema").json() == {"schema": schema}
