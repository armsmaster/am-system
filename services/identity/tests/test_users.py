"""Пользователи для администратора, ссылки-приглашения и журнал."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
import redis
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from am_common.migrations import schema_diff
from identity.db import metadata
from identity.keycloak import IdentityError, KeycloakAdmin
from identity.main import build_app
from identity.settings import IdentitySettings

if TYPE_CHECKING:
    from conftest import FakeKeycloak

pytestmark = pytest.mark.integration

STREAM = "am.events.identity"

NEW_USER: dict[str, Any] = {
    "username": " Ivan.Petrov ",
    "first_name": "Иван",
    "last_name": "Петров",
    "email": "",
    "roles": ["risk", "trading"],
    "ttl_days": 3,
}


def code(response: Response) -> str:
    return str(response.json()["detail"]["code"])


def create(client: TestClient, admin: dict[str, str], **changes: Any) -> Response:
    return client.post("/users", headers=admin, json={**NEW_USER, **changes})


def by_name(client: TestClient, admin: dict[str, str]) -> dict[str, dict[str, Any]]:
    response = client.get("/users", headers=admin)
    assert response.status_code == 200
    return {user["username"]: user for user in response.json()}


def run_sql(settings: IdentitySettings, sql: str) -> list[Any]:
    assert settings.database_url is not None

    async def go() -> list[Any]:
        engine = create_async_engine(settings.database_url.get_secret_value())  # type: ignore[union-attr]
        async with engine.begin() as connection:
            await connection.execute(text(f'SET search_path TO "{settings.database_schema}"'))
            result = await connection.execute(text(sql))
            rows = list(result.all()) if result.returns_rows else []
        await engine.dispose()
        return rows

    return asyncio.run(go())


# --- схема БД ------------------------------------------------------------------


async def test_migrations_match_the_model(settings: IdentitySettings) -> None:
    assert settings.database_url is not None
    assert settings.database_schema is not None

    diff = await schema_diff(
        settings.database_url.get_secret_value(), settings.database_schema, metadata
    )

    assert diff == []


# --- новый пользователь и ссылка -----------------------------------------------


def test_admin_creates_user_who_sets_password_by_link(
    client: TestClient, admin: dict[str, str], keycloak: FakeKeycloak, settings: IdentitySettings
) -> None:
    response = create(client, admin)

    assert response.status_code == 201
    body = response.json()
    user = body["user"]
    assert user["username"] == "ivan.petrov"
    assert user["full_name"] == "Иван Петров"
    assert user["email"] is None
    assert user["roles"] == ["trading", "risk"]
    assert user["status"] == "pending"
    assert user["last_login_at"] is None
    expires_at = datetime.fromisoformat(body["invite"]["expires_at"])
    assert timedelta(days=2, hours=23) < expires_at - datetime.now(UTC) <= timedelta(days=3)
    assert user["invite_expires_at"] == body["invite"]["expires_at"]
    token = body["invite"]["token"]
    # В базе — только хэш секрета ссылки.
    assert token not in str(run_sql(settings, "SELECT * FROM invitation"))

    info = client.get(f"/public/invites/{token}")
    assert info.status_code == 200
    assert info.json() == {
        "username": "ivan.petrov",
        "full_name": "Иван Петров",
        "expires_at": body["invite"]["expires_at"],
    }

    accepted = client.post(f"/public/invites/{token}/accept", json={"password": "ivan-secret-1"})
    assert accepted.status_code == 200
    assert accepted.json()["user"]["username"] == "ivan.petrov"
    assert "am_refresh=" in accepted.headers["set-cookie"]
    assert "Max-Age" not in accepted.headers["set-cookie"]
    assert keycloak.users["u-ivan.petrov"].password == "ivan-secret-1"

    # Ссылка одноразовая.
    assert code(client.get(f"/public/invites/{token}")) == "invite_used"
    again = client.post(f"/public/invites/{token}/accept", json={"password": "other-secret-1"})
    assert again.status_code == 410
    assert keycloak.users["u-ivan.petrov"].password == "ivan-secret-1"

    ivan = by_name(client, admin)["ivan.petrov"]
    assert ivan["status"] == "active"
    assert ivan["invite_expires_at"] is None
    assert ivan["last_login_at"] is not None


def test_new_link_replaces_previous_one(client: TestClient, admin: dict[str, str]) -> None:
    first = create(client, admin, roles=[]).json()
    assert first["user"]["roles"] == []
    user_id = first["user"]["id"]

    second = client.post(f"/users/{user_id}/invite", headers=admin, json={"ttl_days": 1})

    assert second.status_code == 201
    assert code(client.get(f"/public/invites/{first['invite']['token']}")) == "invite_revoked"
    assert client.get(f"/public/invites/{second.json()['token']}").status_code == 200


def test_expired_link(
    client: TestClient, admin: dict[str, str], settings: IdentitySettings
) -> None:
    token = create(client, admin).json()["invite"]["token"]
    run_sql(settings, "UPDATE invitation SET expires_at = now() - interval '1 minute'")

    response = client.get(f"/public/invites/{token}")

    assert response.status_code == 410
    assert code(response) == "invite_expired"
    assert by_name(client, admin)["ivan.petrov"]["status"] == "expired"


def test_unknown_link(client: TestClient) -> None:
    response = client.get("/public/invites/no-such-token")

    assert response.status_code == 404
    assert code(response) == "invite_not_found"


def test_link_password_rules(client: TestClient, admin: dict[str, str]) -> None:
    token = create(client, admin).json()["invite"]["token"]

    for password in ("short", "IVAN.PETROV"):
        response = client.post(f"/public/invites/{token}/accept", json={"password": password})
        assert code(response) == "weak_password"

    assert client.get(f"/public/invites/{token}").status_code == 200


def test_blocked_user_cannot_use_link(
    client: TestClient, admin: dict[str, str], keycloak: FakeKeycloak
) -> None:
    created = create(client, admin).json()
    keycloak.users[created["user"]["id"]].enabled = False

    response = client.post(
        f"/public/invites/{created['invite']['token']}/accept", json={"password": "ivan-secret-1"}
    )

    assert response.status_code == 403
    assert code(response) == "account_disabled"
    reissued = client.post(f"/users/{created['user']['id']}/invite", headers=admin, json={})
    assert code(reissued) == "user_blocked"


def test_link_resets_forgotten_password(
    client: TestClient, admin: dict[str, str], keycloak: FakeKeycloak
) -> None:
    trader = keycloak.users["u-trader"]
    trader.required_actions = ["UPDATE_PASSWORD"]
    token = client.post("/users/u-trader/invite", headers=admin, json={}).json()["token"]

    response = client.post(
        f"/public/invites/{token}/accept", json={"password": "trader-new-1", "remember": True}
    )

    assert response.status_code == 200
    assert "Max-Age" in response.headers["set-cookie"]
    assert trader.password == "trader-new-1"
    assert trader.required_actions == []


# --- список, роли, блокировка --------------------------------------------------


def test_list_shows_people_with_roles_and_last_login(
    client: TestClient, admin: dict[str, str]
) -> None:
    client.post("/public/auth/login", json={"username": "trader", "password": "trader-password"})

    users = by_name(client, admin)

    assert sorted(users) == ["admin", "ops", "trader"]  # без служебных учётных записей
    trader = users["trader"]
    assert trader["full_name"] == "Тимур Трейдеров"
    assert trader["email"] == "trader@am.local"
    assert trader["roles"] == ["trading"]
    assert trader["status"] == "active"
    assert trader["last_login_at"] is not None
    assert users["ops"]["full_name"] == ""
    assert users["admin"]["last_login_at"] is None


def test_only_admin_manages_users(client: TestClient, trader: dict[str, str]) -> None:
    assert client.get("/users").status_code == 401
    assert client.get("/users", headers=trader).status_code == 403
    assert create(client, trader).status_code == 403


def test_create_refusals(client: TestClient, admin: dict[str, str]) -> None:
    assert code(create(client, admin, username="Trader")) == "username_taken"
    assert code(create(client, admin, email="trader@am.local")) == "email_taken"
    rejected = create(client, admin, username="bad-profile")
    assert code(rejected) == "keycloak_rejected"
    assert "error-invalid-value" in rejected.json()["detail"]["message"]
    assert create(client, admin, username="Иван").status_code == 422
    assert create(client, admin, roles=["price_feed"]).status_code == 422
    assert create(client, admin, ttl_days=30).status_code == 422


def test_change_roles(client: TestClient, admin: dict[str, str], keycloak: FakeKeycloak) -> None:
    response = client.put("/users/u-trader/roles", headers=admin, json={"roles": ["risk"]})

    assert response.status_code == 200
    assert response.json()["roles"] == ["risk"]
    assert keycloak.users["u-trader"].roles == {"risk"}
    same = client.put("/users/u-trader/roles", headers=admin, json={"roles": ["risk"]})
    assert same.json()["roles"] == ["risk"]

    own = client.put("/users/u-admin/roles", headers=admin, json={"roles": ["trading"]})
    assert own.status_code == 409
    assert code(own) == "self_admin"
    service = client.put("/users/u-svc/roles", headers=admin, json={"roles": []})
    assert code(service) == "user_not_found"
    missing = client.put("/users/nobody/roles", headers=admin, json={"roles": []})
    assert missing.status_code == 404

    keycloak.missing_roles.add("operations")
    absent = client.put("/users/u-trader/roles", headers=admin, json={"roles": ["operations"]})
    assert code(absent) == "keycloak_rejected"
    assert keycloak.users["u-trader"].roles == {"risk"}


def test_blocking_ends_sessions(
    client: TestClient, admin: dict[str, str], keycloak: FakeKeycloak
) -> None:
    client.post(
        "/public/auth/login",
        json={"username": "trader", "password": "trader-password", "remember": True},
    )
    remembered = client.cookies.get("am_refresh")
    assert remembered is not None

    blocked = client.put("/users/u-trader/enabled", headers=admin, json={"enabled": False})

    assert blocked.status_code == 200
    assert blocked.json()["status"] == "blocked"
    assert keycloak.admin_logouts == ["u-trader"]
    assert client.post("/public/auth/refresh").status_code == 401
    client.put("/users/u-trader/enabled", headers=admin, json={"enabled": False})
    assert keycloak.admin_logouts == ["u-trader"]

    unblocked = client.put("/users/u-trader/enabled", headers=admin, json={"enabled": True})
    assert unblocked.json()["status"] == "active"
    # «Запомненный» браузер после разблокировки без пароля не войдёт.
    client.cookies.set("am_refresh", remembered, path="/public/auth")
    assert code(client.post("/public/auth/refresh")) == "session_expired"
    # Без офлайн-сессий блокировка тоже проходит.
    ops = client.put("/users/u-ops/enabled", headers=admin, json={"enabled": False})
    assert ops.json()["status"] == "blocked"
    own = client.put("/users/u-admin/enabled", headers=admin, json={"enabled": False})
    assert code(own) == "self_block"


# --- Keycloak недоступен -------------------------------------------------------


def test_keycloak_outage_and_misconfiguration(
    client: TestClient,
    admin: dict[str, str],
    keycloak: FakeKeycloak,
    settings: IdentitySettings,
) -> None:
    for status in (500, 403):
        keycloak.admin_status = status
        response = client.get("/users", headers=admin)
        assert response.status_code == 503
        assert code(response) == "keycloak_unavailable"
    keycloak.admin_status = None
    keycloak.down = True
    assert client.get("/users", headers=admin).status_code == 503
    keycloak.down = False

    unconfigured = settings.model_copy(update={"keycloak_client_secret": None})
    with TestClient(build_app(unconfigured, http_transport=keycloak.transport())) as other:
        assert code(other.get("/users", headers=admin)) == "keycloak_unavailable"


async def test_find_reports_missing_user(keycloak: FakeKeycloak) -> None:
    async with httpx2.AsyncClient(transport=keycloak.transport()) as http:
        directory = KeycloakAdmin(
            http,
            keycloak_url="http://keycloak.test/auth",
            realm="am",
            client_id="am-identity",
            client_secret="secret",
        )
        assert (await directory.find("Trader"))["id"] == "u-trader"
        with pytest.raises(IdentityError) as error:
            await directory.find("nobody")

    assert error.value.code == "user_not_found"


# --- журнал --------------------------------------------------------------------


@pytest.fixture
def stream(settings: IdentitySettings) -> redis.Redis:
    """Поток событий сервиса, очищенный от событий прошлых тестов."""
    assert settings.redis_url is not None
    connection = redis.Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
    connection.delete(STREAM)
    return connection


def test_every_action_goes_to_the_audit_journal(
    stream: redis.Redis,
    client: TestClient,
    admin: dict[str, str],
    trader: dict[str, str],
) -> None:
    created = create(client, admin).json()
    user_id = created["user"]["id"]
    client.post(
        f"/public/invites/{created['invite']['token']}/accept", json={"password": "ivan-secret-1"}
    )
    client.put(f"/users/{user_id}/roles", headers=admin, json={"roles": ["operations"]})
    client.put(f"/users/{user_id}/enabled", headers=admin, json={"enabled": False})
    client.put(f"/users/{user_id}/enabled", headers=admin, json={"enabled": True})
    client.post(
        "/me/password",
        headers=trader,
        json={"current_password": "trader-password", "new_password": "fresh-secret-1"},
    )

    expected = 8
    events: list[dict[str, Any]] = []
    for _ in range(100):
        entries = stream.xrange(STREAM)
        events = [json.loads(fields["event"]) for _id, fields in entries]  # type: ignore[union-attr, index]
        if len(events) >= expected:
            break
        time.sleep(0.05)
    stream.close()

    assert [event["type"] for event in events] == [
        "user.created",
        "user.invited",
        "user.password_set",
        "user.logged_in",
        "user.roles_changed",
        "user.blocked",
        "user.unblocked",
        "user.password_changed",
    ]
    assert events[0]["actor"] == {"subject": "u-admin", "username": "admin"}
    # Формат журнала: пользователь — объект, изменения — «было → стало».
    ivan = {"subject": user_id, "username": "ivan.petrov"}
    assert all(event["data"]["user"] == ivan for event in events[:7])
    assert events[0]["data"]["after"]["roles"] == ["trading", "risk"]
    assert events[1]["data"]["after"]["expires_at"].startswith("20")
    assert events[2]["actor"] == ivan
    assert events[2]["data"]["after"] == {"via": "invite"}
    assert events[3]["data"]["after"] == {"remember": False}
    assert events[4]["data"]["before"] == {"roles": ["trading", "risk"]}
    assert events[4]["data"]["after"] == {"roles": ["operations"]}
    assert events[5]["data"]["after"] == {"enabled": False}
    assert events[6]["data"]["before"] == {"enabled": False}
    assert events[7]["data"] == {"user": {"subject": "u-trader", "username": "trader"}}
