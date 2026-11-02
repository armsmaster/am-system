from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx2
import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from am_common.app import Resources
from am_common.clock import MOSCOW
from am_common.db import Database
from am_common.migrations import upgrade
from am_common.testing import TEST_AUDIENCE, TEST_ISSUER, TEST_JWKS_URL, TokenFactory
from reconciliation.main import build_app
from reconciliation.settings import ReconciliationSettings

PORTFOLIO_URL = "http://portfolio.test"
BOOKING_URL = "http://booking.test"
REFDATA_URL = "http://refdata.test"

# Фонд «ОПИФ 1» (design/08, сквозной пример): казначейский портфель на банковском
# счёте, портфель облигаций — на брокерском. Второй фонд трейдеру не виден.
FUND = UUID("00000000-0000-0000-0000-00000000f001")
TREASURY = UUID("00000000-0000-0000-0000-00000000a001")
BONDS = UUID("00000000-0000-0000-0000-00000000b001")
BANK = UUID("00000000-0000-0000-0000-00000000a002")
BROKER = UUID("00000000-0000-0000-0000-00000000b002")
OTHER_FUND = UUID("00000000-0000-0000-0000-00000000f002")
OTHER_BONDS = UUID("00000000-0000-0000-0000-00000000c001")
OTHER_BROKER = UUID("00000000-0000-0000-0000-00000000c002")
OFZ = UUID("00000000-0000-0000-0000-000000026238")
BUY = UUID("00000000-0000-0000-0000-0000000b0001")
SELL = UUID("00000000-0000-0000-0000-0000000b0002")

# Пятница 04.12.2026, утро: сверка недели — по выпискам за прошлые дни.
NOW = datetime(2026, 12, 4, 9, 30, tzinfo=MOSCOW)
FIRST_DAY = date(2026, 11, 26)


def _portfolio(
    portfolio_id: UUID,
    code: str,
    *,
    fund: UUID,
    account: UUID | None = None,
) -> dict[str, Any]:
    return {
        "id": str(portfolio_id),
        "code": code,
        "name": code,
        "kind": "compound" if account is None else "physical",
        "is_fund": account is None,
        "parent_ids": [] if account is None else [str(fund)],
        "fund_id": str(fund),
        "account_id": None if account is None else str(account),
        "treasury_id": str(TREASURY) if portfolio_id == FUND else None,
    }


def _account(account_id: UUID, code: str, kind: str, fund: UUID) -> dict[str, Any]:
    return {
        "id": str(account_id),
        "fund_id": str(fund),
        "code": code,
        "name": f"Счёт {code}",
        "cash_kind": kind,
        "institution": "Брокер Х" if kind == "broker" else "Банк Y",
        "cash_account": "40701810000000000001",
        "depo_account": None,
        "stock_market": kind == "broker",
        "derivatives_market": False,
    }


def trade(trade_id: UUID = BUY, **changes: Any) -> dict[str, Any]:
    """Сделка дня, как её отдаёт сервис сделок: по умолчанию — покупка 27.11 с комиссией."""
    return {
        "trade_id": str(trade_id),
        "version": 2,
        "status": "entered",
        "portfolio_code": "OPIF1-BONDS",
        "external_id": "1000000001",
        "kind": "outright",
        "side": "buy",
        "instrument_id": str(OFZ),
        "instrument_kind": "bond",
        "secid": "SU26238RMFS4",
        "short_name": "ОФЗ 26238",
        "quantity": 50_000,
        "price": "52.500000",
        "accrued": "35.01",
        "amount": "28000500.00",
        "fee": "2800.05",
        "settle_date": "2026-11-30",
        "repo_rate": None,
        "second_date": None,
        "second_amount": None,
        **changes,
    }


SALE = {
    "external_id": "1000000002",
    "side": "sell",
    "quantity": 25_000,
    "price": "52.600000",
    "accrued": "35.21",
    "amount": "14030250.00",
    "fee": "0.00",
    "settle_date": "2026-12-01",
}


