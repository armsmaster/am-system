"""Справочник пользователей — Keycloak.

Сервис читает пользователей и их роли через Admin API Keycloak под
служебной учётной записью (client credentials) с правами только на
чтение пользователей. Свои копии пользователей сервис не хранит.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx2

from am_common.auth import Role


class DirectoryUnavailableError(Exception):
    """Keycloak недоступен или служебная учётная запись не настроена."""


@dataclass(frozen=True, slots=True)
class DirectoryUser:
    subject: str
    username: str
    full_name: str
    enabled: bool
    roles: frozenset[Role]


class UserDirectory(Protocol):
    async def users(self) -> list[DirectoryUser]: ...

    async def user(self, subject: str) -> DirectoryUser | None: ...


class KeycloakDirectory:
    # Токен обновляем заранее, а не в последнюю секунду.
    _TOKEN_MARGIN_SECONDS = 30.0

    def __init__(
        self,
        http: httpx2.AsyncClient,
        *,
        base_url: str,
        realm: str,
        client_id: str,
        client_secret: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._realm = realm
        self._client_id = client_id
        self._client_secret = client_secret
        self._clock = clock
        self._token: str | None = None
        self._token_expires_at = 0.0

    async def users(self) -> list[DirectoryUser]:
        raw = await self._get(f"/admin/realms/{self._realm}/users", params={"max": 1000})
        people = [
            item for item in raw if not str(item.get("username", "")).startswith("service-account-")
        ]
        # Роли — по каждому пользователю: так служебной учётке достаточно права
        # view-users, без доступа к настройкам realm. Пользователей единицы.
        users = [_user(item, await self._roles(item["id"])) for item in people]
        return sorted(users, key=lambda user: user.username)

    async def user(self, subject: str) -> DirectoryUser | None:
        try:
            item = await self._get(f"/admin/realms/{self._realm}/users/{subject}")
        except _NotFoundError:
            return None
        return _user(item, await self._roles(subject))

    async def _roles(self, subject: str) -> frozenset[Role]:
        mappings = await self._get(
            f"/admin/realms/{self._realm}/users/{subject}/role-mappings/realm"
        )
        known = {role.value for role in Role}
        return frozenset(Role(m["name"]) for m in mappings if m.get("name") in known)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        token = await self._access_token()
        try:
            response = await self._http.get(
                f"{self._base}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx2.HTTPError as exc:
            raise DirectoryUnavailableError(str(exc)) from exc
        if response.status_code == httpx2.codes.NOT_FOUND:
            raise _NotFoundError
        if response.is_error:
            raise DirectoryUnavailableError(f"Keycloak answered {response.status_code}")
        return response.json()

    async def _access_token(self) -> str:
        now = self._clock()
        if self._token is not None and now < self._token_expires_at:
            return self._token
        try:
            response = await self._http.post(
                f"{self._base}/realms/{self._realm}/protocol/openid-connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx2.HTTPError as exc:
            raise DirectoryUnavailableError(f"cannot get service token: {exc}") from exc
        self._token = str(payload["access_token"])
        self._token_expires_at = now + float(payload["expires_in"]) - self._TOKEN_MARGIN_SECONDS
        return self._token


class _NotFoundError(Exception):
    pass


def _user(item: dict[str, Any], roles: frozenset[Role]) -> DirectoryUser:
    full_name = " ".join(
        part for part in (item.get("firstName"), item.get("lastName")) if isinstance(part, str)
    )
    return DirectoryUser(
        subject=item["id"],
        username=item["username"],
        full_name=full_name or item["username"],
        enabled=bool(item.get("enabled", True)),
        roles=roles,
    )
