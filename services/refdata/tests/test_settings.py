from __future__ import annotations

from datetime import time

import pytest

from am_common.testing import TEST_ISSUER, TEST_JWKS_URL
from refdata import main, migrate
from refdata.settings import RefdataSettings


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AM_AUTH_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("AM_AUTH_JWKS_URL", TEST_JWKS_URL)


def test_defaults() -> None:
    settings = RefdataSettings(auth_issuer=TEST_ISSUER, auth_jwks_url=TEST_JWKS_URL)

    assert settings.database_schema == "refdata"
    assert settings.iss_url == "https://iss.moex.com/iss"
    assert settings.sync_at == time(6, 0)


@pytest.mark.usefixtures("environment")
def test_serve_reads_environment_and_configures_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(main, "configure_logging", lambda *args: calls.append(args))

    app = main.serve()

    assert app.title == "Reference data service"
    assert calls == [("refdata", "INFO")]


@pytest.mark.usefixtures("environment")
def test_migrate_applies_service_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, str]] = []
    monkeypatch.setattr(
        migrate, "upgrade", lambda settings, location: calls.append((settings, location))
    )

    migrate.main()

    assert calls[0][1] == "refdata:migrations"
    assert isinstance(calls[0][0], RefdataSettings)
