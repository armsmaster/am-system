"""Операции фонда, переводы и лестница через API: правила → БД → пересчёт → позиции."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select

from am_common.app import Resources
from am_common.clock import MOSCOW
from am_common.testing import TokenFactory
from booking.db import outbox
from booking.main import build_app
from booking.recalc import recalc_once, refdata_client
from booking.settings import BookingSettings

if TYPE_CHECKING:
    from conftest import World

pytestmark = pytest.mark.integration

FUND = UUID("00000000-0000-0000-0000-00000000f001")
TREASURY = UUID("00000000-0000-0000-0000-00000000a001")
BONDS = UUID("00000000-0000-0000-0000-00000000b001")
OTHER_FUND = UUID("00000000-0000-0000-0000-00000000f002")
OTHER_BONDS = UUID("00000000-0000-0000-0000-00000000b002")
BONDS_ACCOUNT = UUID(int=BONDS.int + 1)  # так счета раздаёт conftest
OFZ = UUID("00000000-0000-0000-0000-000000026238")
MATURED = UUID("00000000-0000-0000-0000-000000000666")
FUTURE = UUID("00000000-0000-0000-0000-000000000777")
STALE = timedelta(minutes=10)
NOW = datetime(2026, 12, 3, 12, 0, tzinfo=MOSCOW)
MONDAY = datetime(2026, 11, 30, 18, 0, tzinfo=MOSCOW)


class Api:
    def __init__(self, client: TestClient, tokens: TokenFactory, username: str, *roles: str):
        self._client = client
        token = tokens.issue(subject=f"u-{username}", username=username, roles=roles)
        self._headers = {"Authorization": f"Bearer {token}"}

    def call(
        self, method: str, path: str, body: dict[str, Any] | None = None, **params: Any
    ) -> Response:
        return self._client.request(method, path, json=body, params=params, headers=self._headers)

    def ok(self, method: str, path: str, body: dict[str, Any] | None = None, **params: Any) -> Any:
        response = self.call(method, path, body, **params)
        assert response.is_success, response.text
        return response.json()

    def problem(
        self, method: str, path: str, body: dict[str, Any] | None = None, **params: Any
    ) -> tuple[int, str]:
        response = self.call(method, path, body, **params)
        assert response.is_error, response.text
        detail = response.json()["detail"]
        return response.status_code, detail["code"] if isinstance(detail, dict) else "invalid"


@pytest.fixture
def trader(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "trader", "trading")


@pytest.fixture
def ops(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "ops", "operations")


@pytest.fixture
def monday(settings: BookingSettings, world: World, tokens: TokenFactory) -> Iterator[Api]:
    """Тот же сервис, но «сейчас» — понедельник 30.11, 18:00: лестница из методики."""
    app = build_app(settings, http_transport=world.transport(), clock=lambda: MONDAY)
    with TestClient(app) as test_client:
        yield Api(test_client, tokens, "trader", "trading")


def units(**changes: Any) -> dict[str, Any]:
    """Выдача 100 000 паёв на 100 млн ₽ в четверг 26.11, расчёт в пятницу."""
    return {
        "kind": "unit_issue",
        "fund_id": str(FUND),
        "op_date": "2026-11-26",
        "units": "100000",
        "amount": "100000000",
        "planned_date": "2026-11-27",
        **changes,
    }


def transfer(**changes: Any) -> dict[str, Any]:
    """Перевод 60 млн ₽ из казначейского портфеля в портфель облигаций 26.11."""
    return {
        "kind": "cash_transfer",
        "portfolio_id": str(TREASURY),
        "receiver_id": str(BONDS),
        "op_date": "2026-11-26",
        "amount": "60000000",
        **changes,
    }


def bonds_transfer(**changes: Any) -> dict[str, Any]:
    return {
        "kind": "security_transfer",
        "portfolio_id": str(BONDS),
        "receiver_id": str(TREASURY),
        "op_date": "2026-12-02",
        "instrument_id": str(OFZ),
        "quantity": 10_000,
        **changes,
    }


def trade(**changes: Any) -> dict[str, Any]:
    """Покупка 50 000 ОФЗ 26238 по 52,50% в пятницу 27.11, расчёты 30.11."""
    return {
        "portfolio_id": str(BONDS),
        "instrument_id": str(OFZ),
        "side": "buy",
        "quantity": 50_000,
        "price": "52.50",
        "trade_at": "2026-11-27T11:42:00+03:00",
        "settle_date": "2026-11-30",
        **changes,
    }


async def recalc(resources: Resources) -> list[UUID]:
    done = []
    refdata = refdata_client(resources)
    while fund := await recalc_once(resources.require_database(), refdata, NOW, STALE):
        done.append(fund)
    return done


def positions(api: Api, portfolio: UUID, day: str) -> dict[str, Any]:
    found: dict[str, Any] = api.ok("GET", "/positions", portfolio_id=str(portfolio), date=day)
    return found


async def events(resources: Resources) -> list[dict[str, Any]]:
    async with resources.require_database().session() as session:
        rows = await session.execute(select(outbox.c.payload).order_by(outbox.c.id))
    return [row.payload for row in rows]


# --- паи --------------------------------------------------------------------------


async def test_units_settle_by_the_mark(trader: Api, ops: Api, resources: Resources) -> None:
    assert trader.problem("POST", "/operations", units()) == (403, "invalid")
    issued = ops.ok("POST", "/operations", units())
    assert (issued["portfolio_code"], issued["status"], issued["settled_date"]) == (
        "OPIF1-TREASURY",
        "active",
        None,
    )
    path = f"/operations/{issued['id']}"

    assert await recalc(resources) == [FUND]
    # До отметки расчёта: паи выданы, требование к пайщикам, денег нет.
    before = positions(trader, TREASURY, "2026-11-27")
    assert (before["units"], before["claims"], before["cash"]) == (
        "100000.00000",
        "100000000.00",
        "0",
    )

    assert ops.problem("POST", f"{path}/settle", {"settled_date": "2026-11-28"}) == (
        422,
        "not_trading_day",
    )
    assert ops.problem("POST", f"{path}/settle", {"settled_date": "2026-11-25"}) == (
        422,
        "date_before_operation",
    )
    settled = ops.ok(
        "POST", f"{path}/settle", {"settled_date": "2026-11-27", "comment": "Выписка банка"}
    )
    assert (settled["number"], settled["versions"][-1]["changed"]) == (2, ["settled_date"])
    assert ops.problem("POST", f"{path}/settle", {"settled_date": "2026-11-27"}) == (
        422,
        "already_settled",
    )
    await recalc(resources)
    after = positions(trader, TREASURY, "2026-11-27")
    assert (after["claims"], after["cash"]) == ("0.00", "100000000.00")
    assert [m["kind"] for m in after["portfolios"][0]["movements"]] == ["unit_settlement"]

    cancelled = ops.ok("POST", f"{path}/cancel", {"comment": "Заявка отозвана"})
    assert (cancelled["status"], cancelled["versions"][-1]["changed"]) == ("cancelled", ["status"])
    assert ops.problem("POST", f"{path}/cancel", {"comment": "Ещё раз"}) == (
        422,
        "operation_cancelled",
    )
    await recalc(resources)
    assert positions(trader, TREASURY, "2026-11-27")["portfolios"][0]["as_of"] is None

    journal = await events(resources)
    assert [e["type"] for e in journal] == [
        "operation.entered",
        "operation.settled",
        "operation.cancelled",
    ]
    entered = journal[0]["data"]
    assert entered["operation"] == {"kind": "unit_issue", "portfolio": "OPIF1-TREASURY"}
    assert (entered["after"]["units"], entered["after"]["receiver"]) == ("100000", None)


async def test_redemption_and_expenses(trader: Api, ops: Api, resources: Resources) -> None:
    ops.ok("POST", "/operations", units(kind="unit_redemption", units="10", amount="10000"))
    accrual = {
        "kind": "expense_accrual",
        "fund_id": str(FUND),
        "op_date": "2026-11-28",  # начисление — в любой день, денег не двигает
        "expense_kind": "specdep",
        "amount": "50000",
    }
    accrued = ops.ok("POST", "/operations", accrual)
    payment = accrual | {"kind": "expense_payment", "amount": "30000"}
    assert ops.problem("POST", "/operations", payment) == (422, "not_trading_day")
    ops.ok("POST", "/operations", payment | {"op_date": "2026-11-30"})
    assert ops.problem(
        "POST", f"/operations/{accrued['id']}/settle", {"settled_date": "2026-11-30"}
    ) == (422, "not_unit_operation")

    listed = trader.ok("GET", "/operations", portfolio_id=str(FUND))
    assert [o["kind"] for o in listed] == ["expense_payment", "expense_accrual", "unit_redemption"]
    on_day = trader.ok("GET", "/operations", portfolio_id=str(FUND), date="2026-11-28")
    assert [o["expense_kind"] for o in on_day] == ["specdep"]

    await recalc(resources)
    fund = positions(trader, FUND, "2026-11-30")
    assert (fund["units"], fund["obligations"], fund["expenses"], fund["cash"]) == (
        "-10.00000",
        "10000.00",
        "20000.00",
        "-30000.00",
    )


# --- переводы ---------------------------------------------------------------------


async def test_security_transfer_moves_lots(trader: Api, ops: Api, resources: Resources) -> None:
    bought = trader.ok("POST", "/trades", trade())
    moved = ops.ok("POST", "/operations", bonds_transfer())
    assert (moved["secid"], moved["receiver_code"], moved["warnings"]) == (
        "SU26238RMFS4",
        "OPIF1-TREASURY",
        [],
    )
    too_many = ops.ok("POST", "/operations", bonds_transfer(quantity=45_000))
    assert too_many["warnings"] == [
        "Переводится больше, чем на счёте депо: на 02.12.2026 в портфеле OPIF1-BONDS "
        "40000 шт., переводится 45000. Дефицит покажет лестница."
    ]
    ops.ok("POST", f"/operations/{too_many['id']}/cancel", {"comment": "Ошибка ввода"})
    # Трейдинг видит операцию, но не меняет её.
    assert trader.ok("GET", f"/operations/{moved['id']}")["quantity"] == 10_000
    # Позже — ещё одна сделка: название бумаги — из последней записи о ней.
    trader.ok(
        "POST",
        "/trades",
        trade(
            quantity=1,
            trade_at="2026-12-02T15:00:00+03:00",
            settle_date="2026-12-03",
            accrued="0.20",
        ),
    )

    assert await recalc(resources) == [FUND]
    treasury = positions(trader, TREASURY, "2026-12-02")
    [line] = treasury["lines"]
    [lot] = line["lots"]
    assert (line["secid"], lot["portfolio_code"], lot["trade_id"], lot["quantity"]) == (
        "SU26238RMFS4",
        "OPIF1-TREASURY",
        bought["id"],
        10_000,
    )
    assert lot["cost"] == "5250000.00"
    assert positions(trader, BONDS, "2026-12-02")["lines"][0]["settle_quantity"] == 40_000
    # Список портфеля — и отправленные, и полученные переводы.
    assert len(trader.ok("GET", "/operations", portfolio_id=str(TREASURY))) == 2


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (transfer(portfolio_id=str(FUND)), "portfolio_not_physical"),
        (transfer(receiver_id=str(TREASURY)), "same_portfolio"),
        (transfer(receiver_id=str(OTHER_BONDS)), "other_fund"),
        (transfer(receiver_id=str(UUID(int=9))), "portfolio_unavailable"),
        (transfer(op_date="2026-11-28"), "not_trading_day"),
        (transfer(op_date="2026-12-04"), "future_operation"),
        (bonds_transfer(instrument_id=str(UUID(int=9))), "unknown_instrument"),
        (bonds_transfer(instrument_id=str(FUTURE)), "not_issued"),
        (bonds_transfer(instrument_id=str(MATURED)), "matured"),
        (units(fund_id=str(BONDS)), "fund_unavailable"),
        (units(fund_id=str(OTHER_FUND)), "no_treasury"),
        (units(planned_date="2026-11-25"), "date_before_operation"),
        ({k: v for k, v in units().items() if k != "planned_date"}, "invalid"),
        (units(units="1.123456"), "invalid"),
    ],
)
def test_entry_checks(ops: Api, body: dict[str, Any], code: str) -> None:
    assert ops.problem("POST", "/operations", body) == (422, code)


def test_operation_of_a_hidden_portfolio(
    client: TestClient, tokens: TokenFactory, ops: Api, trader: Api, world: World
) -> None:
    other = next(p for p in world.portfolios if p["id"] == str(OTHER_FUND))
    other["treasury_id"] = str(OTHER_BONDS)
    hidden = ops.ok(
        "POST",
        "/operations",
        units(fund_id=str(OTHER_FUND), kind="expense_accrual", expense_kind="other"),
    )
    path = f"/operations/{hidden['id']}"
    world.hidden["ops2"] = {OTHER_FUND, OTHER_BONDS}
    stranger = Api(client, tokens, "ops2", "operations")

    assert trader.problem("GET", path) == (404, "operation_not_found")
    assert stranger.problem("POST", f"{path}/cancel", {"comment": "Чужая"}) == (
        404,
        "operation_not_found",
    )
    assert trader.problem("GET", f"/operations/{UUID(int=5)}") == (404, "not_found")
    assert trader.ok("GET", "/operations", portfolio_id=str(OTHER_FUND)) == []


# --- лестница ---------------------------------------------------------------------


async def test_ladder_from_the_methodology(
    trader: Api, ops: Api, monday: Api, resources: Resources
) -> None:
    ops.ok("POST", "/operations", units(planned_date="2026-11-26"))
    ops.ok("POST", "/operations", transfer())
    trader.ok("POST", "/trades", trade())
    trader.ok(
        "POST",
        "/trades",
        trade(
            side="sell",
            quantity=20_000,
            price="52.60",
            trade_at="2026-11-30T12:10:00+03:00",
            settle_date="2026-12-01",
        ),
    )
    await recalc(resources)

    ladder = monday.ok("GET", "/ladder", account_id=str(BONDS_ACCOUNT))

    assert ([p["code"] for p in ladder["portfolios"]], ladder["as_of"]) == (
        ["OPIF1-BONDS"],
        "2026-11-29",
    )
    assert ladder["instruments"] == [
        {"instrument_id": str(OFZ), "secid": "SU26238RMFS4", "short_name": "ОФЗ 26238"}
    ]
    rows = [
        (d["date"], d["cash"], d["securities"][0]["quantity"], [m["kind"] for m in d["movements"]])
        for d in ladder["days"][:3]
    ]
    assert rows == [
        ("2026-11-30", "31999500.00", 50_000, ["settlement"]),
        ("2026-12-01", "43223700.00", 30_000, ["settlement"]),
        ("2026-12-02", "44285700.00", 30_000, ["coupon"]),
    ]
    first = ladder["days"][0]["movements"][0]
    assert (first["portfolio_code"], first["quantity"], first["cash"]) == (
        "OPIF1-BONDS",
        50_000,
        "-28000500.00",
    )
    assert monday.problem("GET", "/ladder", account_id=str(UUID(int=9))) == (404, "not_found")
