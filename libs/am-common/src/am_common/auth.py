"""Аутентификация и авторизация.

Пользователь входит через Keycloak, сервис получает access token (JWT) в
заголовке ``Authorization: Bearer``. Сервис проверяет подпись токена по
публичным ключам Keycloak, issuer, audience и срок действия, а затем
извлекает роли.

Права на портфели — не здесь: это доменные данные сервиса portfolio.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any

import httpx2
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# Keycloak подписывает токены RS256. Список фиксирован, чтобы исключить
# подмену алгоритма в заголовке токена.
_ALGORITHMS = ["RS256"]


class Role(StrEnum):
    TRADING = "trading"
    OPERATIONS = "operations"  # сопровождение
    RISK = "risk"  # риск-менеджмент: рейтинги, классификаторы, лимиты
    ADMIN = "admin"
    PRICE_FEED = "price_feed"  # скрипт цен — служебная учётная запись, не человек
    TRADE_FEED = "trade_feed"  # скрипт сделок — служебная учётная запись, не человек
    STATEMENT_FEED = "statement_feed"  # скрипт выписок брокера — служебная учётная запись
    CLOSE_DAY = "close_day"  # сервис сверки закрывает день фонда в booking
    # Сервис лимитов читает позиции, СЧА и сделки любого фонда — для проверки сделки
    # до ввода у трейдера, который ведёт только портфель фонда (решение 147).
    FUND_READER = "fund_reader"


@dataclass(frozen=True, slots=True)
class Principal:
    """Аутентифицированный пользователь."""

    subject: str
    username: str
    roles: frozenset[Role]

    def has_role(self, role: Role) -> bool:
        # Админ видит всё и может всё.
        return Role.ADMIN in self.roles or role in self.roles


class AuthError(Exception):
    """Токен отсутствует, повреждён, просрочен или выпущен не для нас."""


class AuthUnavailableError(Exception):
    """Не удалось получить ключи Keycloak — проверить токен сейчас нельзя."""


class JwksProvider:
    """Публичные ключи Keycloak с кэшем.

    Ключи перечитываются раз в ``ttl`` секунд, а также когда в токене
    встречается неизвестный ``kid`` (Keycloak ротирует ключи), но не чаще
    раза в ``min_refresh_interval`` секунд — чтобы мусорные токены не
    превращались в поток запросов к Keycloak.
    """

    def __init__(
        self,
        client: httpx2.AsyncClient,
        url: str,
        *,
        ttl: float = 600.0,
        min_refresh_interval: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._url = url
        self._ttl = ttl
        self._min_refresh_interval = min_refresh_interval
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None

    async def get_key(self, kid: str) -> jwt.PyJWK:
        now = self._clock()
        if (
            self._fetched_at is None
            or now - self._fetched_at >= self._ttl
            or (kid not in self._keys and now - self._fetched_at >= self._min_refresh_interval)
        ):
            await self._refresh(now)

        key = self._keys.get(kid)
        if key is None:
            raise AuthError(f"unknown signing key {kid!r}")
        return key

    async def _refresh(self, now: float) -> None:
        try:
            response = await self._client.get(self._url)
            response.raise_for_status()
            key_set = jwt.PyJWKSet.from_dict(response.json())
        except (httpx2.HTTPError, ValueError, jwt.PyJWKSetError) as exc:
            raise AuthUnavailableError(f"cannot load signing keys: {exc}") from exc

        # В JWKS Keycloak есть и ключи шифрования (use=enc) — они нам не нужны.
        self._keys = {
            key.key_id: key
            for key in key_set.keys
            if key.key_id and key.public_key_use in (None, "sig")
        }
        self._fetched_at = now


class TokenVerifier:
    """Проверяет access token и превращает его в ``Principal``."""

    def __init__(
        self, jwks: JwksProvider, *, issuer: str, audience: str, leeway: float = 30.0
    ) -> None:
        self._jwks = jwks
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway

    async def verify(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise AuthError(f"malformed token: {exc}") from exc

        kid = header.get("kid")
        if not isinstance(kid, str):
            raise AuthError("token has no key id")

        key = await self._jwks.get_key(kid)
        try:
            claims = jwt.decode(
                token,
                key=key.key,
                algorithms=_ALGORITHMS,
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.InvalidTokenError as exc:
            raise AuthError(str(exc)) from exc

        return principal_from_claims(claims)


def principal_from_claims(claims: dict[str, Any]) -> Principal:
    """Роли берём из ``realm_access.roles``; чужие роли Keycloak игнорируем."""
    realm_access = claims.get("realm_access")
    raw_roles = realm_access.get("roles") if isinstance(realm_access, dict) else None
    known = {role.value for role in Role}
    roles = frozenset(
        Role(name)
        for name in (raw_roles if isinstance(raw_roles, list) else [])
        if isinstance(name, str) and name in known
    )
    subject = str(claims["sub"])
    username = claims.get("preferred_username")
    return Principal(
        subject=subject,
        username=username if isinstance(username, str) else subject,
        roles=roles,
    )


_bearer = HTTPBearer(auto_error=False)


async def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise _unauthorized()

    verifier: TokenVerifier = request.app.state.token_verifier
    try:
        return await verifier.verify(credentials.credentials)
    except AuthError as exc:
        # Причину пишем в лог, клиенту отдаём общее сообщение.
        logger.info("rejected token: %s", exc)
        raise _unauthorized() from exc
    except AuthUnavailableError as exc:
        logger.error("token verification unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication temporarily unavailable",
        ) from exc


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_any_role(*roles: Role) -> Callable[..., Awaitable[Principal]]:
    """Зависимость FastAPI: пускает пользователя хотя бы с одной из ролей."""
    if not roles:
        raise ValueError("at least one role is required")

    async def dependency(principal: CurrentPrincipal) -> Principal:
        if not any(principal.has_role(role) for role in roles):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        return principal

    return dependency


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid or missing token",
        headers={"WWW-Authenticate": "Bearer"},
    )
