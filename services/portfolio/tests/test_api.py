from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from am_common.testing import TEST_AUDIENCE, TEST_ISSUER, TEST_JWKS_URL, TokenFactory
from portfolio import main
from portfolio.main import build_app
from portfolio.settings import PortfolioSettings


@pytest.fixture(scope="module")
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def settings() -> PortfolioSettings:
    return PortfolioSettings(
        auth_issuer=TEST_ISSUER, auth_jwks_url=TEST_JWKS_URL, auth_audience=TEST_AUDIENCE
    )


@pytest.fixture
def client(settings: PortfolioSettings, tokens: TokenFactory) -> Iterator[TestClient]:
    with TestClient(build_app(settings, http_transport=tokens.transport())) as test_client:
        yield test_client


def test_me_returns_user_and_roles(client: TestClient, tokens: TokenFactory) -> None:
    token = tokens.issue(subject="u-7", username="sidorova", roles=["trading", "operations"])

    response = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {
        "subject": "u-7",
        "username": "sidorova",
        "roles": ["operations", "trading"],
    }


def test_me_requires_token(client: TestClient) -> None:
    assert client.get("/me").status_code == 401


def test_uses_its_own_database_schema(settings: PortfolioSettings) -> None:
    assert settings.database_schema == "portfolio"


def test_serve_reads_environment_and_configures_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AM_AUTH_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("AM_AUTH_JWKS_URL", TEST_JWKS_URL)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(main, "configure_logging", lambda *args: calls.append(args))

    app = main.serve()

    assert app.title == "Portfolio service"
    assert calls == [("portfolio", "INFO")]
