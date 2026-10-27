"""Токен служебной учётной записи Keycloak (client credentials).

Сервис, которому нужно позвать другой сервис или Admin API Keycloak, входит
под своей служебной учётной записью. Токен живёт несколько минут: держим его
в памяти и берём новый заранее, а не в последнюю секунду.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx2


class ServiceTokenError(Exception):
    """Keycloak не выдал токен: недоступен или учётная запись не настроена."""


class ServiceToken:
    # Токен обновляем заранее, а не в последнюю секунду.
    _MARGIN_SECONDS = 30.0

    def __init__(
        self,
        http: httpx2.AsyncClient,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0

    async def get(self) -> str:
        now = self._clock()
        if self._token is not None and now < self._expires_at:
            return self._token
        try:
            response = await self._http.post(
                self._token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx2.HTTPError as exc:
            raise ServiceTokenError(f"cannot get service token: {exc}") from exc
        self._token = str(payload["access_token"])
        self._expires_at = now + float(payload["expires_in"]) - self._MARGIN_SECONDS
        return self._token


def realm_token_url(keycloak_url: str, realm: str) -> str:
    """Адрес выдачи токенов realm: ``{keycloak}/realms/{realm}/protocol/openid-connect/token``."""
    return f"{keycloak_url.rstrip('/')}/realms/{realm}/protocol/openid-connect/token"
