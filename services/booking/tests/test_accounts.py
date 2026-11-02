"""День счёта для сверки: сквозной пример методики шага 8 (design/08)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from am_common.app import Resources
from am_common.clock import MOSCOW
from am_common.testing import TokenFactory
from booking.recalc import marketdata_client, recalc_once, refdata_client

pytestmark = pytest.mark.integration

FUND = UUID("00000000-0000-0000-0000-00000000f001")
TREASURY = UUID("00000000-0000-0000-0000-00000000a001")
BONDS = UUID("00000000-0000-0000-0000-00000000b001")
OTHER_BONDS = UUID("00000000-0000-0000-0000-00000000b002")
# Счёт physical-портфеля в conftest — следующий номер после портфеля.
BANK = UUID(int=TREASURY.int + 1)
BROKER = UUID(int=BONDS.int + 1)
OFZ = UUID("00000000-0000-0000-0000-000000026238")
STALE = timedelta(minutes=10)
NOW = datetime(2026, 12, 3, 12, 0, tzinfo=MOSCOW)
WEEK = {"from": "2026-11-26", "till": "2026-12-03"}


class Api:
    def __init__(self, client: TestClient, tokens: TokenFactory, username: str, *roles: str):
        self._client = client
        token = tokens.issue(subject=f"u-{username}", username=username, roles=roles)
        self._headers = {"Authorization": f"Bearer {token}"}

    def call(self, method: str, path: str, body: Any = None, **params: Any) -> Response:
        return self._client.request(method, path, json=body, params=params, headers=self._headers)

    def ok(self, method: str, path: str, body: Any = None, **params: Any) -> Any:
        response = self.call(method, path, body, **params)
        assert response.is_success, response.text
        return response.json()


@pytest.fixture
def trader(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "trader", "trading")


@pytest.fixture
def ops(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "ops", "operations")


async def recalc(resources: Resources) -> None:
    refdata, marketdata = refdata_client(resources), marketdata_client(resources)
    while await recalc_once(
        resources.require_database(), refdata, NOW, STALE, marketdata=marketdata
    ):
        pass


def trade(**changes: Any) -> dict[str, Any]:
    return {
        "portfolio_id": str(BONDS),
        "instrument_id": str(OFZ),
        "side": "buy",
        "quantity": 50_000,
        "price": "52.50",
        "trade_at": "2026-11-27T11:42:00+03:00",
        "settle_date": "2026-11-30",
        "external_id": "1000000001",
        **changes,
    }


@pytest.fixture
async def week(trader: Api, ops: Api, resources: Resources) -> None:
    """Неделя «ОПИФ 1» после исправлений сверки: комиссия по покупке, продажа 25 000,
    вознаграждение УК за ноябрь из банка."""
    issue = ops.ok(
        "POST",
        "/operations",
        {
            "kind": "unit_issue",
            "fund_id": str(FUND),
            "op_date": "2026-11-26",
            "units": "100000",
            "amount": "100000000",
            "planned_date": "2026-11-26",
        },
    )
    ops.ok("POST", f"/operations/{issue['id']}/settle", {"settled_date": "2026-11-26"})
    ops.ok(
        "POST",
        "/operations",
        {
            "kind": "cash_transfer",
            "portfolio_id": str(TREASURY),
            "receiver_id": str(BONDS),
            "op_date": "2026-11-26",
            "amount": "60000000",
        },
    )
    trader.ok("POST", "/trades", trade(fee="2800.05"))
    trader.ok(
        "POST",
        "/trades",
        trade(
            side="sell",
            quantity=25_000,
            price="52.60",
            trade_at="2026-11-30T12:10:00+03:00",
            settle_date="2026-12-01",
            external_id="1000000002",
        ),
    )
    ops.ok(
        "POST",
        f"/funds/{FUND}/fee-rates",
        {"kind": "management", "rate": "1.5", "valid_from": "2026-11-26"},
    )
    ops.ok(
        "POST",
        "/operations",
        {
            "kind": "fee_payment",
            "fund_id": str(FUND),
            "op_date": "2026-12-03",
            "fee_kind": "management",
            "period_end": "2026-11-30",
            "amount": "20550",
        },
    )
    await recalc(resources)


def cash(found: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(d["date"], d["opening_cash"], d["closing_cash"]) for d in found["days"]]


def moves(day: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [(m["kind"], m["cash"], m["external_id"], m["secid"]) for m in day["movements"]]


@pytest.mark.usefixtures("week")
def test_broker_account_week(trader: Api) -> None:
    found = trader.ok("GET", f"/accounts/{BROKER}/days", **WEEK)

    assert (found["fund_id"], [p["code"] for p in found["portfolios"]]) == (
        str(FUND),
        ["OPIF1-BONDS"],
    )
    assert found["waiting_since"] is None
    # Остатки брокерского счёта — таблица методики (design/08, «Остаток денег»).
    assert cash(found) == [
        ("2026-11-26", "0", "60000000.00"),
        ("2026-11-27", "60000000.00", "60000000.00"),
        ("2026-11-28", "60000000.00", "60000000.00"),
        ("2026-11-29", "60000000.00", "60000000.00"),
        ("2026-11-30", "60000000.00", "31996699.95"),
        ("2026-12-01", "31996699.95", "46026949.95"),
        ("2026-12-02", "46026949.95", "46911949.95"),
        ("2026-12-03", "46911949.95", "46911949.95"),
    ]
    days = {d["date"]: d for d in found["days"]}
    assert [d["trading"] for d in found["days"]] == [
        True,
        True,
        False,
        False,
        True,
        True,
        True,
        True,
    ]
    assert all(d["calculated"] for d in found["days"])
    assert moves(days["2026-11-26"]) == [("transfer_in", "60000000.00", None, None)]
    assert moves(days["2026-11-30"]) == [
        ("settlement", "-28000500.00", "1000000001", "SU26238RMFS4"),
        ("fee", "-2800.05", "1000000001", "SU26238RMFS4"),
    ]
    assert moves(days["2026-12-01"]) == [
        ("settlement", "14030250.00", "1000000002", "SU26238RMFS4")
    ]
    assert moves(days["2026-12-02"]) == [("coupon", "885000.00", None, "SU26238RMFS4")]
    # Сделки — в день заключения, со всем, что сравнивают с отчётом брокера.
    [buy] = days["2026-11-27"]["trades"]
    assert {k: buy[k] for k in ("external_id", "side", "quantity", "price", "accrued")} == {
        "external_id": "1000000001",
        "side": "buy",
        "quantity": 50_000,
        "price": "52.500000",
        "accrued": "35.01",
    }
    assert (buy["amount"], buy["fee"], buy["settle_date"], buy["portfolio_code"]) == (
        "28000500.00",
        "2800.05",
        "2026-11-30",
        "OPIF1-BONDS",
    )
    assert (buy["kind"], buy["status"], buy["version"], buy["repo_rate"]) == (
        "outright",
        "entered",
        1,
        None,
    )
    [sell] = days["2026-11-30"]["trades"]
    assert (sell["quantity"], sell["amount"], sell["fee"]) == (25_000, "14030250.00", "0.00")
    assert [
        d["trades"] for d in found["days"] if d["date"] not in {"2026-11-27", "2026-11-30"}
    ] == [[]] * 6


@pytest.mark.usefixtures("week")
def test_bank_account_week(trader: Api) -> None:
    found = trader.ok("GET", f"/accounts/{BANK}/days", **WEEK)

    first, last = found["days"][0], found["days"][-1]
    assert (first["opening_cash"], first["closing_cash"]) == ("0", "40000000.00")
    # Порядок движений одного дня не важен: сверка складывает их по виду.
    assert sorted(moves(first)) == [
        ("transfer_out", "-60000000.00", None, None),
        ("unit_settlement", "100000000.00", None, None),
    ]
    assert (last["closing_cash"], moves(last)) == (
        "39979450.00",
        [("fee_payment", "-20550.00", None, None)],
    )


async def test_days_the_roll_has_not_reached(trader: Api, ops: Api, resources: Resources) -> None:
    # Пока ролла нет, дни есть, но не рассчитаны; деньги до начала истории — ноль.
    found = trader.ok(
        "GET", f"/accounts/{BROKER}/days", **{"from": "2026-11-26", "till": "2026-11-26"}
    )
    assert [(d["calculated"], d["closing_cash"]) for d in found["days"]] == [(False, "0")]

    ops.ok(
        "POST",
        "/operations",
        {
            "kind": "unit_issue",
            "fund_id": str(FUND),
            "op_date": "2026-11-26",
            "units": "100000",
            "amount": "100000000",
            "planned_date": "2026-11-26",
        },
    )
    await recalc(resources)
    trader.ok("POST", "/trades", trade())
    later = trader.ok(
        "GET", f"/accounts/{BROKER}/days", **{"from": "2026-12-03", "till": "2026-12-04"}
    )
    assert later["waiting_since"] == "2026-11-27"
    assert [d["calculated"] for d in later["days"]] == [True, False]


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"from": "2026-12-03", "till": "2026-12-02"}, 422),
        ({"from": "2025-01-01", "till": "2026-12-03"}, 422),
    ],
)
def test_bad_period(trader: Api, params: dict[str, str], status: int) -> None:
    response = trader.call("GET", f"/accounts/{BROKER}/days", **params)
    assert (response.status_code, response.json()["detail"]["code"]) == (status, "bad_period")


def test_account_of_another_fund_is_not_found(trader: Api) -> None:
    # Второй фонд трейдеру не виден — его счёт тоже.
    response = trader.call("GET", f"/accounts/{UUID(int=OTHER_BONDS.int + 1)}/days", **WEEK)
    assert response.status_code == 404


async def test_expense_from_the_broker_account(ops: Api, resources: Resources) -> None:
    """Плату за депозитарий брокер списал с брокерского счёта: расход — в портфель
    облигаций, а не в казначейский (решение 122)."""
    issue = ops.ok(
        "POST",
        "/operations",
        {
            "kind": "unit_issue",
            "fund_id": str(FUND),
            "op_date": "2026-11-26",
            "units": "100000",
            "amount": "100000000",
            "planned_date": "2026-11-26",
        },
    )
    ops.ok("POST", f"/operations/{issue['id']}/settle", {"settled_date": "2026-11-26"})
    expense = {
        "fund_id": str(FUND),
        "portfolio_id": str(BONDS),
        "expense_kind": "exchange_broker",
        "amount": "1500",
    }
    accrued = ops.ok(
        "POST", "/operations", {**expense, "kind": "expense_accrual", "op_date": "2026-11-30"}
    )
    paid = ops.ok(
        "POST", "/operations", {**expense, "kind": "expense_payment", "op_date": "2026-12-01"}
    )
    assert (accrued["portfolio_code"], paid["portfolio_code"]) == ("OPIF1-BONDS", "OPIF1-BONDS")
    await recalc(resources)

    days = ops.ok("GET", f"/accounts/{BROKER}/days", **{"from": "2026-12-01", "till": "2026-12-01"})
    assert moves(days["days"][0]) == [("expense_payment", "-1500.00", None, None)]
    assert days["days"][0]["closing_cash"] == "-1500.00"
    # Без портфеля — по-прежнему казначейский.
    default = ops.ok(
        "POST",
        "/operations",
        {**expense, "portfolio_id": None, "kind": "expense_accrual", "op_date": "2026-11-30"},
    )
    assert default["portfolio_code"] == "OPIF1-TREASURY"


@pytest.mark.parametrize("portfolio", [OTHER_BONDS, FUND])
def test_expense_only_in_a_physical_of_the_fund(ops: Api, portfolio: UUID) -> None:
    response = ops.call(
        "POST",
        "/operations",
        {
            "kind": "expense_accrual",
            "fund_id": str(FUND),
            "portfolio_id": str(portfolio),
            "op_date": "2026-11-30",
            "expense_kind": "specdep",
            "amount": "100",
        },
    )
    assert (response.status_code, response.json()["detail"]["code"]) == (422, "expense_portfolio")
