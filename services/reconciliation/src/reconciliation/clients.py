"""Соседние сервисы: портфели, сделки, справочник.

Сверка ходит к ним с токеном того, кто спрашивает, — пользователя или скрипта
брокера: сервис портфелей отдаёт ровно те фонды и счета, что тот видит, сервис
сделок проверяет доступ сам. Своей служебной учётной записи у сверки нет.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from http import HTTPStatus
from typing import Any, cast
from uuid import UUID

import httpx2

from am_common.app import Resources
from am_common.events import Actor
from am_common.service_token import ServiceToken, ServiceTokenError, realm_token_url
from reconciliation.matching import SystemDay, SystemMovement, SystemTrade
from reconciliation.settings import ReconciliationSettings


class ServiceUnavailableError(Exception):
    """Соседний сервис недоступен или ответил ошибкой."""

    def __init__(self, service: str, detail: str) -> None:
        super().__init__(f"{service}: {detail}")
        self.service = service


class RefusedError(Exception):
    """Сосед отказал по правилам: сообщение — для пользователя."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _Client:
    service = ""

    def __init__(self, http: httpx2.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    async def _get(self, path: str, authorization: str, **params: str) -> Any:
        return await self._send("GET", path, authorization, params=params)

    async def _send(
        self,
        method: str,
        path: str,
        authorization: str,
        *,
        params: dict[str, str] | None = None,
        body: Any = None,
    ) -> Any:
        """Запрос к соседу. Отказ по правилам (422) — его сообщение пользователю,
        остальные ошибки — «сервис недоступен»."""
        try:
            response = await self._http.request(
                method,
                f"{self._base}{path}",
                params=params,
                json=body,
                headers={"Authorization": authorization},
            )
        except httpx2.HTTPError as exc:
            raise ServiceUnavailableError(self.service, str(exc)) from exc
        if response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY:
            detail = response.json()["detail"]
            raise RefusedError(detail["code"], detail["message"])
        if response.is_error:
            raise ServiceUnavailableError(self.service, f"{response.status_code} for {path}")
        return response.json()


@dataclass(frozen=True, slots=True)
class Account:
    id: UUID
    fund_id: UUID
    code: str
    name: str
    cash_kind: str  # broker | bank
    institution: str


@dataclass(frozen=True, slots=True)
class Portfolio:
    id: UUID
    code: str
    name: str
    kind: str  # compound | physical
    is_fund: bool
    fund_id: UUID | None
    account_id: UUID | None


class PortfolioClient(_Client):
    service = "portfolio"

    async def accounts(self, authorization: str) -> dict[UUID, Account]:
        """Счета видимых фондов."""
        payload = cast(list[dict[str, Any]], await self._get("/accounts", authorization))
        return {
            account.id: account
            for account in (
                Account(
                    id=UUID(item["id"]),
                    fund_id=UUID(item["fund_id"]),
                    code=item["code"],
                    name=item["name"],
                    cash_kind=item["cash_kind"],
                    institution=item["institution"],
                )
                for item in payload
            )
        }

    async def portfolios(self, authorization: str) -> dict[UUID, Portfolio]:
        """Видимые портфели: фонды и physical со счетами."""
        payload = cast(list[dict[str, Any]], await self._get("/portfolios", authorization))
        return {
            portfolio.id: portfolio
            for portfolio in (
                Portfolio(
                    id=UUID(item["id"]),
                    code=item["code"],
                    name=item["name"],
                    kind=item["kind"],
                    is_fund=item["is_fund"],
                    fund_id=None if item["fund_id"] is None else UUID(item["fund_id"]),
                    account_id=None if item["account_id"] is None else UUID(item["account_id"]),
                )
                for item in payload
            )
        }


@dataclass(frozen=True, slots=True)
class AccountDays:
    """Дни счёта в системе: портфели счёта, очередь пересчёта, дни."""

    portfolios: tuple[str, ...]
    waiting_since: date | None
    days: tuple[SystemDay, ...]


@dataclass(frozen=True, slots=True)
class Closing:
    """Граница закрытого периода фонда в сервисе сделок."""

    closed_through: date | None
    next_day: date | None
    history: tuple[dict[str, Any], ...]


class BookingClient(_Client):
    service = "booking"

    def __init__(self, http: httpx2.AsyncClient, base_url: str, token: ServiceToken | None) -> None:
        super().__init__(http, base_url)
        self._token = token

    async def closing(self, fund_id: UUID, authorization: str) -> Closing:
        """По какой день фонд закрыт и какой день закрывать следующим."""
        return _closing(await self._get(f"/funds/{fund_id}/closing", authorization))

    async def close(self, fund_id: UUID, day: date, fund_code: str, actor: Actor) -> Closing:
        """Закрыть день под служебной учётной записью сверки; кто закрывает — в теле."""
        if self._token is None:
            raise ServiceUnavailableError("keycloak", "service credentials are not configured")
        try:
            token = await self._token.get()
        except ServiceTokenError as exc:
            raise ServiceUnavailableError("keycloak", str(exc)) from exc
        body = {
            "day": day.isoformat(),
            "fund_code": fund_code,
            "closed_by": {"subject": actor.subject, "username": actor.username},
        }
        found = await self._send("POST", f"/funds/{fund_id}/close", f"Bearer {token}", body=body)
        return _closing(found)

    async def days(
        self, account_id: UUID, first: date, last: date, authorization: str
    ) -> AccountDays:
        """Сделки дня, движения и остатки денег счёта по дням (design/08)."""
        found = await self._get(
            f"/accounts/{account_id}/days",
            authorization,
            **{"from": first.isoformat(), "till": last.isoformat()},
        )
        return AccountDays(
            portfolios=tuple(p["code"] for p in found["portfolios"]),
            waiting_since=_day(found["waiting_since"]),
            days=tuple(_system_day(item) for item in found["days"]),
        )


class RefdataClient(_Client):
    service = "refdata"

    async def codes(self, authorization: str) -> dict[str, tuple[UUID, str]]:
        """Бумаги справочника по коду — SECID и ISIN в верхнем регистре: id и SECID."""
        payload = cast(list[dict[str, Any]], await self._get("/instruments", authorization))
        return {
            code.upper(): (UUID(item["id"]), item["secid"])
            for item in payload
            for code in (item["secid"], item["isin"])
            if code
        }


def service_token(resources: Resources) -> ServiceToken | None:
    """Токен служебной учётной записи сверки; секрета нет — закрывать день нечем."""
    settings = cast(ReconciliationSettings, resources.settings)
    secret = settings.keycloak_client_secret
    if secret is None:
        return None
    return ServiceToken(
        resources.http,
        token_url=realm_token_url(settings.keycloak_url, settings.keycloak_realm),
        client_id=settings.keycloak_client_id,
        client_secret=secret.get_secret_value(),
    )


def _closing(found: dict[str, Any]) -> Closing:
    return Closing(
        closed_through=_day(found["closed_through"]),
        next_day=_day(found["next_day"]),
        history=tuple(found["history"]),
    )


def _system_day(item: dict[str, Any]) -> SystemDay:
    return SystemDay(
        day=date.fromisoformat(item["date"]),
        trading=item["trading"],
        calculated=item["calculated"],
        opening=Decimal(item["opening_cash"]),
        closing=Decimal(item["closing_cash"]),
        trades=tuple(
            SystemTrade(
                trade_id=UUID(t["trade_id"]),
                version=t["version"],
                status=t["status"],
                portfolio_code=t["portfolio_code"],
                external_id=t["external_id"],
                kind=t["kind"],
                side=t["side"],
                instrument_id=_uuid(t["instrument_id"]),
                secid=t["secid"],
                short_name=t["short_name"],
                settle_date=date.fromisoformat(t["settle_date"]),
                quantity=t["quantity"],
                price=Decimal(t["price"]),
                accrued=_decimal(t["accrued"]),
                amount=Decimal(t["amount"]),
                fee=Decimal(t["fee"]),
                repo_rate=_decimal(t["repo_rate"]),
                second_date=_day(t["second_date"]),
                second_amount=_decimal(t["second_amount"]),
            )
            for t in item["trades"]
        ),
        movements=tuple(
            SystemMovement(
                kind=m["kind"],
                cash=Decimal(m["cash"]),
                portfolio_code=m["portfolio_code"],
                trade_id=_uuid(m["trade_id"]),
                external_id=m["external_id"],
                instrument_id=_uuid(m["instrument_id"]),
                secid=m["secid"],
            )
            for m in item["movements"]
        ),
    )


def _uuid(value: str | None) -> UUID | None:
    return None if value is None else UUID(value)


def _decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _day(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)