def movement(kind: str, cash: str, **changes: Any) -> dict[str, Any]:
    return {
        "portfolio_code": "OPIF1-BONDS",
        "kind": kind,
        "cash": cash,
        "trade_id": None,
        "external_id": None,
        "instrument_id": None,
        "secid": None,
        **changes,
    }


def by_trade(trade_id: UUID, number: str) -> dict[str, Any]:
    return {
        "trade_id": str(trade_id),
        "external_id": number,
        "instrument_id": str(OFZ),
        "secid": "SU26238RMFS4",
    }


def week() -> dict[UUID, dict[date, dict[str, list[dict[str, Any]]]]]:
    """Неделя после исправлений сверки: комиссия по покупке, продажа 25 000."""
    treasury = {"portfolio_code": "OPIF1-TREASURY"}
    return {
        BROKER: {
            date(2026, 11, 26): {"movements": [movement("transfer_in", "60000000.00")]},
            date(2026, 11, 27): {"trades": [trade()]},
            date(2026, 11, 30): {
                "trades": [trade(SELL, **SALE)],
                "movements": [
                    movement("settlement", "-28000500.00", **by_trade(BUY, "1000000001")),
                    movement("fee", "-2800.05", **by_trade(BUY, "1000000001")),
                ],
            },
            date(2026, 12, 1): {
                "movements": [movement("settlement", "14030250.00", **by_trade(SELL, "1000000002"))]
            },
            date(2026, 12, 2): {
                "movements": [
                    movement("coupon", "885000.00", instrument_id=str(OFZ), secid="SU26238RMFS4")
                ]
            },
        },
        BANK: {
            date(2026, 11, 26): {
                "movements": [
                    movement("unit_issue", "0", **treasury),
                    movement("unit_settlement", "100000000.00", **treasury),
                    movement("transfer_out", "-60000000.00", **treasury),
                ]
            },
            date(2026, 12, 3): {"movements": [movement("fee_payment", "-20550.00", **treasury)]},
        },
    }


