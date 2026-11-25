"""Keycloak: вход по паролю и Admin API.

Вход — от имени публичного клиента ``am-web`` (grant ``password``): Keycloak
сам проверяет пароль, блокировку и подбор пароля. Пользователей, роли и
пароли меняем через Admin API под служебной учётной записью ``am-identity``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import httpx2

from am_common.service_token import ServiceToken, ServiceTokenError, realm_token_url

logger = logging.getLogger(__name__)


class IdentityError(Exception):
    """Отказ или нарушение правил: HTTP-статус, код и сообщение для пользователя."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class KeycloakUnavailableError(Exception):
    """Keycloak не отвечает или служебная учётная запись не настроена."""


def session_expired() -> IdentityError:
    return IdentityError(401, "session_expired", "Сессия закончилась — войдите снова.")


@dataclass(frozen=True, slots=True)
class Tokens:
    access_token: str
    expires_in: int
    refresh_token: str
    # 0 — Keycloak не назвал срок refresh token.
    refresh_expires_in: int


# Отказы Keycloak при входе по паролю: текст ошибки → что ответить пользователю.
_LOGIN_ERRORS: dict[str, tuple[int, str, str]] = {
    "Invalid user credentials": (401, "invalid_credentials", "Неверный логин или пароль."),
    "Account disabled": (
        403,
        "account_disabled",
        "Учётная запись заблокирована. Обратитесь к администратору.",
    ),
    "Account temporarily disabled": (
        423,
        "account_locked",
        "Слишком много неудачных попыток входа. Повторите через несколько минут.",
    ),
    # Так Keycloak отвечает, только если пароль верный: временный пароль пора сменить.
    "Account is not fully set up": (
        409,
        "password_change_required",
        "Задайте свой пароль вместо временного.",
    ),
    # Нет роли offline_access: «Запомнить меня» этой учётной записи недоступна.
    "Offline tokens not allowed for the user or client": (
        403,
        "remember_not_allowed",
        "«Запомнить меня» недоступна этой учётной записи. Войдите без галочки "
        "или попросите администратора выдать роль offline_access в Keycloak.",
    ),
    "Client not allowed for direct access grants": (
        503,
        "login_not_configured",
        "Вход не настроен в Keycloak. Обратитесь к администратору.",
    ),
}
_UNKNOWN_LOGIN_ERROR = (
    503,
    "login_failed",
    "Keycloak отказал во входе. Обратитесь к администратору.",
)


class KeycloakAuth:
    def __init__(
        self, http: httpx2.AsyncClient, *, keycloak_url: str, realm: str, client_id: str
    ) -> None:
        self._http = http
        endpoints = f"{keycloak_url.rstrip('/')}/realms/{realm}/protocol/openid-connect"
        self._token_url = f"{endpoints}/token"
        self._logout_url = f"{endpoints}/logout"
        self._client_id = client_id

    async def login(self, username: str, password: str, *, remember: bool) -> Tokens:
        """Вход по паролю. «Запомнить меня» — офлайн-сессия: живёт после закрытия браузера."""
        response = await self._post(
            {
                "grant_type": "password",
                "client_id": self._client_id,
                "username": username,
                "password": password,
                "scope": "openid offline_access" if remember else "openid",
            }
        )
        if response.status_code == httpx2.codes.OK:
            return _tokens(response)
        description = _error_description(response)
        if description not in _LOGIN_ERRORS:
            logger.warning("keycloak refused login: %s %s", response.status_code, description)
        status, code, message = _LOGIN_ERRORS.get(description, _UNKNOWN_LOGIN_ERROR)
        raise IdentityError(status, code, message)

    async def refresh(self, refresh_token: str) -> Tokens:
        response = await self._post(
            {
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "refresh_token": refresh_token,
            }
        )
        if response.status_code == httpx2.codes.OK:
            return _tokens(response)
        # Сессия истекла, пользователь заблокирован или токен отозван.
        raise session_expired()

    async def logout(self, refresh_token: str) -> None:
        """Завершает сессию в Keycloak. Не вышло — не страшно: cookie всё равно удаляем."""
        try:
            await self._http.post(
                self._logout_url,
                data={"client_id": self._client_id, "refresh_token": refresh_token},
            )
        except httpx2.HTTPError as exc:
            logger.warning("keycloak logout failed: %s", exc)

    async def _post(self, form: dict[str, str]) -> httpx2.Response:
        try:
            response = await self._http.post(self._token_url, data=form)
        except httpx2.HTTPError as exc:
            raise KeycloakUnavailableError(f"token endpoint: {exc}") from exc
        if response.status_code >= httpx2.codes.INTERNAL_SERVER_ERROR:
            raise KeycloakUnavailableError(f"token endpoint: {response.status_code}")
        return response


