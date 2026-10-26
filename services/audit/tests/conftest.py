from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from pydantic import SecretStr

from am_common.db import Database
from am_common.migrations import upgrade
from am_common.testing import TEST_AUDIENCE, TEST_ISSUER, TEST_JWKS_URL, TokenFactory
from audit.settings import AuditSettings


@pytest.fixture(scope="session")
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
async def settings(postgres_url: str, redis_url: str, schema: str) -> AuditSettings:
    """Настройки сервиса с отдельной, уже мигрированной схемой."""
    settings = AuditSettings(
        auth_issuer=TEST_ISSUER,
        auth_jwks_url=TEST_JWKS_URL,
        auth_audience=TEST_AUDIENCE,
        database_url=SecretStr(postgres_url),
        database_schema=schema,
        redis_url=SecretStr(redis_url),
        streams=[f"am.events.{schema}"],
    )
    await asyncio.to_thread(upgrade, settings, "audit:migrations")
    return settings


@pytest.fixture
async def database(settings: AuditSettings) -> AsyncIterator[Database]:
    assert settings.database_url is not None
    assert settings.database_schema is not None
    db = Database(settings.database_url.get_secret_value(), settings.database_schema)
    yield db
    await db.dispose()
