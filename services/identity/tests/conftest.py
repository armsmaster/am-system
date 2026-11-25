from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx2
import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from am_common.migrations import upgrade
from am_common.testing import TEST_AUDIENCE, TEST_ISSUER, TEST_JWKS_URL, TokenFactory
from identity.main import build_app
from identity.settings import IdentitySettings

KEYCLOAK = "http://keycloak.test/auth"
ADMIN_API = "/auth/admin/realms/am"
REFRESH_KEY = "refresh-token-signing-key-for-tests-only"
REALM_ROLES = {"trading", "operations", "risk", "admin", "price_feed", "offline_access"}


@dataclass
class FakeUser:
    id: str
    username: str
    password: str | None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    enabled: bool = True
    roles: set[str] = field(default_factory=set)
    required_actions: list[str] = field(default_factory=list)

    def representation(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "firstName": self.first_name,
            "lastName": self.last_name,
            "email": self.email,
            "enabled": self.enabled,
            "requiredActions": list(self.required_actions),
            "createdTimestamp": 1,
        }


@dataclass
class FakeKeycloak:
    """Keycloak в памяти: выдача токенов (вход, обновление, выход) и Admin API пользователей."""

    tokens: TokenFactory
    users: dict[str, FakeUser] = field(default_factory=dict)
    direct_grants: bool = True
    locked: set[str] = field(default_factory=set)
    # Срок офлайн-сессии в ответе; 0 — Keycloak его не назвал.
    offline_expires_in: int = 0
    opaque_refresh: bool = False
    reject_passwords: bool = False
    down: bool = False
    token_status: int | None = None
    admin_status: int | None = None
    logout_fails: bool = False
    missing_roles: set[str] = field(default_factory=set)
    no_offline: set[str] = field(default_factory=set)
    sessions: dict[str, str] = field(default_factory=dict)
    scopes: list[str] = field(default_factory=list)
    admin_logouts: list[str] = field(default_factory=list)
    updates: list[dict[str, Any]] = field(default_factory=list)

    def add(self, user: FakeUser) -> FakeUser:
        self.users[user.id] = user
        return user

    def by_name(self, username: str) -> FakeUser | None:
        return next((u for u in self.users.values() if u.username == username.lower()), None)

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        if self.down:
            raise httpx2.ConnectError("keycloak is down", request=request)
        path = request.url.path
        if path.endswith("/protocol/openid-connect/certs"):
            return httpx2.Response(200, json=self.tokens.jwks())
        if path.endswith("/protocol/openid-connect/token"):
            if self.token_status is not None:
                return httpx2.Response(self.token_status)
            return self._token(_form(request))
        if path.endswith("/protocol/openid-connect/logout"):
            if self.logout_fails:
                raise httpx2.ConnectError("keycloak is down", request=request)
            self.sessions.pop(_form(request)["refresh_token"], None)
            return httpx2.Response(204)
        assert request.headers["Authorization"] == "Bearer svc"
        if self.admin_status is not None:
            return httpx2.Response(self.admin_status, json={"errorMessage": "boom"})
        return self._admin(request, path.removeprefix(ADMIN_API))

    # --- выдача токенов ---

    def _token(self, form: dict[str, str]) -> httpx2.Response:
        grant = form["grant_type"]
        if grant == "client_credentials":
            return httpx2.Response(200, json={"access_token": "svc", "expires_in": 300})
        if grant == "refresh_token":
            user_id = self.sessions.get(form["refresh_token"])
            user = self.users.get(user_id or "")
            if user is None or not user.enabled:
                return _grant_error("Token is not active")
            offline = _typ(form["refresh_token"]) == "Offline"
            return self._issue(user, offline=offline)
        assert grant == "password"
        return self._password_grant(form)

    def _password_grant(self, form: dict[str, str]) -> httpx2.Response:
        assert form["client_id"] == "am-web"
        self.scopes.append(form["scope"])
        if not self.direct_grants:
            return httpx2.Response(
                400,
                json={
                    "error": "unauthorized_client",
                    "error_description": "Client not allowed for direct access grants",
                },
            )
        user = self.by_name(form["username"])
        if user is None or user.password is None or user.password != form["password"]:
            return _grant_error("Invalid user credentials", status=401)
        offline = "offline_access" in form["scope"].split()
        refusal = next(
            (
                text
                for text, refused in (
                    ("Account temporarily disabled", user.username in self.locked),
                    ("Account disabled", not user.enabled),
                    ("Account is not fully set up", bool(user.required_actions)),
                    (
                        "Offline tokens not allowed for the user or client",
                        offline and user.username in self.no_offline,
                    ),
                )
                if refused
            ),
            None,
        )
        if refusal is not None:
            return _grant_error(refusal)
        return self._issue(user, offline=offline)

    def _issue(self, user: FakeUser, *, offline: bool) -> httpx2.Response:
        access = self.tokens.issue(
            subject=user.id,
            username=user.username,
            roles=[*sorted(user.roles), "offline_access", "default-roles-am"],
            given_name=user.first_name,
            family_name=user.last_name,
        )
        if self.opaque_refresh:
            refresh = uuid.uuid4().hex
        else:
            claims = {"typ": "Offline" if offline else "Refresh", "sub": user.id}
            refresh = jwt.encode({**claims, "jti": uuid.uuid4().hex}, REFRESH_KEY, "HS256")
        self.sessions[refresh] = user.id
        return httpx2.Response(
            200,
            json={
                "access_token": access,
                "expires_in": 300,
                "refresh_token": refresh,
                "refresh_expires_in": self.offline_expires_in if offline else 1800,
                "token_type": "Bearer",
            },
        )

    # --- Admin API ---

    def _admin(self, request: httpx2.Request, path: str) -> httpx2.Response:
        method = request.method
        body: Any = json.loads(request.content) if request.content else None
        if path == "/users" and method == "GET":
            return self._list(request)
        if path == "/users" and method == "POST":
            return self._create(body)
        match = re.fullmatch(r"/users/([^/]+)(/.*)?", path)
        assert match is not None, f"unexpected request {method} {path}"
        user = self.users.get(match[1])
        if user is None:
            return httpx2.Response(404, json={"error": "User not found"})
        return self._user(method, match[2] or "", user, body)

    def _list(self, request: httpx2.Request) -> httpx2.Response:
        users = list(self.users.values())
        if "username" in request.url.params:
            assert request.url.params["exact"] == "true"
            users = [u for u in users if u.username == request.url.params["username"].lower()]
        return httpx2.Response(200, json=[u.representation() for u in users])

    def _create(self, body: dict[str, Any]) -> httpx2.Response:
        if self.by_name(body["username"]):
            return httpx2.Response(409, json={"errorMessage": "User exists with same username"})
        if body.get("email") and any(u.email == body["email"] for u in self.users.values()):
            return httpx2.Response(409, json={"errorMessage": "User exists with same email"})
        if body["username"] == "bad-profile":
            return httpx2.Response(400, json={"errorMessage": "error-invalid-value"})
        user = self.add(
            FakeUser(
                id=f"u-{body['username']}",
                username=body["username"],
                password=None,
                first_name=body.get("firstName"),
                last_name=body.get("lastName"),
                email=body.get("email"),
                enabled=body["enabled"],
            )
        )
        location = f"{KEYCLOAK}/admin/realms/am/users/{user.id}"
        return httpx2.Response(201, headers={"Location": location})

    def _user(self, method: str, rest: str, user: FakeUser, body: Any) -> httpx2.Response:
        if rest == "" and method == "GET":
            return httpx2.Response(200, json=user.representation())
        if rest == "" and method == "PUT":
            self.updates.append(body)
            user.enabled = body["enabled"]
            user.required_actions = list(body["requiredActions"])
            return httpx2.Response(204)
        if rest == "/reset-password":
            if self.reject_passwords:
                return httpx2.Response(
                    400, json={"error": "invalidPasswordHistoryMessage", "error_description": "x"}
                )
            assert body["temporary"] is False
            user.password = body["value"]
            user.required_actions = [a for a in user.required_actions if a != "UPDATE_PASSWORD"]
            return httpx2.Response(204)
        if rest.startswith("/role-mappings/realm"):
            return self._roles(method, rest, user, body)
        assert rest == "/logout", f"unexpected request {method} {rest}"
        self.admin_logouts.append(user.id)
        self.sessions = {t: uid for t, uid in self.sessions.items() if uid != user.id}
        return httpx2.Response(204)

    def _roles(self, method: str, rest: str, user: FakeUser, body: Any) -> httpx2.Response:
        if rest.endswith("/available"):
            names = sorted(REALM_ROLES - user.roles - self.missing_roles)
        else:
            changed = {role["name"] for role in body or []}
            if method == "POST":
                user.roles |= changed
            elif method == "DELETE":
                user.roles -= changed
            names = sorted(user.roles)
        return httpx2.Response(200, json=[{"id": f"r-{n}", "name": n} for n in names])


