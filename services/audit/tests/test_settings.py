from __future__ import annotations

import pytest

from am_common.testing import TEST_ISSUER, TEST_JWKS_URL
from audit import migrate
from audit.settings import AuditSettings


def test_reads_streams_of_audited_services() -> None:
    settings = AuditSettings(auth_issuer=TEST_ISSUER, auth_jwks_url=TEST_JWKS_URL)

    assert settings.streams == ["am.events.portfolio", "am.events.refdata"]
    assert settings.database_schema == "audit"
    assert settings.consumer_name


def test_migrate_applies_service_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AM_AUTH_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("AM_AUTH_JWKS_URL", TEST_JWKS_URL)
    calls: list[tuple[object, str]] = []
    monkeypatch.setattr(
        migrate, "upgrade", lambda settings, location: calls.append((settings, location))
    )

    migrate.main()

    assert calls[0][1] == "audit:migrations"
    assert isinstance(calls[0][0], AuditSettings)
