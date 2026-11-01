"""Соседние сервисы: справочник, рыночные данные, портфели.

Справочник и рыночные данные `booking` читает под служебной учётной записью
am-booking. Портфели — с токеном пользователя: сервис портфелей отдаёт ровно
те портфели, что видит пользователь, и доступ проверяется в одном месте.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import httpx2

from am_common.app import Resources
from am_common.service_token import ServiceToken, ServiceTokenError, realm_token_url
from booking.roll import Mark
from booking.settings import BookingSettings
from booking.terms import InstrumentHistory

CACHE_SECONDS = 300.0


class ServiceUnavailableError(Exception):
    """Соседний сервис недоступен или ответил ошибкой."""

    def __init__(self, service: str, detail: str) -> None:
        super().__init__(f"{service}: {detail}")
        self.service = service


class _Client:
    service = ""

    def __init__(self, http: httpx2.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    async def _get(
        self, path: str, authorization: str, *, missing_ok: bool = False, **params: str
    ) -> Any:
        try:
            response = await self._http.get(
                f"{self._base}{path}", params=params, headers={"Authorization": authorization}
            )
        except httpx2.HTTPError as exc:
            raise ServiceUnavailableError(self.service, str(exc)) from exc
        if missing_ok and response.status_code in {404, 422}:
            return None
        if response.is_error:
            raise ServiceUnavailableError(self.service, f"{response.status_code} for {path}")
        return response.json()


class _ServiceClient(_Client):
    """Запросы под служебной учётной записью."""

    def __init__(self, http: httpx2.AsyncClient, base_url: str, token: ServiceToken | None) -> None:
        super().__init__(http, base_url)
        self._token = token

    async def _service_get(self, path: str, *, missing_ok: bool = False, **params: str) -> Any:
        if self._token is None:
            raise ServiceUnavailableError(self.service, "service credentials are not configured")
        try:
            token = await self._token.get()
        except ServiceTokenError as exc:
            raise ServiceUnavailableError(self.service, str(exc)) from exc
        return await self._get(path, f"Bearer {token}", missing_ok=missing_ok, **params)


class RefdataClient(_ServiceClient):
    service = "refdata"

    def __init__(
        self,
        http: httpx2.AsyncClient,
        base_url: str,
        token: ServiceToken | None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(http, base_url, token)
        self._clock = clock
        self._calendars: dict[int, tuple[float, dict[date, bool]]] = {}

    async def history(self, instrument_id: UUID) -> InstrumentHistory | None:
        """Бумага и все версии её условий; нет в справочнике — None."""
        found = await self._service_get(f"/instruments/{instrument_id}", missing_ok=True)
        if found is None:
            return None
        versions = []
        for header in found["versions"]:
            number = header["number"]
            version = (
                found["current"]
                if number == found["current"]["number"]
                else await self._service_get(f"/instruments/{instrument_id}/versions/{number}")
            )
            valid_from = version["valid_from"]
            versions.append(
                (None if valid_from is None else date.fromisoformat(valid_from), version["params"])
            )
        return InstrumentHistory(
            id=instrument_id,
            kind=found["kind"],
            secid=found["secid"],
            short_name=found["short_name"],
            versions=versions,
        )

    async def codes(self) -> dict[str, UUID]:
        """Бумаги справочника по коду: SECID и ISIN в верхнем регистре."""
        payload = cast(list[dict[str, Any]], await self._service_get("/instruments"))
        return {
            code.upper(): UUID(item["id"])
            for item in payload
            for code in (item["secid"], item["isin"])
            if code
        }

    async def calendar(self, year: int) -> dict[date, bool]:
        """Исключения торгового календаря за год; держим в памяти несколько минут."""
        cached = self._calendars.get(year)
        if cached is not None and self._clock() - cached[0] < CACHE_SECONDS:
            return cached[1]
        payload = await self._service_get(f"/calendar/{year}")
        exceptions = {date.fromisoformat(item["day"]): item["is_trading"] for item in payload}
        self._calendars[year] = (self._clock(), exceptions)
        return exceptions

    async def calendar_around(self, *days: date) -> dict[date, bool]:
        """Исключения за годы от первой даты до последней и соседние: даты расчётов
        уходят через Новый год, пересчёт идёт от начала истории фонда."""
        years = range(min(d.year for d in days) - 1, max(d.year for d in days) + 2)
        exceptions: dict[date, bool] = {}
        for year in years:
            exceptions |= await self.calendar(year)
        return exceptions


class MarketdataClient(_ServiceClient):
    service = "marketdata"

    async def on_date(self, instrument_id: UUID, what: str, day: date) -> Decimal | None:
        """НКД (``accrued-interest``) или номинал (``face-value``) на дату; нет — None."""
        found = await self._service_get(
            f"/instruments/{instrument_id}/{what}", missing_ok=True, date=day.isoformat()
        )
        return None if found is None else Decimal(found["value"])

    async def marks(self, instrument_id: UUID, first: date, last: date) -> dict[date, Mark]:
        """Цена для оценки, НКД на T+1 и номинал на каждый день; бумаги нет — пусто."""
        found = await self._service_get(
            f"/instruments/{instrument_id}/marks",
            missing_ok=True,
            **{"from": first.isoformat(), "till": last.isoformat()},
        )
        return {
            date.fromisoformat(item["date"]): Mark(
                price=None if item["price"] is None else Decimal(item["price"]["value"]),
                stale=item["price"] is not None and item["price"]["stale"],
                settle=date.fromisoformat(item["settle_date"]),
                accrued=None if item["accrued"] is None else Decimal(item["accrued"]),
                face=None if item["face_value"] is None else Decimal(item["face_value"]),
            )
            for item in found or []
        }

    async def rates(self, day: date) -> dict[str, Decimal]:
        """Последние известные ставки индексов на дату, % годовых: ключевая, RUONIA, RUSFAR."""
        found = await self._service_get("/rates", date=day.isoformat())
        return {item["rate"]: Decimal(item["value"]) for item in found if item["value"] is not None}

    async def metrics(self, instrument_id: UUID, at: datetime) -> Metrics | None:
        """DV01 и модифицированная дюрация бумаги по цене для оценки на момент (design/04):
        облигация с фиксированным купоном — по доходности, флоатер и линкер — по спреду."""
        found = await self._service_get(
            f"/instruments/{instrument_id}/analytics", missing_ok=True, at=at.isoformat()
        )
        if found is None:
            return None
        if found["spread"] is not None:
            spread = found["spread"]
            return Metrics(
                "spread", Decimal(spread["spread_dv01"]), Decimal(spread["spread_duration"])
            )
        for item in found["yields"]:
            if item["dv01"] is not None:
                return Metrics("yield", Decimal(item["dv01"]), Decimal(item["modified_duration"]))
        return None


@dataclass(frozen=True, slots=True)
class Metrics:
    """Чувствительность бумаги: ``basis`` — к доходности или к спреду (флоатер, линкер)."""

    basis: str
    dv01: Decimal  # ₽ на бумагу за 1 бп
    duration: Decimal  # модифицированная, лет


@dataclass(frozen=True, slots=True)
class PortfolioRef:
    id: UUID
    code: str
    name: str
    kind: str  # compound | physical
    is_fund: bool
    parent_ids: tuple[UUID, ...]
    fund_id: UUID | None
    account_id: UUID | None
    treasury_id: UUID | None


class PortfolioClient(_Client):
    service = "portfolio"

    async def visible(self, authorization: str) -> dict[UUID, PortfolioRef]:
        """Портфели, которые видит пользователь, — по его токену."""
        payload = cast(list[dict[str, Any]], await self._get("/portfolios", authorization))
        return {
            ref.id: ref
            for ref in (
                PortfolioRef(
                    id=UUID(item["id"]),
                    code=item["code"],
                    name=item["name"],
                    kind=item["kind"],
                    is_fund=item["is_fund"],
                    parent_ids=tuple(UUID(p) for p in item["parent_ids"]),
                    fund_id=None if item["fund_id"] is None else UUID(item["fund_id"]),
                    account_id=None if item["account_id"] is None else UUID(item["account_id"]),
                    treasury_id=None if item["treasury_id"] is None else UUID(item["treasury_id"]),
                )
                for item in payload
            )
        }


def physical_under(portfolio_id: UUID, visible: Mapping[UUID, PortfolioRef]) -> list[PortfolioRef]:
    """Physical-портфели под портфелем — каждый один раз (агрегат compound)."""
    children: dict[UUID, list[UUID]] = {}
    for ref in visible.values():
        for parent in ref.parent_ids:
            children.setdefault(parent, []).append(ref.id)
    found: dict[UUID, PortfolioRef] = {}
    stack = [portfolio_id]
    seen: set[UUID] = set()
    while stack:
        current = stack.pop()
        if current in seen or current not in visible:
            continue
        seen.add(current)
        ref = visible[current]
        if ref.kind == "physical":
            found[ref.id] = ref
        stack += children.get(current, [])
    return sorted(found.values(), key=lambda r: r.code)


def service_token(resources: Resources) -> ServiceToken | None:
    settings = cast(BookingSettings, resources.settings)
    secret = settings.keycloak_client_secret
    if secret is None:
        return None
    return ServiceToken(
        resources.http,
        token_url=realm_token_url(settings.keycloak_url, settings.keycloak_realm),
        client_id=settings.keycloak_client_id,
        client_secret=secret.get_secret_value(),
    )
