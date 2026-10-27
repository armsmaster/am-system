"""MOEX ISS: итоги дня и данные дня — на записанных ответах настоящего ISS (27.10.2026)."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import pytest

from am_common.clock import MOSCOW
from marketdata.iss import IssClient, IssError, parse_daily, parse_snapshots, rows
from marketdata.prices import PriceType

FIXTURES = Path(__file__).parent / "fixtures" / "iss"


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


def recorded(request: httpx2.Request) -> httpx2.Response:
    """Отдаёт записанный ответ по пути и номеру страницы; запоминать запросы — в тесте."""
    path = request.url.path
    if "/history/" in path:
        secid = path.rsplit("/", 1)[1].removesuffix(".json")
        name = f"history_{secid}_{request.url.params['start']}"
    else:
        name = f"intraday_{path.split('/')[5]}"
    return httpx2.Response(200, json=load(name))


def client(handler: Any = recorded) -> IssClient:
    return IssClient(
        httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), "http://iss.test/iss/"
    )


async def test_history_of_a_bond_with_iss_dates() -> None:
    days = await client().history(
        "SU26238RMFS4", "bond", "TQOB", date(2026, 10, 20), date(2026, 10, 26)
    )

    assert [d.day for d in days] == [
        date(2026, 10, 20),
        date(2026, 10, 21),
        date(2026, 10, 22),
        date(2026, 10, 23),
        date(2026, 10, 26),
    ]
    last = days[-1]
    assert last.prices == {
        PriceType.CLOSE: Decimal("51.856"),
        PriceType.VWAP: Decimal("51.782"),
        PriceType.MARKET_PRICE: Decimal("51.781"),
    }
    # НКД и номинал — на дату торгов; итог дня — на конец дня по Москве.
    assert (last.accrued, last.face_value) == (Decimal("28.2"), Decimal("1000"))
    assert last.time == datetime(2026, 10, 26, 23, 59, 59, tzinfo=MOSCOW)


async def test_history_comes_in_pages() -> None:
    starts: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        starts.append(request.url.params["start"])
        assert request.url.params["history.columns"].startswith("TRADEDATE,BOARDID")
        return recorded(request)

    days = await client(handler).history(
        "RU000A10F504", "bond", "TQCB", date(2026, 5, 15), date(2026, 10, 26)
    )

    assert starts == ["0", "100"]
    assert len(days) == 116
    assert (days[0].day, days[0].face_value) == (date(2026, 5, 15), Decimal("1000"))


async def test_fund_share_history_has_no_accrued_interest() -> None:
    days = await client().history(
        "LQDT", "fund_share", None, date(2026, 10, 20), date(2026, 10, 26)
    )

    assert days[0].prices[PriceType.VWAP] == Decimal("2.1133")
    assert (days[0].accrued, days[0].face_value) == (None, None)


async def test_other_boards_are_skipped() -> None:
    days = await client().history(
        "SU26238RMFS4", "bond", "PSOB", date(2026, 10, 20), date(2026, 10, 26)
    )

    assert days == []


async def test_snapshots_of_bonds_and_fund_shares() -> None:
    iss = client()

    bonds = await iss.snapshots("bond", [("SU26238RMFS4", "TQOB"), ("RU000A10F504", None)])
    shares = await iss.snapshots("fund_share", [("LQDT", "TQBR")])

    ofz = next(s for s in bonds if s.secid == "SU26238RMFS4")
    assert ofz.time == datetime(2026, 10, 27, 9, 25, 8, tzinfo=MOSCOW)
    assert ofz.prices == {
        PriceType.BID: Decimal("51.75"),
        PriceType.ASK: Decimal("51.765"),
        PriceType.LAST: Decimal("51.765"),
        PriceType.VWAP: Decimal("51.809"),
    }
    # НКД и номинал — на дату расчётов.
    assert (ofz.settle_date, ofz.accrued, ofz.face_value) == (
        date(2026, 10, 28),
        Decimal("28.59"),
        Decimal("1000"),
    )
    linker = next(s for s in bonds if s.secid == "RU000A10F504")
    assert linker.face_value == Decimal("1065.92")
    [fund] = shares
    assert (fund.settle_date, fund.accrued) == (None, None)
    assert fund.prices[PriceType.VWAP] == Decimal("2.1188")


def test_no_prices_outside_trading_and_without_trades() -> None:
    payload = load("intraday_bonds")
    columns = payload["marketdata"]["columns"]
    closed, quiet = (list(row) for row in payload["marketdata"]["data"])
    closed[columns.index("TRADINGSTATUS")] = "N"
    quiet[columns.index("NUMTRADES")] = 0
    payload["marketdata"]["data"] = [closed, quiet]

    found = {
        s.secid: s for s in parse_snapshots(payload, {"RU000A10F504": None, "SU26238RMFS4": None})
    }

    assert (found["RU000A10F504"].time, found["RU000A10F504"].prices) == (None, {})
    # Сделок не было — только заявки.
    assert set(found["SU26238RMFS4"].prices) == {PriceType.BID, PriceType.ASK}


def test_other_securities_in_the_answer_are_ignored() -> None:
    assert parse_snapshots(load("intraday_bonds"), {"SU26238RMFS4": "PSOB"}) == []


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (lambda r: (_ for _ in ()).throw(httpx2.ConnectError("down", request=r)), "failed"),
        (lambda r: httpx2.Response(500), "failed"),
        (lambda r: httpx2.Response(200, content=b"not json"), "failed"),
        (lambda r: httpx2.Response(200, json=[]), "unexpected answer"),
        (lambda r: httpx2.Response(200, json={}), "no block"),
    ],
)
async def test_iss_failures(handler: Any, message: str) -> None:
    with pytest.raises(IssError, match=message):
        await client(handler).history(
            "SU26238RMFS4", "bond", None, date(2026, 10, 26), date(2026, 10, 26)
        )


def test_broken_number() -> None:
    payload = {"history": {"columns": ["TRADEDATE", "WAPRICE"], "data": [["2026-10-26", "abc"]]}}

    with pytest.raises(IssError, match="not a number"):
        parse_daily(rows(payload, "history")[0])
