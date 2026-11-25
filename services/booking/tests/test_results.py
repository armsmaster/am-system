"""Оценка и результат через API: сквозной пример методики шага 6 (design/06)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from am_common.app import Resources
from am_common.clock import MOSCOW
from am_common.testing import TokenFactory
from booking.queries import (
    PeriodResult,
    PositionLine,
    ResultDay,
    _fill,
    _spread_periods,
    period_results,
    period_starts,
)
from booking.recalc import marketdata_client, recalc_once, refdata_client
from booking.repository import Repository, Snapshot

if TYPE_CHECKING:
    from conftest import World

pytestmark = pytest.mark.integration

FUND = UUID("00000000-0000-0000-0000-00000000f001")
TREASURY = UUID("00000000-0000-0000-0000-00000000a001")
BONDS = UUID("00000000-0000-0000-0000-00000000b001")
OFZ = UUID("00000000-0000-0000-0000-000000026238")
STALE = timedelta(minutes=10)
# Те же «сейчас», что в conftest: четверг 03.12.2026, 12:00 по Москве.
NOW = datetime(2026, 12, 3, 12, 0, tzinfo=MOSCOW)
# Цены для оценки ОФЗ 26238 на конец дня — из методики; выходные — цена пятницы.
PRICES = {
    "2026-11-27": "52.55",
    "2026-11-30": "52.62",
    "2026-12-01": "52.58",
    "2026-12-02": "52.70",
    "2026-12-03": "52.66",
}


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
        **changes,
    }


@pytest.fixture
async def week(trader: Api, ops: Api, resources: Resources, world: World) -> None:
    """Неделя фонда «ОПИФ 1»: паи, перевод, покупка, продажа, купон."""
    world.prices |= {(OFZ, day): price for day, price in PRICES.items()}
    world.metrics[OFZ] = {
        "type": "fixed",
        "spread": None,
        "yields": [
            {
                "dv01": "0.326",
                "modified_duration": "5.89",
                "yield_percent": "15.935227",
                "horizon": {"kind": "maturity", "date": "2041-05-15"},
                "continuous": {
                    "yield_percent": "14.786146",
                    "modified_duration": "6.82",
                    "dv01": "0.3779",
                },
            }
        ],
    }
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


def numbers(found: dict[str, Any], *names: str) -> tuple[Decimal, ...]:
    return tuple(Decimal(found[name]) for name in names)


def money(*values: int | str) -> tuple[Decimal, ...]:
    return tuple(Decimal(v) for v in values)


@pytest.mark.usefixtures("week")
def test_result_by_day_and_by_bond(trader: Api) -> None:
    found = trader.ok(
        "GET", "/results", portfolio_id=str(FUND), **{"from": "2026-11-26", "till": "2026-12-03"}
    )

    # Без вознаграждения УК (часть 6.2) — результат дня фонда из таблицы методики.
    assert [Decimal(d["total"]) for d in found["days"]] == [
        0,
        25_000,
        0,
        0,
        41_000,
        -6_300,
        41_700,
        -6_000,
    ]
    assert found["portfolios"] == ["OPIF1-BONDS", "OPIF1-TREASURY"]
    [ofz] = found["lines"]
    assert numbers(ofz, "revaluation", "realized", "coupon", "total") == money(
        48_000,
        20_000,
        27_400,
        95_400,
    )
    # Приток — паи: 100 млн ₽ в первый день; перевод между портфелями фонда взаимно гасится.
    assert numbers(found["days"][0], "flows", "value") == money(100_000_000, 100_000_000)
    assert Decimal(found["days"][-1]["value"]) == Decimal("100095400.00")


@pytest.mark.usefixtures("week")
def test_positions_with_valuation_and_dv01(trader: Api) -> None:
    monday = trader.ok("GET", "/positions", portfolio_id=str(BONDS), date="2026-11-30")

    [ofz] = monday["lines"]
    assert numbers(ofz, "price", "clean", "accrued_value", "unrealized") == money(
        "52.62",
        15_786_000,
        1_056_300,
        36_000,
    )
    assert numbers(ofz, "revaluation", "realized", "coupon", "result") == money(
        11_000,
        20_000,
        10_000,
        41_000,
    )
    # DV01 позиции — 30 000 × 0,326 ₽ на бумагу за 1 бп.
    assert (ofz["dv01"], ofz["duration"], ofz["metrics_basis"]) == ("9780.00", "5.89", "yield")
    # Доходность к горизонту из расчёта сервиса цен — в годовых и непрерывная (решение 164).
    assert (ofz["bond_type"], ofz["yield_percent"], ofz["yield_continuous"], ofz["spread_bp"]) == (
        "fixed",
        "15.935227",
        "14.786146",
        None,
    )
    assert (ofz["horizon_kind"], ofz["horizon_date"], ofz["duration_continuous"]) == (
        "maturity",
        "2041-05-15",
        "6.82",
    )
    assert ofz["dv01_continuous"] == "11337.00"  # 30 000 × 0,3779
    assert (monday["dv01_yield"], monday["dv01_spread"]) == ("9780.00", "9780.00")
    result = monday["result"]
    assert numbers(result, "total", "receivable", "value") == money(
        41_000,
        11_224_200,
        "60066000.00",
    )
    assert monday["portfolios"][0]["result"]["flags"] == []


@pytest.mark.usefixtures("week")
def test_positions_results_by_period(trader: Api, world: World) -> None:
    tuesday = trader.ok("GET", "/positions", portfolio_id=str(FUND), date="2026-12-02")

    [ofz] = tuesday["lines"]
    periods = {name: (p["result"], p["realized"]) for name, p in ofz["periods"].items()}
    # Результат дня по таблице методики: 27.11 +25 000, 30.11 +41 000 (продажа: +20 000
    # реализованных), 01.12 −6 300, 02.12 +41 700. Декабрь — с 01.12.
    assert periods == {
        "week": ("101400.00", "20000.00"),
        "days30": ("101400.00", "20000.00"),
        "month": ("35400.00", "0.00"),
        "quarter": ("101400.00", "20000.00"),
        "year": ("101400.00", "20000.00"),
    }
    assert tuesday["closed"]["year"] == {"result": "0.00", "realized": "0.00"}
    # Соседние торговые дни — для кнопок даты.
    assert (tuesday["previous_day"], tuesday["next_day"]) == ("2026-12-01", "2026-12-03")

    # Справочник недоступен (календарь другого года ещё не загружен) — кнопок нет.
    world.down.add("refdata")
    blind = trader.ok("GET", "/positions", portfolio_id=str(FUND), date="2030-06-03")
    assert (blind["previous_day"], blind["next_day"]) == (None, None)


def test_period_starts() -> None:
    assert period_starts(date(2026, 11, 30)) == {
        "week": date(2026, 11, 24),
        "days30": date(2026, 11, 1),
        "month": date(2026, 11, 1),
        "quarter": date(2026, 10, 1),
        "year": date(2026, 1, 1),
    }
    assert period_starts(date(2026, 2, 14))["quarter"] == date(2026, 1, 1)


async def test_sold_bonds_go_to_the_closed_line() -> None:
    sold, held = UUID(int=1), UUID(int=2)

    class Snapshots:
        async def snapshots_between(self, *_: object) -> list[Snapshot]:
            state = {
                "valuations": [
                    {
                        "instrument_id": str(sold),
                        "revaluation": "10",
                        "realized": "5",
                        "coupon": "1",
                        "fees": "-1",
                    },
                ]
            }
            return [Snapshot(UUID(int=9), date(2026, 11, 27), state, UUID(int=8))]

    totals = await period_results(
        cast(Repository, Snapshots()), [UUID(int=9)], date(2026, 11, 30), []
    )
    line = PositionLine(held, "bond", "HELD", "Held")
    closed = _spread_periods({held: line}, totals)
    assert (closed["week"].result, closed["week"].realized) == (Decimal(15), Decimal(5))
    assert line.periods["week"].result == 0


@pytest.mark.usefixtures("week")
def test_risk_management_reads_but_does_not_trade(client: TestClient, tokens: TokenFactory) -> None:
    # По позициям, СЧА и сделкам дня считаются лимиты (design/09); сделки вводит трейдинг.
    risk = Api(client, tokens, "risk", "risk")

    [ofz] = risk.ok("GET", "/positions", portfolio_id=str(BONDS), date="2026-11-30")["lines"]
    assert (ofz["duration"], ofz["reset_duration"]) == ("5.89", None)
    assert risk.ok("GET", "/nav", fund_id=str(FUND), **{"from": "2026-11-30", "till": "2026-11-30"})
    assert risk.ok("GET", "/trades", portfolio_id=str(FUND), date="2026-11-30")
    assert risk.call("POST", "/trades", trade()).status_code == 403


@pytest.mark.usefixtures("week")
def test_the_limits_service_reads_but_does_not_trade(
    client: TestClient, tokens: TokenFactory
) -> None:
    # Служебная учётная запись сервиса лимитов — для проверки сделки до ввода (решение 147).
    limits = Api(client, tokens, "service-account-am-limits", "fund_reader")

    assert limits.ok("GET", "/positions", portfolio_id=str(BONDS), date="2026-11-30")["lines"]
    assert limits.ok(
        "GET", "/nav", fund_id=str(FUND), **{"from": "2026-11-30", "till": "2026-11-30"}
    )
    assert limits.ok("GET", "/trades", portfolio_id=str(FUND), date="2026-11-30")
    assert limits.call("POST", "/trades", trade()).status_code == 403


@pytest.mark.usefixtures("week")
def test_past_period_and_a_day_without_a_calculation(trader: Api) -> None:
    found = trader.ok(
        "GET", "/results", portfolio_id=str(BONDS), **{"from": "2026-11-26", "till": "2026-11-30"}
    )
    assert [d["date"] for d in found["days"]] == [
        "2026-11-26",
        "2026-11-27",
        "2026-11-28",
        "2026-11-29",
        "2026-11-30",
    ]
    # На субботу 05.12 расчёта ещё нет: позиции — по последнему, без оценки дня.
    saturday = trader.ok("GET", "/positions", portfolio_id=str(BONDS), date="2026-12-05")
    [ofz] = saturday["lines"]
    assert (ofz["trade_quantity"], ofz["price"], saturday["result"]) == (30_000, None, None)


async def test_snapshots_without_valuation(trader: Api, resources: Resources) -> None:
    # Пересчёт без сервиса цен — позиции есть, оценки нет.
    trader.ok("POST", "/trades", trade())
    refdata = refdata_client(resources)
    while await recalc_once(resources.require_database(), refdata, NOW, STALE):
        pass

    found = trader.ok(
        "GET", "/results", portfolio_id=str(BONDS), **{"from": "2026-11-27", "till": "2026-12-03"}
    )
    assert (found["days"], found["lines"]) == ([], [])
    assert nav_of(trader, "2026-11-27", "2026-12-03")["days"] == []


@pytest.mark.usefixtures("week")
def test_today_is_valued_at_the_current_price(trader: Api, world: World) -> None:
    # После пересчёта цена выросла с 52,66% до 52,70%: сегодня — по текущей цене.
    world.prices[(OFZ, "2026-12-03")] = "52.70"

    today = trader.ok("GET", "/positions", portfolio_id=str(BONDS))
    [ofz] = today["lines"]
    assert numbers(ofz, "price", "clean", "revaluation") == money("52.70", 15_810_000, 0)
    found = trader.ok(
        "GET", "/results", portfolio_id=str(FUND), **{"from": "2026-12-03", "till": "2026-12-03"}
    )
    assert Decimal(found["days"][0]["total"]) == 6_000


async def test_no_price_values_at_cost(trader: Api, resources: Resources) -> None:
    trader.ok("POST", "/trades", trade())
    await recalc(resources)

    friday = trader.ok("GET", "/positions", portfolio_id=str(BONDS), date="2026-11-27")
    [ofz] = friday["lines"]
    # Цены не было ни разу — по цене покупки, оценка помечена неполной (решение 103).
    assert (ofz["price"], ofz["clean"], ofz["flags"]) == (None, "26250000.00", ["no_price"])
    assert friday["result"]["flags"] == ["no_price"]
    assert (ofz["dv01"], friday["dv01_spread"]) == (None, "0.00")


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"from": "2026-12-03", "till": "2026-12-01"}, 422),
        ({"from": "2020-01-01", "till": "2026-12-01"}, 422),
    ],
)
def test_bad_period(trader: Api, params: dict[str, str], status: int) -> None:
    response = trader.call("GET", "/results", portfolio_id=str(FUND), **params)
    assert (response.status_code, response.json()["detail"]["code"]) == (status, "bad_period")


def test_unknown_portfolio(trader: Api) -> None:
    response = trader.call(
        "GET",
        "/results",
        portfolio_id=str(UUID(int=9)),
        **{"from": "2026-12-01", "till": "2026-12-03"},
    )
    assert response.status_code == 404


# --- СЧА и комиссии (часть 6.2) -------------------------------------------------------

OTHER_FUND = UUID("00000000-0000-0000-0000-00000000f002")
NAVS = [
    "99995890.41",
    "100016779.96",
    "100012669.68",
    "100008559.57",
    "100045447.94",
    "100035036.74",
    "100072624.00",
    "100062511.67",
]


def management(ops: Api) -> None:
    ops.ok(
        "POST",
        f"/funds/{FUND}/fee-rates",
        {"kind": "management", "rate": "1.5", "valid_from": "2026-11-26", "comment": "ПДУ п. 92"},
    )


def nav_of(api: Api, first: str = "2026-11-26", last: str = "2026-12-03") -> dict[str, Any]:
    found: dict[str, Any] = api.ok(
        "GET", "/nav", fund_id=str(FUND), **{"from": first, "till": last}
    )
    return found


@pytest.mark.usefixtures("week")
async def test_nav_unit_price_and_management_fee(
    trader: Api, ops: Api, resources: Resources
) -> None:
    management(ops)
    await recalc(resources)

    found = nav_of(trader)
    # СЧА и стоимость пая — таблицы методики (design/06) до копейки.
    assert [d["nav"] for d in found["days"]] == NAVS
    assert [d["unit_price"] for d in found["days"]][::7] == ["999.96", "1000.63"]
    assert [d["fees_today"] for d in found["days"]][:2] == [
        {"management": "4109.59"},
        {"management": "4110.45"},
    ]
    monday = found["days"][4]
    assert numbers(monday, "cash", "clean", "accrued", "receivable", "fees_owed", "units") == money(
        "71999500.00", "15786000.00", "1056300.00", "11224200.00", "20552.06", 100_000
    )
    result = trader.ok(
        "GET", "/results", portfolio_id=str(FUND), **{"from": "2026-11-26", "till": "2026-12-03"}
    )
    assert sum((Decimal(d["charges"]) for d in result["days"]), Decimal(0)) == Decimal("-32888.33")
    rates = trader.ok("GET", f"/funds/{FUND}/fee-rates")
    assert [(r["kind"], r["rate"], r["comment"]) for r in rates] == [
        ("management", "1.500000", "ПДУ п. 92")
    ]


@pytest.mark.usefixtures("week")
async def test_fee_payment_for_november(trader: Api, ops: Api, resources: Resources) -> None:
    management(ops)
    paid = ops.ok(
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
    assert (paid["fee_kind"], paid["period_end"], paid["portfolio_code"]) == (
        "management",
        "2026-11-30",
        "OPIF1-TREASURY",
    )
    await recalc(resources)

    last = nav_of(trader)["days"][-1]
    # Корректировка −2,06 ₽ расхода; за декабрь к выплате — 12 336,27 ₽.
    assert (last["fees_owed"], last["nav"]) == ("12336.27", "100062513.73")
    # В разложении результата корректировка — отдельной частью.
    period = {"from": "2026-12-03", "till": "2026-12-03"}
    day = trader.ok("GET", "/results", portfolio_id=str(FUND), **period)["days"][0]
    assert (day["charges"], day["correction"]) == ("-4112.33", "2.06")


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"period_end": "2026-11-29"}, "period_not_month_end"),
        ({"period_end": "2026-12-31"}, "period_after_payment"),
        ({"op_date": "2026-11-28", "period_end": "2026-10-31"}, "not_trading_day"),
        ({"fund_id": str(OTHER_FUND)}, "no_treasury"),
    ],
)
def test_fee_payment_checks(ops: Api, changes: dict[str, str], code: str) -> None:
    body = {
        "kind": "fee_payment",
        "fund_id": str(FUND),
        "op_date": "2026-12-03",
        "fee_kind": "management",
        "period_end": "2026-11-30",
        "amount": "20550",
    } | changes
    response = ops.call("POST", "/operations", body)
    assert (response.status_code, response.json()["detail"]["code"]) == (422, code)


def test_who_sets_rates_and_where(trader: Api, ops: Api) -> None:
    body = {"kind": "management", "rate": "1.5", "valid_from": "2026-11-26"}
    assert trader.call("POST", f"/funds/{FUND}/fee-rates", body).status_code == 403
    no_treasury = ops.call("POST", f"/funds/{OTHER_FUND}/fee-rates", body)
    assert no_treasury.json()["detail"]["code"] == "no_treasury"
    assert ops.call("POST", f"/funds/{FUND}/fee-rates", body | {"rate": "-1"}).status_code == 422
    # Ставки — только у фонда, к которому есть доступ.
    assert trader.call("GET", f"/funds/{BONDS}/fee-rates").status_code == 404
    assert trader.call("GET", f"/funds/{OTHER_FUND}/fee-rates").status_code == 404


def test_nav_only_for_a_fund_and_a_sane_period(trader: Api) -> None:
    not_fund = trader.call(
        "GET", "/nav", fund_id=str(BONDS), **{"from": "2026-11-26", "till": "2026-11-30"}
    )
    assert not_fund.status_code == 404
    reversed_period = trader.call(
        "GET", "/nav", fund_id=str(FUND), **{"from": "2026-11-30", "till": "2026-11-26"}
    )
    assert reversed_period.json()["detail"]["code"] == "bad_period"
    assert nav_of(trader)["days"] == []


@pytest.mark.usefixtures("week")
async def test_today_nav_at_the_current_price(trader: Api, world: World) -> None:
    world.prices[(OFZ, "2026-12-03")] = "52.70"
    last = nav_of(trader, "2026-12-03", "2026-12-03")["days"][-1]
    # Без вознаграждения: СЧА 03.12 — 100 095 400 ₽ и +12 000 ₽ от новой цены.
    assert (last["nav"], last["unit_price"]) == ("100107400.00", "1001.07")


# --- РЕПО (design/07) --------------------------------------------------------------

LQDT = UUID("00000000-0000-0000-0000-0000000001d7")
BONDS_ACCOUNT = UUID(int=BONDS.int + 1)
# Сделка А: обратное РЕПО с КСУ — казначейство размещает 30 млн на выходные.
KSU_REPO = {
    "kind": "repo",
    "portfolio_id": str(TREASURY),
    "side": "buy",
    "trade_at": "2026-11-27T10:00:00+03:00",
    "settle_date": "2026-11-27",
    "amount": "30000000",
    "repo_rate": "14.20",
    "second_date": "2026-11-30",
    "second_amount": "30035013.70",
}
# Сделка Б: прямое РЕПО — портфель облигаций отдаёт 10 000 ОФЗ на три дня.
OFZ_REPO = {
    "kind": "repo",
    "portfolio_id": str(BONDS),
    "instrument_id": str(OFZ),
    "side": "sell",
    "quantity": 10_000,
    "price": "52.60",
    "trade_at": "2026-11-30T11:05:00+03:00",
    "settle_date": "2026-11-30",
    "repo_rate": "14.50",
    "second_date": "2026-12-03",
    "second_amount": "5616786.01",
}
REPO_NAVS = [
    "99995890.41",
    "100016779.96",
    "100024340.43",
    "100031900.60",
    "100080458.77",
    "100067817.55",
    "100103174.88",
    "100090832.72",
]


def positions_of(api: Api, portfolio: UUID, day: str) -> dict[str, Any]:
    found: dict[str, Any] = api.ok("GET", "/positions", portfolio_id=str(portfolio), date=day)
    return found


@pytest.mark.usefixtures("week")
async def test_repo_week_from_the_methodology(trader: Api, ops: Api, resources: Resources) -> None:
    management(ops)
    placed = trader.ok("POST", "/trades", KSU_REPO)
    assert (placed["kind"], placed["instrument_id"], placed["secid"], placed["amount"]) == (
        "repo",
        None,
        "КСУ",
        "30000000.00",
    )
    borrowed = trader.ok("POST", "/trades", OFZ_REPO)
    assert (borrowed["amount"], borrowed["accrued"], borrowed["warnings"]) == (
        "5610100.00",
        "35.01",
        [],
    )
    await recalc(resources)

    found = nav_of(trader)
    assert [d["nav"] for d in found["days"]] == REPO_NAVS
    sunday, tuesday = found["days"][3], found["days"][5]
    assert (sunday["repo_placed"], sunday["repo_borrowed"]) == ("30023342.47", "0")
    assert (tuesday["repo_placed"], tuesday["repo_borrowed"]) == ("0", "5612328.67")
    result = trader.ok(
        "GET", "/results", portfolio_id=str(FUND), **{"from": "2026-11-26", "till": "2026-12-03"}
    )
    assert [Decimal(d["repo"]) for d in result["days"]] == list(
        money("0", "0", "11671.23", "11671.24", "11671.23", "-2228.67", "-2228.67", "-2228.67")
    )

    (repo,) = positions_of(trader, FUND, "2026-12-01")["repos"]
    assert (repo["portfolio_code"], repo["direct"], repo["secid"], repo["quantity"]) == (
        "OPIF1-BONDS",
        True,
        "SU26238RMFS4",
        10_000,
    )
    assert numbers(repo, "rate", "principal", "accrued", "value") == money(
        "14.50", "5610100.00", "2228.67", "-5612328.67"
    )
    tuesday_bonds = positions_of(trader, BONDS, "2026-12-01")
    (line,) = tuesday_bonds["lines"]
    assert (line["trade_quantity"], line["settle_quantity"]) == (30_000, 20_000)
    assert tuesday_bonds["result"]["repo"] == "-2228.67"

    # Лестница счёта с 03.12: вторая часть сделки Б возвращает бумаги и забирает деньги.
    ladder = trader.ok("GET", "/ladder", account_id=str(BONDS_ACCOUNT))
    today = ladder["days"][0]
    assert today["date"] == "2026-12-03"
    second = [m for m in today["movements"] if m["kind"] == "repo_second"]
    assert [(m["trade_id"], m["quantity"], m["cash"]) for m in second] == [
        (borrowed["id"], 10_000, "-5616786.01")
    ]
    # Срок сделки Б: 30.11 → 03.12 — три торговых дня (решение 166).
    assert [m["repo_term"] for m in second] == [3]
    listed = trader.ok("GET", "/trades", portfolio_id=str(BONDS), date="2026-11-30")
    assert [(t["kind"], t["second_date"]) for t in listed] == [
        ("repo", "2026-12-03"),
        ("outright", None),
    ]


@pytest.mark.usefixtures("week")
async def test_money_contribution_changes_the_interest(
    trader: Api, ops: Api, resources: Resources
) -> None:
    borrowed = trader.ok("POST", "/trades", OFZ_REPO)
    paid = ops.ok(
        "POST",
        "/operations",
        {
            "kind": "repo_contribution",
            "trade_id": borrowed["id"],
            "op_date": "2026-12-02",
            "direction": "out",
            "amount": "20000",
        },
    )
    assert (paid["kind"], paid["trade_id"], paid["direction"], paid["portfolio_code"]) == (
        "repo_contribution",
        borrowed["id"],
        "out",
        "OPIF1-BONDS",
    )
    ops.ok(
        "POST",
        f"/trades/{borrowed['id']}/versions",
        OFZ_REPO | {"second_amount": "5596778.06", "comment": "Брокер пересчитал вторую часть"},
    )
    await recalc(resources)
    result = trader.ok(
        "GET", "/results", portfolio_id=str(BONDS), **{"from": "2026-12-01", "till": "2026-12-03"}
    )
    assert [d["repo"] for d in result["days"]] == ["-2228.67", "-2228.67", "-2220.72"]

    # Отмена взноса пересчитывает фонд с первой части: проценты снова делятся поровну.
    ops.ok("POST", f"/operations/{paid['id']}/cancel", {"comment": "Взнос не проходил"})
    await recalc(resources)
    (repo,) = positions_of(trader, BONDS, "2026-12-02")["repos"]
    assert repo["principal"] == "5610100.00"


@pytest.mark.usefixtures("week")
def test_securities_contribution_and_its_checks(trader: Api, ops: Api) -> None:
    borrowed = trader.ok("POST", "/trades", OFZ_REPO)
    more = ops.ok(
        "POST",
        "/operations",
        {
            "kind": "repo_contribution",
            "trade_id": borrowed["id"],
            "op_date": "2026-12-01",
            "direction": "out",
            "quantity": 1_000,
        },
    )
    assert (more["secid"], more["quantity"], more["amount"]) == ("SU26238RMFS4", 1_000, None)

    placed = trader.ok("POST", "/trades", KSU_REPO)
    plain = trader.ok("POST", "/trades", trade(trade_at="2026-11-27T12:00:00+03:00"))
    cancelled = trader.ok("POST", "/trades", OFZ_REPO)
    trader.ok("POST", f"/trades/{cancelled['id']}/cancel", {"comment": "Ошибка ввода"})

    def refused(trade_id: str, **changes: Any) -> str:
        body = {
            "kind": "repo_contribution",
            "trade_id": trade_id,
            "op_date": "2026-12-01",
            "direction": "in",
            "amount": "100",
        } | changes
        response = ops.call("POST", "/operations", body)
        assert response.status_code == 422, response.text
        return str(response.json()["detail"]["code"])

    assert refused(borrowed["id"], op_date="2026-12-03") == "contribution_outside_term"
    assert refused(borrowed["id"], op_date="2026-11-29") == "contribution_outside_term"
    assert refused(placed["id"], op_date="2026-11-27", amount=None, quantity=5) == (
        "ksu_contribution"
    )
    assert refused(plain["id"]) == "repo_not_found"
    unknown = {"kind": "repo_contribution", "trade_id": str(UUID(int=5)), "op_date": "2026-12-01"}
    assert (
        ops.call("POST", "/operations", unknown | {"direction": "in", "amount": "1"}).status_code
        == 404
    )
    assert refused(cancelled["id"]) == "repo_cancelled"
    both = ops.call(
        "POST",
        "/operations",
        {
            "kind": "repo_contribution",
            "trade_id": borrowed["id"],
            "op_date": "2026-12-01",
            "direction": "in",
            "amount": "100",
            "quantity": 5,
        },
    )
    assert both.status_code == 422


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"repo_rate": None}, "repo_terms_required"),
        ({"second_date": "2026-11-30"}, "second_before_first"),
        ({"second_date": "2026-11-29"}, "second_before_first"),
        ({"second_date": "2026-11-28", "settle_date": "2026-11-27"}, "second_not_trading_day"),
        ({"venue": "otc"}, "repo_not_exchange"),
        ({"instrument_id": str(LQDT), "price": "1.5"}, "repo_collateral"),
        (
            {
                "second_date": "2026-11-30",
                "settle_date": "2026-11-27",
                "instrument_id": None,
                "quantity": 0,
                "price": "0",
            },
            "repo_amount_required",
        ),
        (
            {"kind": "outright", "instrument_id": None, "quantity": 0, "price": "0"},
            "instrument_required",
        ),
    ],
)
def test_repo_entry_checks(trader: Api, changes: dict[str, Any], code: str) -> None:
    body = OFZ_REPO | {"trade_at": "2026-11-27T11:05:00+03:00", "settle_date": "2026-11-30"}
    response = trader.call("POST", "/trades", body | changes)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == code


def test_repo_warnings_and_a_security_without_quantity(trader: Api) -> None:
    # Отдают в прямое РЕПО бумаги, которых нет, и ошибаются во второй части на 100 ₽.
    found = trader.ok("POST", "/trades", OFZ_REPO | {"second_amount": "5616886.01"})
    second, position = found["warnings"]
    assert second.startswith(
        "Сумма второй части 5 616 886,01 ₽ отличается от расчётной 5 616 786,01 ₽ "
        "по ставке 14,5% и сроку 3 дн."
    )
    assert "в прямое РЕПО отдаётся 10000" in position
    lacking = trader.call("POST", "/trades", OFZ_REPO | {"quantity": 0})
    assert lacking.status_code == 422


# --- прогноз купона флоатера в лестнице (design/07, решение 14) ---------------------

FLOATER = UUID("00000000-0000-0000-0000-0000000a07b1")


@pytest.mark.usefixtures("week")
async def test_floater_coupon_forecast_in_the_ladder(
    trader: Api, resources: Resources, world: World
) -> None:
    trader.ok(
        "POST",
        "/trades",
        trade(
            instrument_id=str(FLOATER),
            quantity=5_000,
            price="100.1",
            accrued="5.00",
            face_value="1000",
            trade_at="2026-12-01T12:00:00+03:00",
            settle_date="2026-12-02",
        ),
    )
    await recalc(resources)
    ladder = trader.ok("GET", "/ladder", account_id=str(BONDS_ACCOUNT))
    coupon = [
        (d["date"], m["kind"], m["cash"])
        for d in ladder["days"]
        for m in d["movements"]
        if m["instrument_id"] == str(FLOATER) and m["kind"] != "settlement"
    ]
    # 5 000 × 12,58 ₽ — по ключевой ставке 14,00% + 1,30% за 30 дней.
    assert coupon == [("2026-12-14", "coupon_forecast", "62900.00")]
    assert ladder["forecasts"] == [
        {
            "instrument_id": str(FLOATER),
            "date": "2026-12-14",
            "coupon": "12.58",
            "index": "key_rate",
            "index_value": "14.0000",
            "margin_bp": "130",
            "capitalized": False,
        }
    ]

    # Сервис цен недоступен — лестница без прогноза: «сумма не объявлена».
    world.rates = None
    plain = trader.ok("GET", "/ladder", account_id=str(BONDS_ACCOUNT))
    kinds = [
        m["kind"]
        for d in plain["days"]
        for m in d["movements"]
        if m["instrument_id"] == str(FLOATER)
    ]
    assert "payment_unknown" in kinds
    assert plain["forecasts"] == []


# --- отчёт о финансовом результате (решение 167) ----------------------------------------


@pytest.mark.usefixtures("week")
def test_result_summary_by_period(trader: Api) -> None:
    found = trader.ok(
        "GET",
        "/results/summary",
        portfolio_id=str(FUND),
        date="2026-12-03",
        **{"from": "2026-11-30", "till": "2026-12-01"},
    )

    periods = {p["key"]: p for p in found["periods"]}
    assert list(periods) == ["day", "week", "days30", "quarter", "year", "all", "custom"]
    assert (found["date"], found["first_day"]) == ("2026-12-03", periods["all"]["first"])
    # Результат дня фонда из таблицы методики: за неделю 25 000 + 41 000 − 6 300 + 41 700 − 6 000.
    assert {key: p["total"] for key, p in periods.items()} == {
        "day": "-6000.00",
        "week": "95400.00",
        "days30": "95400.00",
        "quarter": "95400.00",
        "year": "95400.00",
        "all": "95400.00",
        "custom": "34700.00",
    }
    week = periods["week"]
    assert numbers(week, "revaluation", "realized", "coupon", "fees") == money(
        48_000, 20_000, 27_400, 0
    )
    assert (week["first"], week["last"], week["days"]) == ("2026-11-27", "2026-12-03", 7)
    # Неделя началась с 100 млн ₽ без притока: доходность — от них.
    assert numbers(week, "start_value", "end_value", "flows", "invested") == money(
        "100000000.00", "100095400.00", 0, "100000000.00"
    )
    assert (week["return_percent"], week["annual_percent"]) == ("0.0954", "4.9744")
    # 30 дней: паи пришли 26.11 и работали 8 дней из 30 — вложено 100 млн × 8 / 30.
    days30 = periods["days30"]
    assert (days30["start_value"], days30["flows"], days30["invested"]) == (
        "0.00",
        "100000000.00",
        "26666666.67",
    )
    assert (days30["return_percent"], days30["annual_percent"]) == ("0.3578", "4.3526")
    day = periods["day"]
    assert (day["start_value"], day["return_percent"], day["annual_percent"]) == (
        "100101400.00",
        "-0.0060",
        "-2.1878",
    )
    custom = periods["custom"]
    assert (custom["first"], custom["last"], custom["start_value"]) == (
        "2026-11-30",
        "2026-12-01",
        "100025000.00",
    )
    [ofz] = found["lines"]
    assert (ofz["secid"], ofz["held"]) == ("SU26238RMFS4", True)
    assert (ofz["totals"]["day"], ofz["totals"]["week"], ofz["totals"]["custom"]) == (
        "-6000.00",
        "95400.00",
        "34700.00",
    )


@pytest.mark.usefixtures("week")
async def test_result_summary_marks_a_sold_bond(trader: Api, resources: Resources) -> None:
    trader.ok(
        "POST",
        "/trades",
        trade(
            side="sell",
            quantity=30_000,
            price="52.70",
            trade_at="2026-12-02T12:00:00+03:00",
            settle_date="2026-12-03",
            accrued="5.00",
        ),
    )
    await recalc(resources)

    found = trader.ok("GET", "/results/summary", portfolio_id=str(BONDS), date="2026-12-03")
    [ofz] = found["lines"]
    assert ofz["held"] is False
    # Без притока и без стоимости до начала — доходности нет.
    empty = trader.ok("GET", "/results/summary", portfolio_id=str(BONDS), date="2026-11-20")
    day = empty["periods"][0]
    assert (day["total"], day["invested"], day["return_percent"]) == ("0.00", "0.00", None)


@pytest.mark.parametrize(
    "params",
    [{"from": "2026-12-01"}, {"from": "2026-12-03", "till": "2026-12-01"}],
)
def test_result_summary_bad_custom_period(trader: Api, params: dict[str, str]) -> None:
    response = trader.call("GET", "/results/summary", portfolio_id=str(FUND), **params)
    assert (response.status_code, response.json()["detail"]["code"]) == (422, "bad_period")


def test_result_summary_unknown_portfolio(trader: Api) -> None:
    response = trader.call("GET", "/results/summary", portfolio_id=str(UUID(int=9)))
    assert response.status_code == 404


def test_return_base_counts_unit_issues_and_redemptions() -> None:
    """База доходности (решение 167): погашение паёв уменьшает её со дня погашения,
    выдача — увеличивает; приток не входит в результат."""

    def day(on: date, value: int, flows: int) -> ResultDay:
        zero = Decimal(0)
        return ResultDay(
            day=on,
            value=Decimal(value),
            flows=Decimal(flows),
            revaluation=Decimal(10_000),
            realized=zero,
            coupon=zero,
            fees=zero,
            expenses=zero,
            charges=zero,
            correction=zero,
            flags=[],
        )

    # 30.11 — 100 млн; каждый день +10 000; 05.12 — погашение на 20 млн, 08.12 — выдача на 5 млн.
    first, last = date(2026, 12, 1), date(2026, 12, 10)
    flows = {date(2026, 12, 5): -20_000_000, date(2026, 12, 8): 5_000_000}
    daily = {
        date(2026, 11, 30): replace(day(date(2026, 11, 30), 100_000_000, 0), revaluation=Decimal(0))
    }
    value = 100_000_000
    for n in range(10):
        on = first + timedelta(days=n)
        value += flows.get(on, 0) + 10_000
        daily[on] = day(on, value, flows.get(on, 0))

    period = PeriodResult("custom", first, last)
    _fill(period, daily, {})

    assert (period.total, period.flows) == (Decimal(100_000), Decimal(-15_000_000))
    assert period.end_value - period.start_value - period.flows == period.total
    # 100 млн − 20 млн × 6/10 + 5 млн × 3/10: приток работает с дня поступления.
    assert period.invested == Decimal(89_500_000)
    assert period.rate == Decimal(100_000) / Decimal(89_500_000)
