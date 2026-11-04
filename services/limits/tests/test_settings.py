from __future__ import annotations

import pytest

from am_common.testing import TEST_ISSUER, TEST_JWKS_URL
from limits import main, migrate
from limits.settings import LimitsSettings


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AM_AUTH_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("AM_AUTH_JWKS_URL", TEST_JWKS_URL)


def test_defaults() -> None:
    settings = LimitsSettings(auth_issuer=TEST_ISSUER, auth_jwks_url=TEST_JWKS_URL)

    assert settings.database_schema == "limits"
    assert (settings.portfolio_url, settings.booking_url, settings.refdata_url) == (
        "http://portfolio:8000",
        "http://booking:8000",
        "http://refdata:8000",
    )


@pytest.mark.usefixtures("environment")
def test_serve_reads_environment_and_configures_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(main, "configure_logging", lambda *args: calls.append(args))

    app = main.serve()

    assert app.title == "Limits service"
    assert calls == [("limits", "INFO")]


@pytest.mark.usefixtures("environment")
def test_migrate_applies_service_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[LimitsSettings, str]] = []
    monkeypatch.setattr(migrate, "upgrade", lambda *args: calls.append(args))

    migrate.main()

    [(settings, location)] = calls
    assert (settings.service_name, location) == ("limits", "limits:migrations")
