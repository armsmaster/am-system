"""Вход, обновление сессии, выход, первый вход с временным паролем, смена своего пароля."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

if TYPE_CHECKING:
    from conftest import FakeKeycloak

pytestmark = pytest.mark.integration

AUTH = "/public/auth"


def login(
    client: TestClient,
    username: str = "trader",
    password: str = "trader-password",
    *,
    remember: bool = False,
) -> Response:
    return client.post(
        f"{AUTH}/login", json={"username": username, "password": password, "remember": remember}
    )


def code(response: Response) -> str:
    return str(response.json()["detail"]["code"])


def cookie(response: Response) -> str:
    return response.headers.get("set-cookie", "")


# --- вход ----------------------------------------------------------------------


def test_login_returns_token_and_sets_browser_session_cookie(
    client: TestClient, keycloak: FakeKeycloak
) -> None:
    response = login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["expires_in"] == 300
    assert body["access_token"]
    assert body["user"] == {
        "id": "u-trader",
        "username": "trader",
        "display_name": "Тимур Трейдеров",
        "roles": ["trading"],
    }
    # Без «Запомнить меня» — cookie сессии браузера: без срока, недоступна скриптам.
    set_cookie = cookie(response).lower()
    assert set_cookie.startswith("am_refresh=")
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie
    assert "path=/public/auth" in set_cookie
    assert "max-age" not in set_cookie
    assert "secure" not in set_cookie  # тестовый адрес — http
    assert keycloak.scopes == ["openid"]


def test_name_falls_back_to_login(client: TestClient) -> None:
    response = login(client, "OPS", "ops-password")

    assert response.json()["user"]["display_name"] == "ops"


def test_remember_me_keeps_cookie_after_browser_restart(
    client: TestClient, keycloak: FakeKeycloak
) -> None:
    response = login(client, remember=True)

    assert keycloak.scopes == ["openid offline_access"]
    # Keycloak не назвал срок офлайн-сессии — берём свой предел, 30 дней.
    assert "Max-Age=2592000" in cookie(response)

    keycloak.offline_expires_in = 604800
    refreshed = client.post(f"{AUTH}/refresh")

    assert refreshed.status_code == 200
    assert "Max-Age=604800" in cookie(refreshed)


def test_opaque_refresh_token_is_kept_for_the_browser_session(
    client: TestClient, keycloak: FakeKeycloak
) -> None:
    keycloak.opaque_refresh = True

    response = login(client, remember=True)

    assert response.status_code == 200
    assert "Max-Age" not in cookie(response)


def _wrong_password(keycloak: FakeKeycloak) -> None:
    keycloak.users["u-trader"].password = "another-password"


def _unknown_user(keycloak: FakeKeycloak) -> None:
    del keycloak.users["u-trader"]


def _blocked(keycloak: FakeKeycloak) -> None:
    keycloak.users["u-trader"].enabled = False


def _locked(keycloak: FakeKeycloak) -> None:
    keycloak.locked.add("trader")


def _temporary_password(keycloak: FakeKeycloak) -> None:
    keycloak.users["u-trader"].required_actions = ["UPDATE_PASSWORD"]


def _no_direct_grants(keycloak: FakeKeycloak) -> None:
    keycloak.direct_grants = False


def test_remember_me_needs_offline_role(client: TestClient, keycloak: FakeKeycloak) -> None:
    keycloak.no_offline.add("trader")

    response = login(client, remember=True)

    assert response.status_code == 403
    assert code(response) == "remember_not_allowed"
    assert login(client).status_code == 200


@pytest.mark.parametrize(
    ("prepare", "status", "error"),
    [
        (_wrong_password, 401, "invalid_credentials"),
        (_unknown_user, 401, "invalid_credentials"),
        (_blocked, 403, "account_disabled"),
        (_locked, 423, "account_locked"),
        (_temporary_password, 409, "password_change_required"),
        (_no_direct_grants, 503, "login_not_configured"),
    ],
)
def test_login_refusals_are_explained(
    client: TestClient,
    keycloak: FakeKeycloak,
    prepare: Callable[[FakeKeycloak], None],
    status: int,
    error: str,
) -> None:
    prepare(keycloak)

    response = login(client)

    assert response.status_code == status
    assert code(response) == error
    assert "set-cookie" not in response.headers


def test_unexpected_refusal_and_outage(client: TestClient, keycloak: FakeKeycloak) -> None:
    keycloak.token_status = 400
    assert code(login(client)) == "login_failed"

    keycloak.token_status = 502
    response = login(client)
    assert response.status_code == 503
    assert code(response) == "keycloak_unavailable"

    keycloak.token_status = None
    keycloak.down = True
    assert code(login(client)) == "keycloak_unavailable"


# --- сессия --------------------------------------------------------------------


def test_refresh_gives_new_token_by_cookie(client: TestClient) -> None:
    login(client)

    response = client.post(f"{AUTH}/refresh")

    assert response.status_code == 200
    assert response.json()["user"]["username"] == "trader"
    assert "Max-Age" not in cookie(response)


def test_refresh_without_cookie_asks_to_log_in(client: TestClient) -> None:
    response = client.post(f"{AUTH}/refresh")

    assert response.status_code == 401
    assert code(response) == "no_session"


def test_ended_session_clears_cookie(client: TestClient, keycloak: FakeKeycloak) -> None:
    login(client)
    keycloak.sessions.clear()

    response = client.post(f"{AUTH}/refresh")

    assert response.status_code == 401
    assert code(response) == "session_expired"
    assert 'am_refresh=""' in cookie(response)
    assert "Max-Age=0" in cookie(response)
    assert code(client.post(f"{AUTH}/refresh")) == "no_session"


def test_refresh_during_outage_keeps_cookie(client: TestClient, keycloak: FakeKeycloak) -> None:
    login(client)
    keycloak.token_status = 502

    response = client.post(f"{AUTH}/refresh")

    assert response.status_code == 503
    assert "set-cookie" not in response.headers


def test_logout_ends_keycloak_session_and_clears_cookie(
    client: TestClient, keycloak: FakeKeycloak
) -> None:
    login(client)
    assert len(keycloak.sessions) == 1

    response = client.post(f"{AUTH}/logout")

    assert response.status_code == 204
    assert keycloak.sessions == {}
    assert "Max-Age=0" in cookie(response)
    assert client.post(f"{AUTH}/logout").status_code == 204


def test_logout_works_when_keycloak_is_down(client: TestClient, keycloak: FakeKeycloak) -> None:
    login(client)
    keycloak.logout_fails = True

    response = client.post(f"{AUTH}/logout")

    assert response.status_code == 204
    assert "Max-Age=0" in cookie(response)


# --- первый вход с временным паролем -------------------------------------------


def first_login(
    client: TestClient, password: str = "trader-password", new_password: str = "new-secret-123"
) -> Response:
    return client.post(
        f"{AUTH}/first-login",
        json={
            "username": "trader",
            "password": password,
            "new_password": new_password,
            "remember": True,
        },
    )


def test_first_login_replaces_temporary_password(
    client: TestClient, keycloak: FakeKeycloak
) -> None:
    trader = keycloak.users["u-trader"]
    trader.required_actions = ["UPDATE_PASSWORD", "VERIFY_EMAIL"]

    response = first_login(client)

    assert response.status_code == 200
    assert response.json()["user"]["username"] == "trader"
    assert "Max-Age" in cookie(response)
    assert trader.password == "new-secret-123"
    # Страниц Keycloak пользователь не видит — обязательных действий не остаётся.
    assert trader.required_actions == []
    assert login(client, password="new-secret-123").status_code == 200


def test_first_login_rules(client: TestClient, keycloak: FakeKeycloak) -> None:
    assert code(first_login(client, new_password="short")) == "weak_password"
    keycloak.users["u-trader"].required_actions = ["UPDATE_PASSWORD"]
    assert code(first_login(client, new_password="trader-password")) == "same_password"
    assert code(first_login(client, password="wrong-temporary")) == "invalid_credentials"
    assert keycloak.users["u-trader"].password == "trader-password"


def test_first_login_when_password_is_already_set(
    client: TestClient, keycloak: FakeKeycloak
) -> None:
    response = first_login(client)

    assert response.status_code == 409
    assert code(response) == "password_already_set"
    # Проверочный вход закрыт, пароль не тронут.
    assert keycloak.sessions == {}
    assert keycloak.users["u-trader"].password == "trader-password"


# --- смена своего пароля -------------------------------------------------------


def change(client: TestClient, headers: dict[str, str], current: str, new: str) -> Response:
    return client.post(
        "/me/password",
        headers=headers,
        json={"current_password": current, "new_password": new},
    )


def test_change_own_password(
    client: TestClient, keycloak: FakeKeycloak, trader: dict[str, str]
) -> None:
    response = change(client, trader, "trader-password", "fresh-secret-1")

    assert response.status_code == 204
    assert keycloak.users["u-trader"].password == "fresh-secret-1"
    assert keycloak.sessions == {}


def test_change_password_rules(
    client: TestClient, keycloak: FakeKeycloak, trader: dict[str, str]
) -> None:
    assert client.post("/me/password", json={}).status_code == 401
    assert code(change(client, trader, "wrong-password", "fresh-secret-1")) == "wrong_password"
    assert code(change(client, trader, "trader-password", "trader-password")) == "same_password"
    assert code(change(client, trader, "trader-password", "short")) == "weak_password"

    keycloak.reject_passwords = True
    response = change(client, trader, "trader-password", "fresh-secret-1")
    assert response.status_code == 422
    assert code(response) == "weak_password"

    keycloak.locked.add("trader")
    assert code(change(client, trader, "trader-password", "fresh-secret-1")) == "account_locked"
