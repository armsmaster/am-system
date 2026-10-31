"""Сделки и позиции целиком: API → правила → БД → пересчёт; соседние сервисы — подменные.

Сквозной пример — из docs/design/05-booking.md: выдача паёв, перевод 60 млн ₽
в портфель облигаций, покупка, продажа, купон и исправление продажи задним
числом. Деньги совпадают с таблицами методики до копейки.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import func, select

from am_common.app import Resources
from am_common.clock import MOSCOW
from am_common.migrations import schema_diff
from am_common.testing import TokenFactory
from booking.db import day_snapshot, metadata, outbox, recalc_queue
from booking.recalc import marketdata_client, recalc_once, refdata_client
from booking.settings import BookingSettings

if TYPE_CHECKING:
    from conftest import World

pytestmark = pytest.mark.integration

FUND = UUID("00000000-0000-0000-0000-00000000f001")
TREASURY = UUID("00000000-0000-0000-0000-00000000a001")
BONDS = UUID("00000000-0000-0000-0000-00000000b001")
OTHER_BONDS = UUID("00000000-0000-0000-0000-00000000b002")
OFZ = UUID("00000000-0000-0000-0000-000000026238")
LQDT = UUID("00000000-0000-0000-0000-0000000001d7")
LINKER = UUID("00000000-0000-0000-0000-00000010f504")
MATURED = UUID("00000000-0000-0000-0000-000000000666")
FUTURE = UUID("00000000-0000-0000-0000-000000000777")
STALE = timedelta(minutes=10)
# Те же «сейчас», что в conftest: четверг 03.12.2026, 12:00 по Москве.
NOW = datetime(2026, 12, 3, 12, 0, tzinfo=MOSCOW)


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


SALE = {
    "side": "sell",
    "quantity": 20_000,
    "price": "52.60",
    "trade_at": "2026-11-30T12:10:00+03:00",
    "settle_date": "2026-12-01",
}


async def recalc(resources: Resources) -> list[UUID]:
    done = []
    refdata, marketdata = refdata_client(resources), marketdata_client(resources)
    while portfolio := await recalc_once(
        resources.require_database(), refdata, NOW, STALE, marketdata=marketdata
    ):
        done.append(portfolio)
    return done


def positions(api: Api, portfolio: UUID, day: str) -> dict[str, Any]:
    found: dict[str, Any] = api.ok("GET", "/positions", portfolio_id=str(portfolio), date=day)
    return found


def line(view: dict[str, Any]) -> tuple[int, int, str]:
    [found] = view["lines"]
    return found["trade_quantity"], found["settle_quantity"], view["cash"]


# --- схема БД ------------------------------------------------------------------


async def test_migrations_match_the_model(settings: BookingSettings) -> None:
    assert settings.database_url is not None
    assert settings.database_schema is not None
    diff = await schema_diff(
        settings.database_url.get_secret_value(), settings.database_schema, metadata
    )
    assert diff == []


# --- подсказка для формы -------------------------------------------------------


def test_defaults_for_the_form(trader: Api) -> None:
    found = trader.ok("GET", "/trade-defaults", instrument_id=str(OFZ), trade_date="2026-11-27")
    assert (found["settle_date"], found["face_value"], found["accrued"]) == (
        "2026-11-30",
        "1000",
        "35.01",
    )
    # Среда 04.11 — праздник: сделка вторника 03.11 рассчитывается в четверг 05.11.
    holiday = trader.ok("GET", "/trade-defaults", instrument_id=str(LQDT), trade_date="2026-11-03")
    assert (holiday["settle_date"], holiday["face_value"], holiday["accrued"]) == (
        "2026-11-05",
        None,
        None,
    )
    assert trader.problem(
        "GET", "/trade-defaults", instrument_id=str(UUID(int=9)), trade_date="2026-11-27"
    ) == (422, "unknown_instrument")


# --- сквозной пример -------------------------------------------------------------


async def test_example_from_the_methodology(trader: Api, ops: Api, resources: Resources) -> None:
    # 26.11: выдача 100 000 паёв на 100 млн ₽, расчёт в тот же день; перевод 60 млн ₽.
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
    bought = trader.ok("POST", "/trades", trade())
    sold = trader.ok("POST", "/trades", trade(**SALE))

    # НКД подставлен из рыночных данных на дату расчётов, сумму посчитала система.
    assert (bought["accrued"], bought["face_value"], bought["amount"]) == (
        "35.01",
        "1000.000000",
        "28000500.00",
    )
    assert (sold["accrued"], sold["amount"], sold["status"]) == ("35.21", "11224200.00", "entered")
    assert bought["portfolio_code"] == "OPIF1-BONDS"
    assert [v["number"] for v in bought["versions"]] == [1]

    assert await recalc(resources) == [FUND]
    treasury = positions(trader, TREASURY, "2026-11-26")
    assert (treasury["cash"], treasury["units"], treasury["claims"]) == (
        "40000000.00",
        "100000.00000",
        "0.00",
    )
    assert line(positions(trader, BONDS, "2026-11-27")) == (50_000, 0, "60000000.00")
    assert line(positions(trader, BONDS, "2026-11-30")) == (30_000, 50_000, "31999500.00")
    assert line(positions(trader, BONDS, "2026-12-01")) == (30_000, 30_000, "43223700.00")
    on_coupon = positions(trader, BONDS, "2026-12-02")
    assert line(on_coupon) == (30_000, 30_000, "44285700.00")
    [coupon] = on_coupon["portfolios"][0]["movements"]
    assert (coupon["kind"], coupon["cash"]) == ("coupon", "1062000.00")
    [lot] = on_coupon["lines"][0]["lots"]
    assert (lot["quantity"], lot["cost"], lot["accrued_paid"]) == (
        30_000,
        "15750000.00",
        "1050300.00",
    )
    assert on_coupon["lines"][0]["average_price"] == "52.500000"

    # 03.12 сопровождение исправляет продажу: 25 000, а не 20 000.
    corrected = ops.ok(
        "POST",
        f"/trades/{sold['id']}/versions",
        trade(**{**SALE, "quantity": 25_000}, comment="По отчёту брокера 25 000"),
    )
    assert corrected["versions"][-1]["changed"] == ["quantity"]
    assert corrected["amount"] == "14030250.00"
    assert positions(trader, BONDS, "2026-12-02")["portfolios"][0]["waiting_since"] == "2026-11-30"

    assert await recalc(resources) == [FUND]
    assert line(positions(trader, BONDS, "2026-12-02")) == (25_000, 25_000, "46914750.00")
    # Прежние снапшоты 30.11–03.12 обоих портфелей фонда не удалены, а замещены (решение 8).
    async with resources.require_database().session() as session:
        replaced = await session.scalar(
            select(func.count())
            .select_from(day_snapshot)
            .where(day_snapshot.c.replaced_at.is_not(None))
        )
    assert replaced == 8


async def test_compound_sums_its_physical_portfolios(trader: Api, resources: Resources) -> None:
    trader.ok("POST", "/trades", trade())
    trader.ok(
        "POST",
        "/trades",
        trade(portfolio_id=str(TREASURY), quantity=1_000, trade_at="2026-11-27T12:00:00+03:00"),
    )
    await recalc(resources)

    fund = positions(trader, FUND, "2026-11-30")

    assert [p["code"] for p in fund["portfolios"]] == ["OPIF1-BONDS", "OPIF1-TREASURY"]
    assert line(fund) == (51_000, 51_000, "-28560510.00")
    assert [lot["portfolio_code"] for lot in fund["lines"][0]["lots"]] == [
        "OPIF1-BONDS",
        "OPIF1-TREASURY",
    ]
    # Список сделок фонда за день — по обоим портфелям.
    blotter = trader.ok("GET", "/trades", portfolio_id=str(FUND), date="2026-11-27")
    assert [t["portfolio_code"] for t in blotter] == ["OPIF1-BONDS", "OPIF1-TREASURY"]
    # До расчёта — пусто; невидимый портфель — 404.
    assert positions(trader, FUND, "2026-11-26")["portfolios"][0]["as_of"] is None
    assert trader.problem("GET", "/positions", portfolio_id=str(OTHER_BONDS)) == (404, "not_found")


async def test_fund_share_trade(trader: Api, resources: Resources) -> None:
    bought = trader.ok(
        "POST",
        "/trades",
        trade(instrument_id=str(LQDT), quantity=1_000, price="1.234567", settle_date="2026-11-27"),
    )
    assert (bought["amount"], bought["face_value"], bought["accrued"]) == ("1234.57", None, None)
    await recalc(resources)
    view = positions(trader, BONDS, "2026-11-27")
    assert view["lines"][0]["average_price"] == "1.234567"


# --- версии, подтверждение, отмена ------------------------------------------------


async def test_confirmation_and_who_may_change_what(
    trader: Api, ops: Api, resources: Resources
) -> None:
    entered = trader.ok("POST", "/trades", trade())
    path = f"/trades/{entered['id']}"

    assert trader.problem("POST", f"{path}/confirm", {}) == (403, "invalid")
    confirmed = ops.ok("POST", f"{path}/confirm", {"comment": "Сверено с брокером"})
    assert (confirmed["status"], confirmed["number"]) == ("confirmed", 2)
    assert ops.problem("POST", f"{path}/confirm", {}) == (422, "already_confirmed")

    # Подтверждённую сделку трейдинг не правит и не отменяет (решение 95).
    assert trader.problem("POST", f"{path}/versions", trade(price="52.40", comment="Опечатка")) == (
        403,
        "confirmed_trade",
    )
    assert trader.problem("POST", f"{path}/cancel", {"comment": "Ошибка"}) == (
        403,
        "confirmed_trade",
    )
    # Сопровождение правит — подтверждение снимается.
    amended = ops.ok("POST", f"{path}/versions", trade(price="52.40", comment="Цена по отчёту"))
    assert (amended["status"], amended["versions"][-1]["changed"]) == ("entered", ["price"])

    cancelled = trader.ok("POST", f"{path}/cancel", {"comment": "Сделки не было"})
    assert [v["status"] for v in cancelled["versions"]] == [
        "entered",
        "confirmed",
        "entered",
        "cancelled",
    ]
    assert trader.problem("POST", f"{path}/versions", trade(comment="Вернуть")) == (
        422,
        "trade_cancelled",
    )
    assert ops.problem("POST", f"{path}/confirm", {}) == (422, "trade_cancelled")
    assert trader.ok("GET", path)["status"] == "cancelled"

    await recalc(resources)
    assert positions(trader, BONDS, "2026-12-02")["lines"] == []
    async with resources.require_database().session() as session:
        events = [
            row.payload["type"]
            for row in await session.execute(select(outbox.c.payload).order_by(outbox.c.id))
        ]
    assert events == ["trade.entered", "trade.confirmed", "trade.amended", "trade.cancelled"]


async def test_moving_a_trade_to_another_portfolio(trader: Api, resources: Resources) -> None:
    entered = trader.ok("POST", "/trades", trade())
    await recalc(resources)
    trader.ok(
        "POST",
        f"/trades/{entered['id']}/versions",
        trade(portfolio_id=str(TREASURY), comment="Не тот портфель"),
    )

    assert await recalc(resources) == [FUND]
    assert positions(trader, BONDS, "2026-11-30")["lines"] == []
    assert line(positions(trader, TREASURY, "2026-11-30")) == (50_000, 50_000, "-28000500.00")


def test_sale_beyond_the_position_is_a_warning(trader: Api) -> None:
    trader.ok("POST", "/trades", trade())
    sold = trader.ok("POST", "/trades", trade(**{**SALE, "quantity": 60_000}))

    assert sold["warnings"] == [
        "Продажа больше позиции: на 30.11.2026 в портфеле 50000 шт., продаётся 60000. "
        "Дефицит к расчётам покажет лестница."
    ]


# --- проверки при вводе -----------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "status", "code"),
    [
        ({"portfolio_id": str(FUND)}, 422, "portfolio_not_physical"),
        ({"portfolio_id": str(OTHER_BONDS)}, 422, "portfolio_unavailable"),
        ({"trade_at": "2026-12-04T10:00:00+03:00"}, 422, "future_trade"),
        ({"settle_date": "2026-11-26"}, 422, "settle_before_trade"),
        ({"settle_date": "2026-11-28"}, 422, "settle_not_trading_day"),
        ({"instrument_id": str(UUID(int=9))}, 422, "unknown_instrument"),
        ({"instrument_id": str(FUTURE)}, 422, "not_issued"),
        ({"instrument_id": str(MATURED)}, 422, "matured"),
        ({"instrument_id": str(LINKER)}, 422, "face_value_required"),
        ({"settle_date": "2026-12-03"}, 422, "accrued_required"),
        ({"quantity": 0}, 422, "invalid"),
    ],
)
def test_entry_checks(trader: Api, changes: dict[str, Any], status: int, code: str) -> None:
    assert trader.problem("POST", "/trades", trade(**changes)) == (status, code)


def test_values_from_the_broker_win(trader: Api, world: World) -> None:
    # Рыночные данные недоступны — НКД и номинал вводят по подтверждению брокера.
    world.down.add("marketdata")
    assert trader.problem("POST", "/trades", trade()) == (422, "accrued_required")
    entered = trader.ok("POST", "/trades", trade(accrued="35.00", face_value="1000", fee="1400"))
    assert (entered["amount"], entered["fee"]) == ("28000000.00", "1400.00")
    # Номинал без рыночных данных — по графику погашений справочника.
    by_schedule = trader.ok("POST", "/trades", trade(accrued="35.00"))
    assert by_schedule["face_value"] == "1000.000000"


@pytest.mark.parametrize(
    ("service", "code"),
    [("portfolio", "portfolio_unavailable"), ("refdata", "refdata_unavailable")],
)
def test_neighbours_down(trader: Api, world: World, service: str, code: str) -> None:
    world.down.add(service)
    assert trader.problem("POST", "/trades", trade()) == (503, code)


def test_trade_of_a_hidden_portfolio(trader: Api, ops: Api) -> None:
    hidden = ops.ok("POST", "/trades", trade(portfolio_id=str(OTHER_BONDS)))
    path = f"/trades/{hidden['id']}"

    assert trader.problem("GET", path) == (404, "trade_not_found")
    assert trader.problem("POST", f"{path}/versions", trade(comment="Чужая")) == (
        404,
        "trade_not_found",
    )
    assert trader.problem("POST", f"{path}/cancel", {"comment": "Чужая"}) == (
        404,
        "trade_not_found",
    )
    assert trader.problem("GET", f"/trades/{UUID(int=5)}") == (404, "not_found")


def test_price_feed_cannot_enter_trades(client: TestClient, tokens: TokenFactory) -> None:
    feed = Api(client, tokens, "script", "price_feed")
    assert feed.problem("POST", "/trades", trade()) == (403, "invalid")


async def test_queue_keeps_the_earliest_date(trader: Api, resources: Resources) -> None:
    trader.ok("POST", "/trades", trade(**SALE))
    trader.ok("POST", "/trades", trade())
    async with resources.require_database().session() as session:
        [row] = (await session.execute(select(recalc_queue))).all()
    assert str(row.since) == "2026-11-27"


def test_face_value_by_the_repayment_schedule(trader: Api, world: World) -> None:
    # Рыночных данных о номинале нет — номинал по графику: 1000 − 250 − 25% × 1000 = 500;
    # строка без суммы и процента не учитывается.
    amortizing = UUID(int=301)
    world.add_instrument(
        amortizing,
        "SU46020RMFS2",
        world.ofz_terms()
        | {
            "amortizations": [
                {"date": "2026-08-01", "amount": "250", "percent": None, "is_maturity": False},
                {"date": "2026-09-01", "amount": None, "percent": "25", "is_maturity": False},
                {"date": "2026-10-01", "amount": None, "percent": None, "is_maturity": False},
                {"date": "2041-05-15", "amount": "500", "percent": None, "is_maturity": True},
            ]
        },
    )
    no_face = UUID(int=302)
    world.add_instrument(no_face, "RU000NOFACE", world.ofz_terms() | {"initial_face_value": None})

    def face(instrument: UUID) -> Any:
        return trader.ok(
            "GET", "/trade-defaults", instrument_id=str(instrument), trade_date="2026-11-27"
        )["face_value"]

    assert face(amortizing) == "500"
    assert face(no_face) is None
    assert face(LINKER) is None  # номинал линкера — только из рыночных данных
    assert face(FUTURE) is None  # на дату расчётов бумаги ещё нет
