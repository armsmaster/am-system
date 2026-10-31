"""Справочник для рыночных данных: бумаги, режим торгов, условия выпуска, календарь.

Сервис читает справочник под своей служебной учётной записью Keycloak.
Список бумаг хранит у себя копией (``instrument``): цены принимаются, даже
когда справочник недоступен. Условия выпуска и календарь держит в памяти
несколько минут — они меняются редко.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import httpx2

from am_common.app import Resources
from am_common.service_token import ServiceToken, ServiceTokenError, realm_token_url
from marketdata.accrual import CouponPeriod
from marketdata.settings import MarketdataSettings

CACHE_SECONDS = 600.0
CACHE_LIMIT = 10_000


class RefdataUnavailableError(Exception):
    """Справочник недоступен или служебная учётная запись не настроена."""


@dataclass(frozen=True, slots=True)
class RefInstrument:
    id: UUID
    secid: str
    isin: str | None
    kind: str
    short_name: str


class RefdataClient:
    def __init__(
        self,
        http: httpx2.AsyncClient,
        base_url: str,
        token: ServiceToken | None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._token = token
        self._clock = clock
        self._cache: dict[tuple[str, ...], tuple[float, Any]] = {}

    async def instruments(self) -> list[RefInstrument]:
        payload = await self._get("/instruments")
        return [
            RefInstrument(
                id=UUID(item["id"]),
                secid=item["secid"],
                isin=item["isin"],
                kind=item["kind"],
                short_name=item["short_name"],
            )
            for item in payload
        ]

    async def board(self, instrument_id: UUID) -> str | None:
        """Основной режим торгов — по нему выбираем строки ISS."""
        payload = await self._get(f"/instruments/{instrument_id}")
        board = payload["current"]["params"]["board"]
        return board if isinstance(board, str) and board else None

    async def coupon_periods(self, instrument_id: UUID, day: date) -> list[CouponPeriod]:
        """Купонные периоды по версии бумаги на дату; до выпуска — пусто."""
        params = await self.terms(instrument_id, day)
        return [] if params is None else _periods(params["coupons"])

    async def terms(self, instrument_id: UUID, day: date) -> dict[str, Any] | None:
        """Условия выпуска по версии бумаги на дату (JSON справочника); до выпуска — None."""
        key = ("terms", str(instrument_id), day.isoformat())
        cached = self._cached(key)
        if cached is not None:
            params: dict[str, Any] | None = cached[0]
            return params
        payload = await self._get(f"/instruments/{instrument_id}/as-of/{day}", missing_ok=True)
        params = None if payload is None else payload["params"]
        # В кортеже: «нет версии» тоже кэшируем.
        self._store(key, (params,))
        return params

    async def calendar(self, year: int) -> dict[date, bool]:
        """Исключения торгового календаря за год: праздники и рабочие выходные."""
        key = ("calendar", str(year))
        cached = self._cached(key)
        if cached is not None:
            exceptions: dict[date, bool] = cached
            return exceptions
        payload = await self._get(f"/calendar/{year}")
        exceptions = {date.fromisoformat(item["day"]): item["is_trading"] for item in payload}
        self._store(key, exceptions)
        return exceptions

    def _cached(self, key: tuple[str, ...]) -> Any:
        hit = self._cache.get(key)
        if hit is None or self._clock() - hit[0] >= CACHE_SECONDS:
            return None
        return hit[1]

    def _store(self, key: tuple[str, ...], value: Any) -> None:
        if len(self._cache) >= CACHE_LIMIT:
            self._cache.clear()
        self._cache[key] = (self._clock(), value)

    async def _get(self, path: str, *, missing_ok: bool = False) -> Any:
        if self._token is None:
            raise RefdataUnavailableError("service credentials for refdata are not configured")
        try:
            token = await self._token.get()
            response = await self._http.get(
                f"{self._base}{path}", headers={"Authorization": f"Bearer {token}"}
            )
        except (ServiceTokenError, httpx2.HTTPError) as exc:
            raise RefdataUnavailableError(str(exc)) from exc
        if missing_ok and response.status_code == httpx2.codes.NOT_FOUND:
            return None
        if response.is_error:
            raise RefdataUnavailableError(f"refdata answered {response.status_code} for {path}")
        return response.json()


def _periods(coupons: list[dict[str, Any]]) -> list[CouponPeriod]:
    return [
        CouponPeriod(
            start=date.fromisoformat(c["start_date"]),
            end=date.fromisoformat(c["date"]),
            amount=None if c["amount"] is None else Decimal(c["amount"]),
        )
        for c in coupons
        if c.get("start_date")
    ]


def refdata_client(resources: Resources) -> RefdataClient:
    """Клиент справочника под служебной учётной записью сервиса."""
    settings = cast(MarketdataSettings, resources.settings)
    secret = settings.keycloak_client_secret
    token = (
        None
        if secret is None
        else ServiceToken(
            resources.http,
            token_url=realm_token_url(settings.keycloak_url, settings.keycloak_realm),
            client_id=settings.keycloak_client_id,
            client_secret=secret.get_secret_value(),
        )
    )
    return RefdataClient(resources.http, settings.refdata_url, token)
