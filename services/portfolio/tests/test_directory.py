"""Клиент справочника пользователей (Keycloak Admin API) — без сети."""

from __future__ import annotations

from typing import Any

import httpx2
import pytest

from am_common.auth import Role
from portfolio.directory import DirectoryUnavailableError, DirectoryUser, KeycloakDirectory

BASE = "http://keycloak.test/auth"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def directory(handler: Any, clock: Clock | None = None) -> KeycloakDirectory:
    return KeycloakDirectory(
        httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        base_url=f"{BASE}/",
        realm="am",
        client_id="am-portfolio",
        client_secret="secret",
        clock=clock or Clock(),
    )


class Keycloak:
    def __init__(self) -> None:
        self.tokens_issued = 0
        self.seen_tokens: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/token"):
            self.tokens_issued += 1
            return httpx2.Response(
                200, json={"access_token": f"t{self.tokens_issued}", "expires_in": 60}
            )
        self.seen_tokens.append(request.headers["Authorization"])
        if request.url.path.endswith("/role-mappings/realm"):
            return httpx2.Response(200, json=[{"name": "admin"}, {"name": "default-roles-am"}])
        if request.url.path.endswith("/users/u-1"):
            return httpx2.Response(200, json={"id": "u-1", "username": "ivanov"})
        return httpx2.Response(404)


async def test_service_token_is_reused_until_it_nearly_expires() -> None:
    keycloak, clock = Keycloak(), Clock()
    client = directory(keycloak, clock)

    await client.user("u-1")
    clock.now = 29  # токен на 60 с, обновляем за 30 с до конца
    await client.user("u-1")
    clock.now = 30
    await client.user("u-1")

    assert keycloak.tokens_issued == 2
    assert keycloak.seen_tokens == ["Bearer t1"] * 4 + ["Bearer t2"] * 2


async def test_user_with_system_roles_only() -> None:
    user = await directory(Keycloak()).user("u-1")

    assert user == DirectoryUser(
        subject="u-1",
        username="ivanov",
        full_name="ivanov",
        enabled=True,
        roles=frozenset({Role.ADMIN}),
    )


async def test_unknown_user_is_none() -> None:
    assert await directory(Keycloak()).user("u-404") is None


@pytest.mark.parametrize(
    "failure",
    ["token_rejected", "admin_api_error", "network_down"],
)
async def test_failures_mean_directory_unavailable(failure: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if failure == "network_down":
            raise httpx2.ConnectError("refused", request=request)
        if request.url.path.endswith("/token"):
            if failure == "token_rejected":
                return httpx2.Response(401)
            return httpx2.Response(200, json={"access_token": "t", "expires_in": 60})
        return httpx2.Response(500)

    with pytest.raises(DirectoryUnavailableError):
        await directory(handler).users()


async def test_admin_api_network_error_after_token() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/token"):
            return httpx2.Response(200, json={"access_token": "t", "expires_in": 60})
        raise httpx2.ReadTimeout("slow", request=request)

    with pytest.raises(DirectoryUnavailableError):
        await directory(handler).user("u-1")
