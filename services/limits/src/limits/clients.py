"""Соседние сервисы: портфели, сделки, справочник.

Лимиты ходят к ним с токеном того, кто спрашивает: сервис портфелей отдаёт ровно те
фонды, что тот видит, сервис сделок проверяет доступ сам.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import httpx2

from am_common.service_token import ServiceToken, ServiceTokenError
from limits.engine import Attributes, Classifier, Deal, Figures, Issuer, Paper, Position, Rated


class ServiceUnavailableError(Exception):
    """Соседний сервис недоступен или ответил ошибкой."""

    def __init__(self, service: str, detail: str) -> None:
        super().__init__(f"{service}: {detail}")
        self.service = service


class ServiceRefusedError(Exception):
    """Соседний сервис отказал в доступе: данных этого фонда тому, кто спрашивает, не видно."""

    def __init__(self, service: str) -> None:
        super().__init__(service)
        self.service = service


class _Client:
    service = ""

    def __init__(self, http: httpx2.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    async def _get(self, path: str, authorization: str, **params: str) -> Any:
        try:
            response = await self._http.get(
                f"{self._base}{path}", params=params, headers={"Authorization": authorization}
            )
        except httpx2.HTTPError as exc:
            raise ServiceUnavailableError(self.service, str(exc)) from exc
        # Отказ в доступе — не поломка соседа: так и говорим.
        if response.status_code in {403, 404}:
            raise ServiceRefusedError(self.service)
        if response.is_error:
            raise ServiceUnavailableError(self.service, f"{response.status_code} for {path}")
        return response.json()


@dataclass(frozen=True, slots=True)
class Fund:
    id: UUID
    code: str
    name: str


class PortfolioClient(_Client):
    service = "portfolio"

    async def funds(self, authorization: str) -> dict[UUID, Fund]:
        """Фонды, которые видит тот, кто спрашивает."""
        payload = cast(list[dict[str, Any]], await self._get("/portfolios", authorization))
        return {
            UUID(item["id"]): Fund(UUID(item["id"]), item["code"], item["name"])
            for item in payload
            if item["is_fund"]
        }

    async def access(self, fund_id: UUID, authorization: str) -> str | None:
        """Что пользователь видит: fund — сам фонд, portfolio — только портфель фонда,
        None — ничего (решение 147)."""
        payload = cast(list[dict[str, Any]], await self._get("/portfolios", authorization))
        if any(item["is_fund"] and item["id"] == str(fund_id) for item in payload):
            return "fund"
        return "portfolio" if any(item["fund_id"] == str(fund_id) for item in payload) else None


class BookingClient(_Client):
    service = "booking"

    async def figures(self, fund_id: UUID, day: date, authorization: str) -> Figures | None:
        """Состав СЧА фонда за день; расчёта за день нет — None."""
        found = await self._get(
            "/nav",
            authorization,
            fund_id=str(fund_id),
            **{"from": day.isoformat(), "till": day.isoformat()},
        )
        if not found["days"]:
            return None
        [item] = found["days"]
        return Figures(
            cash=Decimal(item["cash"]),
            receivable=Decimal(item["receivable"]),
            payable=Decimal(item["payable"]),
            claims=Decimal(item["claims"]),
            obligations=Decimal(item["obligations"]),
            expenses=Decimal(item["expenses"]),
            fees_owed=Decimal(item["fees_owed"]),
            repo_placed=Decimal(item["repo_placed"]),
            repo_borrowed=Decimal(item["repo_borrowed"]),
            nav=Decimal(item["nav"]),
        )

    async def positions(self, fund_id: UUID, day: date, authorization: str) -> list[Position]:
        """Позиции фонда на конец дня: стоимость — чистая, НКД и купоны к получению."""
        found = await self._get(
            "/positions", authorization, portfolio_id=str(fund_id), date=day.isoformat()
        )
        return [
            Position(
                instrument_id=UUID(line["instrument_id"]),
                secid=line["secid"],
                kind=line["kind"],
                quantity=line["trade_quantity"],
                value=Decimal(line["clean"])
                + Decimal(line["accrued_value"])
                + Decimal(line["coupon_due"]),
                # У флоатера — дюрация до ближайшего купона, у фикса — по доходности.
                duration=_decimal(line.get("reset_duration") or line.get("duration")),
            )
            for line in found["lines"]
        ]

    async def deals(self, fund_id: UUID, day: date, authorization: str) -> tuple[list[Deal], bool]:
        """Покупки и продажи бумаг за день и были ли сделки вообще, с РЕПО.

        Отменённые сделки не в счёт. Плечо и дюрацию меняют и РЕПО, доли — только бумаги.
        """
        found = await self._get(
            "/trades", authorization, portfolio_id=str(fund_id), date=day.isoformat()
        )
        live = [t for t in found if t["status"] != "cancelled"]
        deals = [
            Deal(UUID(t["instrument_id"]), t["secid"], t["side"], Decimal(t["amount"]))
            for t in live
            if t["kind"] == "outright" and t["instrument_id"]
        ]
        return deals, bool(live)


class RefdataClient(_Client):
    service = "refdata"

    async def attributes(self, day: date, authorization: str) -> Attributes:
        """Признаки, классификаторы и рейтинги бумаг и эмитентов на дату; общая шкала."""
        found = await self._get("/attributes", authorization, date=day.isoformat())
        scale = await self._get("/ratings/scale", authorization)
        return Attributes(
            papers={
                UUID(i["id"]): Paper(
                    id=UUID(i["id"]),
                    secid=i["secid"],
                    kind=i["kind"],
                    iss_type=i["iss_type"],
                    issuer_id=None if i["issuer_id"] is None else UUID(i["issuer_id"]),
                    coupon_kind=i["coupon_kind"],
                    currency=i["currency"],
                    classes=_classes(i["classes"]),
                    ratings=_rated(i["ratings"]),
                )
                for i in found["instruments"]
            },
            issuers={
                UUID(i["id"]): Issuer(
                    UUID(i["id"]), i["name"], _classes(i["classes"]), _rated(i["ratings"])
                )
                for i in found["issuers"]
            },
            classifiers={
                UUID(c["id"]): Classifier(
                    UUID(c["id"]),
                    c["name"],
                    c["target"],
                    {UUID(v["id"]): v["name"] for v in c["values"]},
                )
                for c in found["classifiers"]
            },
            grades=tuple(scale["grades"]),
        )

    async def add_trading_days(self, day: date, days: int, authorization: str) -> date:
        """Дата через N торговых дней календаря справочника — для сроков нарушений."""
        found = await self._get(
            "/calendar/add-trading-days", authorization, date=day.isoformat(), days=str(days)
        )
        return date.fromisoformat(found["date"])

    async def agencies(self, authorization: str) -> set[str]:
        """Коды агентств, рейтинги которых ведёт справочник."""
        scale = await self._get("/ratings/scale", authorization)
        return {a["code"] for a in scale["agencies"]}


async def service_authorization(token: ServiceToken | None) -> str:
    """Заголовок Authorization служебной учётной записи am-limits."""
    if token is None:
        raise ServiceUnavailableError("keycloak", "service credentials are not configured")
    try:
        return f"Bearer {await token.get()}"
    except ServiceTokenError as exc:
        raise ServiceUnavailableError("keycloak", str(exc)) from exc


def _classes(found: dict[str, str]) -> dict[UUID, UUID]:
    return {UUID(k): UUID(v) for k, v in found.items()}


def _rated(found: dict[str, Any]) -> Rated:
    return Rated(found["status"], {r["agency"]: r["grade"] for r in found["ratings"]})


def _decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)
