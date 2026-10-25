from __future__ import annotations

from typing import Any

import httpx2
import pytest

from am_common.auth import AuthError, AuthUnavailableError, JwksProvider
from am_common.testing import TEST_JWKS_URL, TokenFactory


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class JwksServer:
    """Подменный Keycloak: отдаёт текущий JWKS и считает запросы."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.status_code = 200
        self.requests = 0

    def handler(self, _request: httpx2.Request) -> httpx2.Response:
        self.requests += 1
        return httpx2.Response(self.status_code, json=self.payload)


def provider(server: JwksServer, clock: FakeClock) -> JwksProvider:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(server.handler))
    return JwksProvider(client, TEST_JWKS_URL, ttl=600, min_refresh_interval=30, clock=clock)


async def test_loads_keys_once_and_serves_from_cache(tokens: TokenFactory) -> None:
    server, clock = JwksServer(tokens.jwks()), FakeClock()
    jwks = provider(server, clock)

    first = await jwks.get_key(tokens.kid)
    clock.now += 599
    second = await jwks.get_key(tokens.kid)

    assert first is second
    assert server.requests == 1


async def test_reloads_keys_after_ttl(tokens: TokenFactory) -> None:
    server, clock = JwksServer(tokens.jwks()), FakeClock()
    jwks = provider(server, clock)

    await jwks.get_key(tokens.kid)
    clock.now += 600
    await jwks.get_key(tokens.kid)

    assert server.requests == 2


async def test_unknown_kid_triggers_reload_to_pick_up_rotated_key(tokens: TokenFactory) -> None:
    rotated = TokenFactory(kid="rotated-key")
    server, clock = JwksServer(tokens.jwks()), FakeClock()
    jwks = provider(server, clock)
    await jwks.get_key(tokens.kid)

    server.payload = rotated.jwks()
    clock.now += 30
    key = await jwks.get_key("rotated-key")

    assert key.key_id == "rotated-key"
    assert server.requests == 2


async def test_unknown_kid_does_not_reload_more_often_than_min_interval(
    tokens: TokenFactory,
) -> None:
    server, clock = JwksServer(tokens.jwks()), FakeClock()
    jwks = provider(server, clock)
    await jwks.get_key(tokens.kid)

    clock.now += 29
    with pytest.raises(AuthError, match="unknown signing key"):
        await jwks.get_key("garbage")

    assert server.requests == 1


async def test_ignores_encryption_keys(tokens: TokenFactory) -> None:
    payload = tokens.jwks()
    payload["keys"][0]["use"] = "enc"
    jwks = provider(JwksServer(payload), FakeClock())

    with pytest.raises(AuthError, match="unknown signing key"):
        await jwks.get_key(tokens.kid)


async def test_server_error_means_auth_unavailable(tokens: TokenFactory) -> None:
    server = JwksServer(tokens.jwks())
    server.status_code = 500
    jwks = provider(server, FakeClock())

    with pytest.raises(AuthUnavailableError):
        await jwks.get_key(tokens.kid)


async def test_network_error_means_auth_unavailable(tokens: TokenFactory) -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(refuse))
    jwks = JwksProvider(client, TEST_JWKS_URL)

    with pytest.raises(AuthUnavailableError):
        await jwks.get_key(tokens.kid)


@pytest.mark.parametrize("body", [b"not json", b'{"keys": []}'])
async def test_unusable_response_means_auth_unavailable(tokens: TokenFactory, body: bytes) -> None:
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(200, content=body))
    )
    jwks = JwksProvider(client, TEST_JWKS_URL)

    with pytest.raises(AuthUnavailableError):
        await jwks.get_key(tokens.kid)
