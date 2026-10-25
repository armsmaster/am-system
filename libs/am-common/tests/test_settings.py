from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from am_common.settings import ServiceSettings

type SettingsFactory = Callable[..., ServiceSettings]


def test_reads_environment_with_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AM_SERVICE_NAME", "refdata")
    monkeypatch.setenv("AM_AUTH_ISSUER", "http://localhost:8080/auth/realms/am")
    monkeypatch.setenv("AM_AUTH_JWKS_URL", "http://keycloak:8080/auth/realms/am/certs")
    monkeypatch.setenv("AM_DATABASE_URL", "postgresql+asyncpg://svc:secret@db/am")
    monkeypatch.setenv("AM_DATABASE_SCHEMA", "refdata")

    settings = ServiceSettings()

    assert settings.service_name == "refdata"
    assert settings.auth_audience == "am-api"
    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == "postgresql+asyncpg://svc:secret@db/am"


def test_database_password_is_not_printed(make_settings: SettingsFactory) -> None:
    settings = make_settings(
        database_url="postgresql+asyncpg://svc:secret@db/am", database_schema="x"
    )

    assert "secret" not in repr(settings)


def test_database_url_requires_schema(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="database_schema"):
        make_settings(database_url="postgresql+asyncpg://svc:secret@db/am")
