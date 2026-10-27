from __future__ import annotations

from datetime import date, time

import pytest

from am_common.testing import TEST_ISSUER, TEST_JWKS_URL
from marketdata import main, migrate
from marketdata.settings import MarketdataSettings


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AM_AUTH_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("AM_AUTH_JWKS_URL", TEST_JWKS_URL)


def test_defaults() -> None:
    settings = MarketdataSettings(auth_issuer=TEST_ISSUER, auth_jwks_url=TEST_JWKS_URL)

    assert settings.database_schema == "marketdata"
    assert settings.refdata_url == "http://refdata:8000"
    assert settings.history_from == date(2026, 1, 1)
    assert (settings.intraday_from, settings.intraday_till) == (time(6, 50), time(23, 55))
    assert settings.background_jobs is True


@pytest.mark.usefixtures("environment")
def test_serve_reads_environment_and_configures_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(main, "configure_logging", lambda *args: calls.append(args))

    app = main.serve()

    assert app.title == "Market data service"
    assert calls == [("marketdata", "INFO")]


@pytest.mark.usefixtures("environment")
def test_migrate_applies_service_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, str]] = []
    monkeypatch.setattr(
        migrate, "upgrade", lambda settings, location: calls.append((settings, location))
    )

    migrate.main()

    assert calls[0][1] == "marketdata:migrations"
    assert isinstance(calls[0][0], MarketdataSettings)
