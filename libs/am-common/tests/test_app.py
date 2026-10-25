from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated

import httpx2
import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import SecretStr

from am_common.app import create_app
from am_common.auth import CurrentPrincipal, Principal, Role, require_any_role
from am_common.settings import ServiceSettings
from am_common.testing import TokenFactory

type SettingsFactory = Callable[..., ServiceSettings]

router = APIRouter()


@router.get("/whoami")
async def whoami(principal: CurrentPrincipal) -> dict[str, str]:
    return {"username": principal.username}


@router.get("/trading-desk")
async def trading_desk(
    principal: Annotated[Principal, Depends(require_any_role(Role.TRADING))],
) -> dict[str, str]:
    return {"username": principal.username}


@pytest.fixture
def client(tokens: TokenFactory, make_settings: SettingsFactory) -> Iterator[TestClient]:
    app = create_app(make_settings(), routers=[router], http_transport=tokens.transport())
    with TestClient(app) as test_client:
        yield test_client


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_without_database(client: TestClient) -> None:
    assert client.get("/ready").json() == {"status": "ok"}


def test_ready_reports_unreachable_database(
    tokens: TokenFactory, make_settings: SettingsFactory
) -> None:
    settings = make_settings(
        database_url=SecretStr("postgresql+asyncpg://user:secret@127.0.0.1:1/am"),
        database_schema="test",
    )
    app = create_app(settings, http_transport=tokens.transport())

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_missing_token_is_challenged(client: TestClient) -> None:
    response = client.get("/whoami")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_invalid_token_is_rejected_without_details(client: TestClient) -> None:
    response = client.get("/whoami", headers=bearer("not-a-jwt"))

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid or missing token"}


def test_valid_token_identifies_user(client: TestClient, tokens: TokenFactory) -> None:
    response = client.get("/whoami", headers=bearer(tokens.issue(username="petrov")))

    assert response.status_code == 200
    assert response.json() == {"username": "petrov"}


@pytest.mark.parametrize(
    ("roles", "expected_status"),
    [(["trading"], 200), (["admin"], 200), (["operations"], 403), ([], 403)],
)
def test_endpoint_requires_role(
    client: TestClient, tokens: TokenFactory, roles: list[str], expected_status: int
) -> None:
    response = client.get("/trading-desk", headers=bearer(tokens.issue(roles=roles)))

    assert response.status_code == expected_status


def test_unavailable_keycloak_gives_503(
    tokens: TokenFactory, make_settings: SettingsFactory
) -> None:
    broken = httpx2.MockTransport(lambda _request: httpx2.Response(502))
    app = create_app(make_settings(), routers=[router], http_transport=broken)

    with TestClient(app) as client:
        response = client.get("/whoami", headers=bearer(tokens.issue()))

    assert response.status_code == 503


def test_role_requirement_must_name_a_role() -> None:
    with pytest.raises(ValueError, match="at least one role"):
        require_any_role()