def _form(request: httpx2.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


def _grant_error(description: str, *, status: int = 400) -> httpx2.Response:
    return httpx2.Response(
        status, json={"error": "invalid_grant", "error_description": description}
    )


def _typ(refresh_token: str) -> str:
    try:
        return str(jwt.decode(refresh_token, REFRESH_KEY, algorithms=["HS256"])["typ"])
    except jwt.PyJWTError:
        return "Refresh"


@pytest.fixture(scope="session")
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def keycloak(tokens: TokenFactory) -> FakeKeycloak:
    fake = FakeKeycloak(tokens)
    fake.add(FakeUser("u-admin", "admin", "admin-password", "Анна", "Админова", roles={"admin"}))
    fake.add(
        FakeUser(
            "u-trader",
            "trader",
            "trader-password",
            "Тимур",
            "Трейдеров",
            email="trader@am.local",
            roles={"trading"},
        )
    )
    fake.add(FakeUser("u-ops", "ops", "ops-password", roles={"operations"}))
    fake.add(FakeUser("u-svc", "service-account-am-portfolio", None))
    return fake


def bearer(tokens: TokenFactory, subject: str, username: str, *roles: str) -> dict[str, str]:
    token = tokens.issue(subject=subject, username=username, roles=roles)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin(tokens: TokenFactory) -> dict[str, str]:
    """Заголовок запроса от администратора u-admin."""
    return bearer(tokens, "u-admin", "admin", "admin")


@pytest.fixture
def trader(tokens: TokenFactory) -> dict[str, str]:
    return bearer(tokens, "u-trader", "trader", "trading")


@pytest.fixture
async def settings(postgres_url: str, redis_url: str, schema: str) -> IdentitySettings:
    """Настройки сервиса с отдельной, уже мигрированной схемой."""
    settings = IdentitySettings(
        auth_issuer=TEST_ISSUER,
        auth_jwks_url=TEST_JWKS_URL,
        auth_audience=TEST_AUDIENCE,
        database_url=SecretStr(postgres_url),
        database_schema=schema,
        redis_url=SecretStr(redis_url),
        keycloak_url=KEYCLOAK,
        keycloak_client_secret=SecretStr("secret"),
    )
    await asyncio.to_thread(upgrade, settings, "identity:migrations")
    return settings


@pytest.fixture
def client(settings: IdentitySettings, keycloak: FakeKeycloak) -> Iterator[TestClient]:
    with TestClient(build_app(settings, http_transport=keycloak.transport())) as test_client:
        yield test_client
