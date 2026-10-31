"""Рыночные данные целиком: API → правила → БД → события; ISS и справочник — подменные."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode
from uuid import UUID

import pytest
import redis
from fastapi.testclient import TestClient
from httpx2 import Response

from am_common.clock import MOSCOW
from am_common.migrations import schema_diff
from am_common.testing import TokenFactory
from marketdata.db import metadata
from marketdata.main import build_app
from marketdata.settings import MarketdataSettings

if TYPE_CHECKING:
    from conftest import World

pytestmark = pytest.mark.integration

EXAMPLES = Path(__file__).parents[3] / "docs" / "api" / "price-feed"
# Те же бумаги и «сейчас», что в conftest.World.
OFZ = UUID("00000000-0000-0000-0000-000000026238")
LINKER = UUID("00000000-0000-0000-0000-00000010f504")
FUND = UUID("00000000-0000-0000-0000-0000000001d7")
NOW = datetime(2026, 10, 27, 11, 5, tzinfo=MOSCOW)


class Api:
    def __init__(
        self, client: TestClient, tokens: TokenFactory, username: str, *roles: str
    ) -> None:
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
        return response.status_code, detail["code"] if isinstance(
            detail, dict
        ) else "validation_error"


@pytest.fixture
def feed(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "service-account-am-price-feed", "price_feed")


@pytest.fixture
def trader(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "trader", "trading")


@pytest.fixture
def ops(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "ops", "operations")


def moment(hour: int, minute: int, day: int = 27) -> str:
    return datetime(2026, 10, day, hour, minute, tzinfo=MOSCOW).isoformat()


def quote(code: str, at: str, **prices: float) -> dict[str, Any]:
    return {"code": code, "time": at, **prices}


def bar(code: str, start: str, close: float, timeframe: str = "10m") -> dict[str, Any]:
    return {
        "code": code,
        "timeframe": timeframe,
        "start": start,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
    }


@pytest.fixture
def other_client(settings: MarketdataSettings, world: World) -> Iterator[Callable[..., TestClient]]:
    """Сервис с другими часами или настройками."""
    opened: list[TestClient] = []

    def make(clock: datetime = NOW, **changes: Any) -> TestClient:
        app = build_app(
            settings.model_copy(update=changes),
            http_transport=world.transport(),
            clock=lambda: clock,
        )
        test_client = TestClient(app)
        test_client.__enter__()
        opened.append(test_client)
        return test_client

    yield make
    for test_client in opened:
        test_client.__exit__(None, None, None)


# --- схема БД ------------------------------------------------------------------


async def test_migrations_match_the_model(settings: MarketdataSettings) -> None:
    assert settings.database_url is not None
    assert settings.database_schema is not None

    diff = await schema_diff(
        settings.database_url.get_secret_value(), settings.database_schema, metadata
    )

    assert diff == []


# --- приём от скрипта -----------------------------------------------------------


def test_spec_examples_are_accepted(
    other_client: Callable[..., TestClient], tokens: TokenFactory
) -> None:
    # Поздний вечер: в примере есть итог дня в 23:50.
    feed = Api(
        other_client(datetime(2026, 10, 27, 23, 55, tzinfo=MOSCOW)), tokens, "script", "price_feed"
    )

    for kind in ("quotes", "bars", "rates"):
        example = json.loads((EXAMPLES / f"{kind}.example.json").read_text(encoding="utf-8"))
        result = feed.ok("POST", f"/feed/v1/{kind}", example)
        assert (result["accepted"], result["rejected"]) == (len(example[kind]), [])


def test_bad_items_are_rejected_one_by_one(feed: Api, world: World) -> None:
    result = feed.ok(
        "POST",
        "/feed/v1/quotes",
        {
            "source": "quik",
            "quotes": [
                quote("SU26238RMFS4", moment(11, 0), vwap=51.823, bid=51.784),
                quote("XS0000000000", moment(11, 0), vwap=99.0),
                quote("su26238rmfs4", moment(11, 20), vwap=51.9),
                quote("RU000A1038V6", moment(11, 0), vwap=51.8),
            ],
        },
    )

    assert result["accepted"] == 1
    assert [(r["index"], r["error"]) for r in result["rejected"]] == [
        (1, "unknown_instrument"),
        (2, "future_time"),
        (3, "duplicate_in_batch"),
    ]
    assert "нет в справочнике" in result["rejected"][0]["message"]
    # Неизвестный код — повод перечитать справочник: список бумаг подтянулся сам.
    assert "/instruments" in world.requests

    [batch] = feed.ok("GET", "/feed/batches")
    assert (batch["client"], batch["source"], batch["kind"], batch["accepted"]) == (
        "service-account-am-price-feed",
        "quik",
        "quotes",
        1,
    )
    assert len(batch["rejected"]) == 3


def test_unknown_codes_without_refdata(feed: Api, world: World) -> None:
    world.refdata_down = True

    result = feed.ok(
        "POST",
        "/feed/v1/quotes",
        {"source": "quik", "quotes": [quote("LQDT", moment(11, 0), vwap=2.1)]},
    )

    assert [r["error"] for r in result["rejected"]] == ["unknown_instrument"]


def test_bars_are_checked(feed: Api) -> None:
    result = feed.ok(
        "POST",
        "/feed/v1/bars",
        {
            "source": "quik",
            "bars": [
                bar("RU000A10F504", moment(10, 0), 99.7),
                bar("RU000A10F504", moment(10, 5), 99.7),
                {**bar("RU000A10F504", moment(10, 10), 99.7), "high": 99.0},
                bar("RU000A10F504", moment(10, 0), 99.7, timeframe="300m"),
                bar("RU000A10F504", moment(10, 0), 99.71),
                bar("XS0000000000", moment(10, 0), 99.7),
                # Сейчас 11:05: бар 11:10–11:20 ещё не закончился.
                bar("RU000A10F504", moment(11, 10), 99.7),
            ],
        },
    )

    assert result["accepted"] == 1
    assert [(r["index"], r["error"]) for r in result["rejected"]] == [
        (1, "misaligned_start"),
        (2, "inconsistent_ohlc"),
        (3, "bad_timeframe"),
        (4, "duplicate_in_batch"),
        (5, "unknown_instrument"),
        (6, "future_time"),
    ]


def test_feed_is_only_for_the_script(client: TestClient, trader: Api) -> None:
    body = {"source": "quik", "quotes": [quote("SU26238RMFS4", moment(11, 0), vwap=51.8)]}

    assert trader.problem("POST", "/feed/v1/quotes", body)[0] == 403
    assert client.post("/feed/v1/quotes", json=body).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"source": "QUIK", "quotes": [quote("SU26238RMFS4", moment(11, 0), vwap=51.8)]},
        {"source": "quik", "quotes": [quote("SU26238RMFS4", moment(11, 0))]},
        {"source": "quik", "quotes": [quote("SU26238RMFS4", "2026-10-27T11:00:00", vwap=51.8)]},
        {"source": "quik", "quotes": []},
    ],
)
def test_batch_out_of_schema_is_refused_whole(feed: Api, body: dict[str, Any]) -> None:
    assert feed.problem("POST", "/feed/v1/quotes", body) == (422, "validation_error")


def test_too_large_batch(other_client: Callable[..., TestClient], tokens: TokenFactory) -> None:
    feed = Api(other_client(feed_max_bytes=100), tokens, "script", "price_feed")
    body = {"source": "quik", "quotes": [quote("SU26238RMFS4", moment(11, 0), vwap=51.8)] * 5}

    assert feed.problem("POST", "/feed/v1/quotes", body) == (413, "too_large")


# --- цена для оценки ------------------------------------------------------------


def test_valuation_price_by_rule_stale_and_manual(feed: Api, trader: Api) -> None:
    feed.ok(
        "POST",
        "/feed/v1/quotes",
        {"source": "quik", "quotes": [quote("SU26238RMFS4", moment(11, 0), vwap=51.823, bid=51.7)]},
    )

    now = trader.ok("GET", f"/instruments/{OFZ}/price")
    assert now["price"] == {
        "value": "51.823000",
        "type": "vwap",
        "source": "quik",
        "time": "2026-10-27T08:00:00Z",
        "stale": False,
        "reason": "по правилу: VWAP",
    }
    assert [(c["type"], c["today"]) for c in now["candidates"]] == [("vwap", True), ("bid", True)]
    assert now["trading_day"] == "2026-10-27"

    tomorrow = trader.ok("GET", f"/instruments/{OFZ}/price", at=moment(10, 0, day=28))
    assert tomorrow["price"]["stale"] is True

    manual = trader.ok(
        "POST", f"/instruments/{OFZ}/manual-prices", {"value": 51.5, "comment": "котировка брокера"}
    )
    assert manual["author"] == "trader"
    after = trader.ok("GET", f"/instruments/{OFZ}/price")
    assert (after["price"]["type"], after["price"]["value"]) == ("manual", "51.500000")


def test_no_price_yet(trader: Api, feed: Api) -> None:
    feed.ok(
        "POST",
        "/feed/v1/quotes",
        {"source": "quik", "quotes": [quote("LQDT", moment(11, 0), bid=2.1)]},
    )

    view = trader.ok("GET", f"/instruments/{FUND}/price")

    assert view["price"] is None
    assert view["spread_bp"] is None


def test_dema_rule_for_an_illiquid_bond(feed: Api, trader: Api, ops: Api) -> None:
    closes = [99.70, 99.72, 99.10, 99.71, 99.73, 99.74, 99.74, 99.75, 99.75, 99.76]
    feed.ok(
        "POST",
        "/feed/v1/bars",
        {
            "source": "quik",
            "bars": [
                bar("RU000A10F504", moment(9 + i // 6, (i % 6) * 10), close)
                for i, close in enumerate(closes)
            ],
        },
    )
    rule = {
        "price_types": ["vwap", "dema"],
        "dema_timeframe": 10,
        "dema_half_life": 60,
        "spread_threshold_bp": 50,
        "comment": "Неликвидная бумага: сглаживаем",
    }

    assert ops.problem("PUT", f"/instruments/{LINKER}/rule", rule) == (403, "validation_error")
    saved = trader.ok("PUT", f"/instruments/{LINKER}/rule", rule)
    assert (saved["author"], saved["valid_from"]) == ("trader", NOW.isoformat())
    assert trader.problem("PUT", f"/instruments/{LINKER}/rule", rule) == (422, "no_changes")
    assert trader.problem(
        "PUT", f"/instruments/{LINKER}/rule", {**rule, "dema_timeframe": None}
    ) == (422, "dema_settings_required")

    # Правило действует с момента сохранения (11:05): раньше бумага оценивалась по VWAP.
    assert trader.ok("GET", f"/instruments/{LINKER}/price", at=moment(10, 45))["price"] is None
    view = trader.ok("GET", f"/instruments/{LINKER}/price")
    assert view["price"]["type"] == "dema"
    assert view["price"]["value"] == "99.708262"
    assert view["price"]["reason"] == "за сегодня нет: VWAP — берём DEMA"
    assert view["rule"]["price_types"] == ["vwap", "dema"]
    assert [r["comment"] for r in trader.ok("GET", f"/instruments/{LINKER}/rules")] == [
        "Неликвидная бумага: сглаживаем"
    ]

    period = urlencode({"from": moment(8, 0), "to": moment(11, 10)})
    series = trader.ok("GET", f"/instruments/{LINKER}/series?{period}")
    assert len(series["bars"]["10"]) == 10
    assert series["dema"][-1]["value"] == "99.708262"
    assert len(series["rules"]) == 1


def test_overview_flags_a_wide_spread(feed: Api, trader: Api) -> None:
    feed.ok(
        "POST",
        "/feed/v1/quotes",
        {
            "source": "quik",
            "quotes": [
                quote("SU26238RMFS4", moment(11, 0), vwap=51.80, bid=51.0, ask=52.0),
                quote("RU000A10F504", moment(11, 0), vwap=99.80, bid=99.79, ask=99.82),
            ],
        },
    )

    rows = {row["instrument"]["secid"]: row for row in trader.ok("GET", "/overview")}

    assert (rows["SU26238RMFS4"]["spread_bp"], rows["SU26238RMFS4"]["wide_spread"]) == (
        "193.1",
        True,
    )
    assert (rows["RU000A10F504"]["spread_bp"], rows["RU000A10F504"]["wide_spread"]) == (
        "3.0",
        False,
    )
    assert rows["LQDT"]["price"] is None


def test_series_with_manual_prices_and_quotes(feed: Api, trader: Api) -> None:
    feed.ok(
        "POST",
        "/feed/v1/quotes",
        {"source": "quik", "quotes": [quote("SU26238RMFS4", moment(11, 0), vwap=51.8)]},
    )
    trader.ok(
        "POST", f"/instruments/{OFZ}/manual-prices", {"value": 51.5, "comment": "котировка брокера"}
    )

    series = trader.ok("GET", f"/instruments/{OFZ}/series")

    assert [(q["type"], q["value"]) for q in series["quotes"]] == [("vwap", "51.800000")]
    assert [m["value"] for m in series["manual"]] == ["51.500000"]
    assert (series["bars"], series["dema"], series["rules"]) == ({}, [], [])


def test_unknown_instrument(trader: Api) -> None:
    missing = "00000000-0000-0000-0000-000000000000"

    assert trader.problem("GET", f"/instruments/{missing}/price") == (404, "not_found")
    assert trader.problem("GET", f"/instruments/{missing}/rules") == (404, "not_found")
    assert trader.problem(
        "POST", f"/instruments/{missing}/manual-prices", {"value": 1, "comment": "проверка"}
    ) == (404, "not_found")


# --- журнал ---------------------------------------------------------------------


@pytest.fixture
def stream(settings: MarketdataSettings) -> redis.Redis:
    assert settings.redis_url is not None
    connection = redis.Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
    connection.delete("am.events.marketdata")
    return connection


def test_manual_prices_and_rules_go_to_the_journal(
    stream: redis.Redis, feed: Api, trader: Api
) -> None:
    feed.ok(
        "POST",
        "/feed/v1/quotes",
        {"source": "quik", "quotes": [quote("SU26238RMFS4", moment(11, 0), vwap=51.8)]},
    )
    trader.ok(
        "POST", f"/instruments/{OFZ}/manual-prices", {"value": 51.5, "comment": "котировка брокера"}
    )
    trader.ok(
        "PUT",
        f"/instruments/{OFZ}/rule",
        {
            "price_types": ["vwap", "last"],
            "spread_threshold_bp": 30,
            "comment": "Добавили последнюю сделку",
        },
    )

    events: list[dict[str, Any]] = []
    for _ in range(100):
        events = [json.loads(f["event"]) for _id, f in stream.xrange("am.events.marketdata")]  # type: ignore[union-attr, index]
        if len(events) >= 2:
            break
        time.sleep(0.05)
    stream.close()

    manual, rule = events
    assert manual["type"] == "manual_price.set"
    assert manual["data"] == {
        "instrument": {"secid": "SU26238RMFS4", "isin": "RU000A1038V6"},
        "after": {"value": "51.5", "valid_through": "2026-10-27"},
    }
    assert manual["comment"] == "котировка брокера"
    assert rule["type"] == "price_rule.changed"
    assert rule["data"]["before"] == {
        "price_types": "VWAP",
        "dema_timeframe": None,
        "dema_half_life": None,
        "spread_threshold_bp": 50,
    }
    assert rule["data"]["after"]["price_types"] == "VWAP, последняя сделка"


def test_prices_for_past_days_are_announced(stream: redis.Redis, feed: Api) -> None:
    # Цены за сегодня событий не дают: сегодняшнюю оценку сервис сделок берёт при чтении.
    feed.ok(
        "POST",
        "/feed/v1/quotes",
        {"source": "quik", "quotes": [quote("SU26238RMFS4", moment(11, 0), vwap=51.8)]},
    )
    feed.ok(
        "POST",
        "/feed/v1/quotes",
        {
            "source": "quik",
            "quotes": [
                quote("SU26238RMFS4", moment(18, 0, day=23), close=51.7),
                quote("SU26238RMFS4", moment(18, 0, day=22), close=51.6),
                quote("LQDT", moment(18, 0, day=26), close=1.8),
                quote("SU26238RMFS4", moment(11, 1), vwap=51.9),
            ],
        },
    )
    feed.ok(
        "POST",
        "/feed/v1/bars",
        {"source": "quik", "bars": [bar("RU000A10F504", moment(10, 0, day=26), 99.7)]},
    )

    events: list[dict[str, Any]] = []
    for _ in range(100):
        events = [json.loads(f["event"]) for _id, f in stream.xrange("am.events.marketdata")]  # type: ignore[union-attr, index]
        if len(events) >= 2:
            break
        time.sleep(0.05)
    stream.close()

    quotes, bars = events
    assert (quotes["type"], quotes["entity_id"], quotes["actor"]["username"]) == (
        "prices.backfilled",
        "2026-10-22",
        "service-account-am-price-feed",
    )
    assert quotes["data"]["instruments"] == [
        {"id": str(FUND), "secid": "LQDT", "since": "2026-10-26"},
        {"id": str(OFZ), "secid": "SU26238RMFS4", "since": "2026-10-22"},
    ]
    assert bars["data"]["instruments"] == [
        {"id": str(LINKER), "secid": "RU000A10F504", "since": "2026-10-26"}
    ]


def test_marks_for_valuation_by_day(feed: Api, trader: Api) -> None:
    feed.ok(
        "POST",
        "/feed/v1/quotes",
        {
            "source": "quik",
            "quotes": [
                quote("SU26238RMFS4", moment(18, 0, day=23), vwap=51.7),
                quote("SU26238RMFS4", moment(18, 0, day=26), vwap=51.75),
                quote("SU26238RMFS4", moment(11, 0), vwap=51.8),
                quote("LQDT", moment(11, 0), vwap=1.8),
            ],
        },
    )

    found = trader.ok(
        "GET", f"/instruments/{OFZ}/marks", None, **{"from": "2026-10-23", "till": "2026-10-27"}
    )

    # Выходные — цена пятницы; расчёты T+1 — следующий торговый день.
    assert [
        (m["date"], m["price"]["value"], m["price"]["stale"], m["settle_date"]) for m in found
    ] == [
        ("2026-10-23", "51.700000", False, "2026-10-26"),
        ("2026-10-24", "51.700000", False, "2026-10-26"),
        ("2026-10-25", "51.700000", False, "2026-10-26"),
        ("2026-10-26", "51.750000", False, "2026-10-27"),
        ("2026-10-27", "51.800000", False, "2026-10-28"),
    ]
    # НКД — на дату расчётов: в субботу и воскресенье тот же, что в пятницу.
    assert len({m["accrued"] for m in found[:3]}) == 1
    assert found[0]["accrued"] < found[3]["accrued"] < found[4]["accrued"]
    shares = trader.ok(
        "GET", f"/instruments/{FUND}/marks", None, **{"from": "2026-10-27", "till": "2026-10-27"}
    )
    assert (shares[0]["accrued"], shares[0]["face_value"]) == (None, None)
    assert trader.problem(
        "GET", f"/instruments/{OFZ}/marks", None, **{"from": "2026-10-27", "till": "2026-10-26"}
    ) == (422, "bad_period")


def test_nothing_accepted_and_nothing_to_show(feed: Api, trader: Api) -> None:
    assert trader.ok("GET", "/overview") == []

    result = feed.ok(
        "POST",
        "/feed/v1/bars",
        {"source": "quik", "bars": [bar("XS0000000000", moment(10, 0), 99.7)]},
    )

    assert result["accepted"] == 0