class KeycloakAdmin:
    """Пользователи realm через Admin API под служебной учётной записью."""

    def __init__(
        self,
        http: httpx2.AsyncClient,
        *,
        keycloak_url: str,
        realm: str,
        client_id: str,
        client_secret: str,
        login_client_id: str = "am-web",
    ) -> None:
        self._http = http
        self._base = f"{keycloak_url.rstrip('/')}/admin/realms/{realm}"
        self._login_client = login_client_id
        self._token = ServiceToken(
            http,
            token_url=realm_token_url(keycloak_url, realm),
            client_id=client_id,
            client_secret=client_secret,
        )

    async def users(self) -> list[dict[str, Any]]:
        params = {"max": "1000", "briefRepresentation": "true"}
        return _json_list(await self._call("GET", "/users", params=params))

    async def user(self, user_id: str) -> dict[str, Any]:
        response = await self._call("GET", f"/users/{user_id}")
        if response.status_code == httpx2.codes.NOT_FOUND:
            raise IdentityError(404, "user_not_found", "Пользователь не найден в Keycloak.")
        return cast(dict[str, Any], response.json())

    async def find(self, username: str) -> dict[str, Any]:
        params = {"username": username, "exact": "true"}
        found = _json_list(await self._call("GET", "/users", params=params))
        if not found:
            raise IdentityError(404, "user_not_found", "Пользователь не найден в Keycloak.")
        return found[0]

    async def create(self, representation: dict[str, Any]) -> str:
        """Заводит пользователя; возвращает его id."""
        response = await self._call("POST", "/users", json=representation)
        if response.status_code == httpx2.codes.CONFLICT:
            if "email" in str(response.json().get("errorMessage", "")):
                raise IdentityError(
                    409, "email_taken", "Этот email уже есть у другого пользователя."
                )
            raise IdentityError(409, "username_taken", "Такой логин уже занят.")
        _check(response)
        return response.headers["Location"].rsplit("/", 1)[-1]

    async def update(self, user_id: str, representation: dict[str, Any]) -> None:
        _check(await self._call("PUT", f"/users/{user_id}", json=representation))

    async def set_password(self, user_id: str, password: str) -> None:
        """Постоянный пароль: Keycloak заодно снимает требование сменить временный."""
        response = await self._call(
            "PUT",
            f"/users/{user_id}/reset-password",
            json={"type": "password", "value": password, "temporary": False},
        )
        if response.status_code == httpx2.codes.BAD_REQUEST:
            raise IdentityError(422, "weak_password", "Пароль не подходит под правила Keycloak.")
        _check(response)

    async def realm_roles(self, user_id: str) -> set[str]:
        mappings = _json_list(await self._call("GET", f"/users/{user_id}/role-mappings/realm"))
        return {str(role["name"]) for role in mappings}

    async def add_roles(self, user_id: str, names: Sequence[str]) -> None:
        await self._map_roles("POST", user_id, names)

    async def remove_roles(self, user_id: str, names: Sequence[str]) -> None:
        await self._map_roles("DELETE", user_id, names)

    async def logout(self, user_id: str) -> None:
        """Завершает все сессии пользователя, «запомненные» тоже.

        ``logout`` Keycloak закрывает только обычные сессии. Офлайн («Запомнить меня»)
        закрывает отзыв согласия клиента входа — иначе после разблокировки старый
        браузер вошёл бы без пароля. Офлайн-сессий нет — Keycloak отвечает 404.
        """
        _check(await self._call("POST", f"/users/{user_id}/logout"))
        response = await self._call("DELETE", f"/users/{user_id}/consents/{self._login_client}")
        if response.status_code != httpx2.codes.NOT_FOUND:
            _check(response)

    async def _map_roles(self, method: str, user_id: str, names: Sequence[str]) -> None:
        """Роль берём из доступных пользователю (добавить) или назначенных (снять).

        Так хватает прав на пользователей: читать роли realm напрямую служебной учётной
        записи не нужно.
        """
        path = f"/users/{user_id}/role-mappings/realm"
        source = f"{path}/available" if method == "POST" else path
        pool = _json_list(await self._call("GET", source))
        roles = [role for role in pool if role["name"] in names]
        if len(roles) != len(names):
            raise IdentityError(
                422, "keycloak_rejected", f"В Keycloak нет ролей: {', '.join(sorted(names))}."
            )
        _check(await self._call(method, path, json=roles))

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, str] | None = None,
    ) -> httpx2.Response:
        try:
            token = await self._token.get()
            response = await self._http.request(
                method,
                self._base + path,
                json=json,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        except (httpx2.HTTPError, ServiceTokenError) as exc:
            raise KeycloakUnavailableError(f"admin api: {exc}") from exc
        # 401/403 — у служебной учётной записи нет прав: это настройка, а не ошибка пользователя.
        if response.status_code >= httpx2.codes.INTERNAL_SERVER_ERROR or response.status_code in {
            httpx2.codes.UNAUTHORIZED,
            httpx2.codes.FORBIDDEN,
        }:
            raise KeycloakUnavailableError(f"admin api {method} {path}: {response.status_code}")
        return response


def _tokens(response: httpx2.Response) -> Tokens:
    payload = response.json()
    return Tokens(
        access_token=str(payload["access_token"]),
        expires_in=int(payload["expires_in"]),
        refresh_token=str(payload["refresh_token"]),
        refresh_expires_in=int(payload.get("refresh_expires_in") or 0),
    )


def _error_description(response: httpx2.Response) -> str:
    try:
        return str(response.json().get("error_description", ""))
    except ValueError:
        return ""


def _json_list(response: httpx2.Response) -> list[dict[str, Any]]:
    _check(response)
    return cast(list[dict[str, Any]], response.json())


def _check(response: httpx2.Response) -> None:
    if response.status_code >= httpx2.codes.BAD_REQUEST:
        raise IdentityError(
            422,
            "keycloak_rejected",
            f"Keycloak отклонил изменение ({response.status_code}): {response.text[:300]}",
        )