class World:
    """Всё, куда сервис ходит по сети: Keycloak, портфели, сделки, справочник."""

    def __init__(self, tokens: TokenFactory) -> None:
        self.tokens = tokens
        self.down: set[str] = set()
        self.failing: set[str] = set()  # отвечают ошибкой
        self.portfolios = [
            _portfolio(FUND, "OPIF1", fund=FUND),
            _portfolio(TREASURY, "OPIF1-TREASURY", fund=FUND, account=BANK),
            _portfolio(BONDS, "OPIF1-BONDS", fund=FUND, account=BROKER),
            _portfolio(OTHER_FUND, "OPIF2", fund=OTHER_FUND),
            _portfolio(OTHER_BONDS, "OPIF2-BONDS", fund=OTHER_FUND, account=OTHER_BROKER),
        ]
        self.accounts = [
            _account(BANK, "OPIF1-BANK", "bank", FUND),
            _account(BROKER, "OPIF1-BROKER", "broker", FUND),
            _account(OTHER_BROKER, "OPIF2-BROKER", "broker", OTHER_FUND),
        ]
        # Трейдер второй фонд не видит.
        self.hidden = {"trader": {OTHER_FUND, OTHER_BONDS, OTHER_BROKER}}
        self.instruments = [
            {"id": str(OFZ), "secid": "SU26238RMFS4", "isin": "RU000A1038V6", "kind": "bond"}
        ]
        # Сервис сделок: что в системе по дням; деньги на конец дня — сумма движений.
        self.ledger = week()
        self.reached = date(2026, 12, 4)  # ролл фонда дошёл до этого дня
        self.waiting: dict[UUID, str] = {}
        self.calendar = {date(2026, 11, 4): False}
        self.requests: list[str] = []

    # Сделки для тестов: сервис сделок отдаёт их в дне счёта.
    trade = staticmethod(trade)
    sale = SALE

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        url, path = str(request.url), request.url.path
        self.requests.append(path)
        if path.endswith("/protocol/openid-connect/certs"):
            return httpx2.Response(200, json=self.tokens.jwks())
        for name, base in (
            ("portfolio", PORTFOLIO_URL),
            ("booking", BOOKING_URL),
            ("refdata", REFDATA_URL),
        ):
            if url.startswith(base):
                if name in self.down:
                    raise httpx2.ConnectError(f"{name} is down", request=request)
                if name in self.failing:
                    return httpx2.Response(500, json={"detail": "boom"})
                handler = getattr(self, f"_{name}")
                response: httpx2.Response = handler(request, path)
                return response
        raise AssertionError(f"unexpected request {url}")

    def _username(self, request: httpx2.Request) -> str:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        name: str = jwt.decode(token, options={"verify_signature": False})["preferred_username"]
        return name

    def _portfolio(self, request: httpx2.Request, path: str) -> httpx2.Response:
        hidden = {str(h) for h in self.hidden.get(self._username(request), set())}
        items = self.portfolios if path == "/portfolios" else self.accounts
        assert path in {"/portfolios", "/accounts"}
        return httpx2.Response(200, json=[p for p in items if p["id"] not in hidden])

    def _refdata(self, request: httpx2.Request, path: str) -> httpx2.Response:
        assert path == "/instruments"
        assert request.headers["Authorization"].startswith("Bearer ")
        return httpx2.Response(200, json=self.instruments)

    def _booking(self, request: httpx2.Request, path: str) -> httpx2.Response:
        match = re.fullmatch(r"/accounts/([^/]+)/days", path)
        assert match is not None
        account_id = UUID(match[1])
        first = date.fromisoformat(request.url.params["from"])
        last = date.fromisoformat(request.url.params["till"])
        ledger = self.ledger.get(account_id, {})
        cash = Decimal(0)
        day, days = FIRST_DAY, []
        while day <= last:
            entry = ledger.get(day, {})
            movements = entry.get("movements", [])
            opening = cash
            cash += sum((Decimal(m["cash"]) for m in movements), Decimal(0))
            if day >= first:
                days.append(
                    {
                        "date": day.isoformat(),
                        "trading": self.calendar.get(day, day.weekday() < 5),
                        "calculated": day <= self.reached,
                        "opening_cash": str(opening),
                        "closing_cash": str(cash),
                        "trades": entry.get("trades", []),
                        "movements": [m for m in movements if Decimal(m["cash"])],
                    }
                )
            day += timedelta(days=1)
        codes = sorted(p["code"] for p in self.portfolios if p["account_id"] == str(account_id))
        return httpx2.Response(
            200,
            json={
                "account_id": str(account_id),
                "fund_id": str(FUND),
                "portfolios": [{"id": str(BONDS), "code": c, "name": c} for c in codes],
                "waiting_since": self.waiting.get(account_id),
                "days": days,
            },
        )


@pytest.fixture(scope="session")
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def world(tokens: TokenFactory) -> World:
    return World(tokens)


@pytest.fixture
def bare_settings(postgres_url: str, redis_url: str, schema: str) -> ReconciliationSettings:
    return ReconciliationSettings(
        auth_issuer=TEST_ISSUER,
        auth_jwks_url=TEST_JWKS_URL,
        auth_audience=TEST_AUDIENCE,
        database_url=SecretStr(postgres_url),
        database_schema=schema,
        redis_url=SecretStr(redis_url),
        portfolio_url=PORTFOLIO_URL,
        booking_url=BOOKING_URL,
        refdata_url=REFDATA_URL,
    )


@pytest.fixture
async def settings(bare_settings: ReconciliationSettings) -> ReconciliationSettings:
    await asyncio.to_thread(upgrade, bare_settings, "reconciliation:migrations")
    return bare_settings


@pytest.fixture
def client(settings: ReconciliationSettings, world: World) -> Iterator[TestClient]:
    app = build_app(settings, http_transport=world.transport(), clock=lambda: NOW)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def resources(settings: ReconciliationSettings) -> AsyncIterator[Resources]:
    assert settings.database_url is not None
    assert settings.database_schema is not None
    database = Database(settings.database_url.get_secret_value(), settings.database_schema)
    async with httpx2.AsyncClient() as http:
        yield Resources(settings=settings, http=http, database=database, redis=None)
    await database.dispose()
