from __future__ import annotations

import pytest

from am_common.testing import TEST_ISSUER, TEST_JWKS_URL
from identity import main, migrate
from identity.settings import IdentitySettings


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AM_AUTH_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("AM_AUTH_JWKS_URL", TEST_JWKS_URL)


def test_defaults() -> None:
    settings = IdentitySettings(auth_issuer=TEST_ISSUER, auth_jwks_url=TEST_JWKS_URL)

    assert settings.database_schema == "identity"
    assert settings.keycloak_client_id == "am-identity"
    assert settings.login_client_id == "am-web"
    assert settings.keycloak_client_secret is None
    assert settings.remember_max_age == 30 * 24 * 3600


@pytest.mark.usefixtures("environment")
def test_serve_reads_environment_and_configures_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(main, "configure_logging", lambda *args: calls.append(args))

    app = main.serve()

    assert app.title == "Identity service"
    assert calls == [("identity", "INFO")]


@pytest.mark.usefixtures("environment")
def test_migrate_applies_service_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, str]] = []
    monkeypatch.setattr(
        migrate, "upgrade", lambda settings, location: calls.append((settings, location))
    )

    migrate.main()

    assert calls[0][1] == "identity:migrations"
    assert isinstance(calls[0][0], IdentitySettings)
