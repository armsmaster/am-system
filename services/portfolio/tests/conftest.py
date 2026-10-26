from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from am_common.migrations import upgrade
from am_common.testing import TEST_AUDIENCE, TEST_ISSUER, TEST_JWKS_URL, TokenFactory
from portfolio.main import build_app
from portfolio.settings import PortfolioSettings

KEYCLOAK = "http://keycloak.test/auth"


@dataclass
class KeycloakUser:
    subject: str
    username: str
    roles: list[str]
    first_name: str | None = None
    last_name: str | None = None


@dataclass
class FakeKeycloak:
    """Подменный Keycloak: ключи токенов, служебный токен и Admin API пользователей."""

    tokens: TokenFactory
    users: list[KeycloakUser] = field(default_factory=list)
    admin_api_status: int = 200
    token_requests: int = 0

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path.endswith("/protocol/openid-connect/certs"):
            return httpx2.Response(200, json=self.tokens.jwks())
        if path.endswith("/protocol/openid-connect/token"):
            self.token_requests += 1
            return httpx2.Response(200, json={"access_token": "svc", "expires_in": 300})
        if self.admin_api_status != 200:
            return httpx2.Response(self.admin_api_status)
        return self._admin_api(path)

    def _admin_api(self, path: str) -> httpx2.Response:
        by_id = {user.subject: user for user in self.users}
        if path == "/auth/admin/realms/am/users":
            return httpx2.Response(200, json=[self._json(user) for user in self.users])
        if match := re.fullmatch(r"/auth/admin/realms/am/users/([^/]+)/role-mappings/realm", path):
            return httpx2.Response(200, json=[{"name": r} for r in by_id[match[1]].roles])
        if match := re.fullmatch(r"/auth/admin/realms/am/users/([^/]+)", path):
            user = by_id.get(match[1])
            return (
                httpx2.Response(404)
                if user is None
                else httpx2.Response(200, json=self._json(user))
            )
        raise AssertionError(f"unexpected request {path}")

    @staticmethod
    def _json(user: KeycloakUser) -> dict[str, Any]:
        return {
            "id": user.subject,
            "username": user.username,
            "firstName": user.first_name,
            "lastName": user.last_name,
            "enabled": True,
        }


@pytest.fixture(scope="session")
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def keycloak(tokens: TokenFactory) -> FakeKeycloak:
    return FakeKeycloak(
        tokens,
        users=[
            KeycloakUser("u-admin", "admin", ["admin"], "Админ", "Главный"),
            KeycloakUser("u-trader", "trader", ["trading"], "Тимур", "Трейдеров"),
            KeycloakUser("u-ops", "ops", ["operations"]),
            KeycloakUser("u-svc", "service-account-am-portfolio", []),
        ],
    )


@pytest.fixture
async def settings(postgres_url: str, redis_url: str, schema: str) -> PortfolioSettings:
    """Настройки сервиса с отдельной, уже мигрированной схемой."""
    settings = PortfolioSettings(
        auth_issuer=TEST_ISSUER,
        auth_jwks_url=TEST_JWKS_URL,
        auth_audience=TEST_AUDIENCE,
        database_url=SecretStr(postgres_url),
        database_schema=schema,
        redis_url=SecretStr(redis_url),
        keycloak_url=KEYCLOAK,
        keycloak_client_secret=SecretStr("secret"),
    )
    await asyncio.to_thread(upgrade, settings, "portfolio:migrations")
    return settings


@pytest.fixture
def client(settings: PortfolioSettings, keycloak: FakeKeycloak) -> Iterator[TestClient]:
    with TestClient(build_app(settings, http_transport=keycloak.transport())) as test_client:
        yield test_client
