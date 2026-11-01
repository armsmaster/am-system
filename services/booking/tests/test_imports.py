"""Импорт сделок по спецификации docs/api/trade-import: пакет → правила → версии → журнал."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from jsonschema import Draft202012Validator
from sqlalchemy import select

from am_common.app import Resources
from am_common.testing import TokenFactory
from booking.db import outbox
from booking.repository import Repository

if TYPE_CHECKING:
    from conftest import World

pytestmark = pytest.mark.integration

SPEC = Path(__file__).parents[3] / "docs" / "api" / "trade-import"
PATH = "/feed/v1/trades"
OTHER_BONDS = UUID("00000000-0000-0000-0000-00000000b002")


def load(name: str) -> Any:
    return json.loads((SPEC / name).read_text(encoding="utf-8"))


class Api:
    def __init__(self, client: TestClient, tokens: TokenFactory, username: str, *roles: str):
        self._client = client
        token = tokens.issue(subject=f"u-{username}", username=username, roles=roles)
        self._headers = {"Authorization": f"Bearer {token}"}

    def call(self, method: str, path: str, body: Any = None) -> Response:
        return self._client.request(method, path, json=body, headers=self._headers)

    def ok(self, method: str, path: str, body: Any = None) -> Any:
        response = self.call(method, path, body)
        assert response.is_success, response.text
        return response.json()


@pytest.fixture
def trader(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "trader", "trading")


@pytest.fixture
def ops(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "ops", "operations")


def batch(*trades: dict[str, Any], source: str = "broker-x") -> dict[str, Any]:
    return {"source": source, "trades": list(trades)}


def bought(**changes: Any) -> dict[str, Any]:
    """Покупка 50 000 ОФЗ 26238 из примера спецификации."""
    found: dict[str, Any] = copy.deepcopy(load("trades.example.json")["trades"][0])
    return found | changes


def counts(report: dict[str, Any]) -> tuple[int, int, int, int, int]:
    return (
        report["created"],
        report["amended"],
        report["cancelled"],
        report["unchanged"],
        len(report["rejected"]),
    )


async def journal(resources: Resources) -> list[str]:
    async with resources.require_database().session() as session:
        rows = await session.execute(select(outbox.c.payload).order_by(outbox.c.id))
    return [row.payload["type"] for row in rows]


# --- спецификация ------------------------------------------------------------------


def test_example_matches_the_schema() -> None:
    schema = load("trades.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    assert list(validator.iter_errors(load("trades.example.json"))) == []
    broken = [
        batch(bought(), source="Broker X"),
        batch(bought(trade_at="2026-11-27T11:42:00")),
        batch(bought(side="short")),
        batch(bought(quantity=0)),
        batch(bought(price="52.5")),
        batch(bought(extra=1)),
        batch(),
    ]
    assert all(list(validator.iter_errors(b)) for b in broken)


def test_repo_example_matches_the_schema() -> None:
    schema = load("trades.schema.json")
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    example = load("repo.example.json")
    assert list(validator.iter_errors(example)) == []
    ksu, bonds = example["trades"]
    broken = [
        batch(bonds | {"second_amount": None} | {"kind": "repo"}),
        batch({k: v for k, v in bonds.items() if k != "repo_rate"}),
        batch({k: v for k, v in ksu.items() if k != "amount"}),
        batch({k: v for k, v in bonds.items() if k != "quantity"}),
    ]
    assert all(list(validator.iter_errors(b)) for b in broken)


@pytest.mark.parametrize(
    "body",
    [
        batch(bought(), source="Broker X"),
        batch(bought(trade_at="2026-11-27T11:42:00")),
        batch(bought(price=1.1234567)),
        batch(bought(extra=1)),
        batch(),
    ],
)
def test_broken_batch_is_rejected_whole(trader: Api, body: dict[str, Any]) -> None:
    assert trader.call("POST", PATH, body).status_code == 422


# --- повтор, исправление, отмена ---------------------------------------------------


async def test_example_from_the_spec(trader: Api, resources: Resources) -> None:
    example = load("trades.example.json")

    first = trader.ok("POST", PATH, example)
    assert counts(first) == (3, 0, 0, 0, 0)
    assert [t["result"] for t in first["trades"]] == ["created"] * 3
    buy = trader.ok("GET", f"/trades/{first['trades'][0]['trade_id']}")
    # Сумму посчитала система; НКД — из пакета, номинал — из рыночных данных.
    assert (buy["amount"], buy["external_id"], buy["portfolio_code"]) == (
        "28000500.00",
        "BX-2026-11-27-0042",
        "OPIF1-BONDS",
    )
    assert buy["versions"][0]["comment"] == "Загружена из broker-x."
    sale = trader.ok("GET", f"/trades/{first['trades'][1]['trade_id']}")
    assert (sale["fee"], sale["accrued"]) == ("1400.50", "35.21")
    shares = trader.ok("GET", f"/trades/{first['trades'][2]['trade_id']}")
    assert shares["comment"] == "Свободные деньги — в фонд ликвидности"

    # Повтор пакета — дублей нет.
    assert counts(trader.ok("POST", PATH, example)) == (0, 0, 0, 3, 0)

    # Источник исправил продажу: новая версия, комментарий — о причине.
    corrected = copy.deepcopy(example)
    corrected["trades"][1]["quantity"] = 25_000
    amended = trader.ok("POST", PATH, corrected)
    assert counts(amended) == (0, 1, 0, 2, 0)
    sale = trader.ok("GET", f"/trades/{amended['trades'][1]['trade_id']}")
    assert (sale["quantity"], sale["versions"][-1]["changed"]) == (25_000, ["quantity"])
    assert sale["versions"][-1]["comment"] == "Исправлена в источнике broker-x."

    # Отмена в источнике — версия «отменена»; повтор отмены ничего не меняет.
    corrected["trades"][1]["cancelled"] = True
    assert counts(trader.ok("POST", PATH, corrected)) == (0, 0, 1, 2, 0)
    again = trader.ok("POST", PATH, corrected)
    assert counts(again) == (0, 0, 0, 3, 0)
    sale = trader.ok("GET", f"/trades/{again['trades'][1]['trade_id']}")
    assert (sale["status"], sale["versions"][-1]["comment"]) == (
        "cancelled",
        "Отменена в источнике broker-x.",
    )
    # Отменённая сделка не возвращается.
    corrected["trades"][1]["cancelled"] = False
    returned = trader.ok("POST", PATH, corrected)
    assert [(r["index"], r["error"]) for r in returned["rejected"]] == [(1, "trade_cancelled")]

    assert await journal(resources) == [
        "trade.entered",
        "trade.entered",
        "trade.entered",
        "trade.amended",
        "trade.cancelled",
    ]


def test_each_bad_trade_is_rejected_alone(trader: Api) -> None:
    report = trader.ok(
        "POST",
        PATH,
        batch(
            bought(external_id="A", portfolio="OPIF9"),
            bought(external_id="B", code="XS0000000000"),
            bought(external_id="C"),
            bought(external_id="C", quantity=1),
            bought(external_id="D", cancelled=True),
            bought(external_id="E", trade_at="2026-12-04T10:00:00+03:00"),
            bought(external_id="F", portfolio="opif1", code="ru000a1038v6"),
            bought(external_id="G", portfolio="opif1-bonds", code="ru000a1038v6"),
            bought(external_id="H", side="sell", quantity=60_000),
        ),
    )

    assert [(r["external_id"], r["error"]) for r in report["rejected"]] == [
        ("A", "portfolio_unavailable"),
        ("B", "unknown_instrument"),
        ("C", "duplicate_in_batch"),
        ("C", "duplicate_in_batch"),
        ("D", "unknown_trade"),
        ("E", "future_trade"),
        ("F", "portfolio_not_physical"),
    ]
    assert report["rejected"][1]["message"] == (
        "Бумаги XS0000000000 нет в справочнике. Загрузите её в разделе «Справочник»."
    )
    # Код портфеля и ISIN — без учёта регистра; продажа больше позиции — предупреждение.
    [g, h] = report["trades"]
    assert (g["external_id"], g["result"], h["external_id"]) == ("G", "created", "H")
    assert h["warnings"] == [
        "Продажа больше позиции: на 27.11.2026 в портфеле 50000 шт., продаётся 60000. "
        "Дефицит к расчётам покажет лестница."
    ]


def test_confirmed_trade_is_changed_by_operations_only(trader: Api, ops: Api) -> None:
    [created] = trader.ok("POST", PATH, batch(bought()))["trades"]
    ops.ok("POST", f"/trades/{created['trade_id']}/confirm", {})
    changed = batch(bought(price=52.4))

    [refused] = trader.ok("POST", PATH, changed)["rejected"]
    assert refused["error"] == "confirmed_trade"
    # Подтверждённую сделку с теми же полями повтор не трогает.
    assert counts(trader.ok("POST", PATH, batch(bought()))) == (0, 0, 0, 1, 0)
    assert counts(ops.ok("POST", PATH, changed)) == (0, 1, 0, 0, 0)


def test_key_of_a_hidden_trade(trader: Api, ops: Api) -> None:
    ops.ok("POST", PATH, batch(bought(portfolio="OPIF2-BONDS")))

    [refused] = trader.ok("POST", PATH, batch(bought()))["rejected"]
    assert (refused["error"], refused["message"]) == (
        "portfolio_unavailable",
        "Сделка с этим номером — в портфеле, к которому нет доступа.",
    )
    # Тот же номер другого источника — другая сделка.
    assert counts(trader.ok("POST", PATH, batch(bought(), source="oms"))) == (1, 0, 0, 0, 0)


def test_trade_feed_script(client: TestClient, tokens: TokenFactory) -> None:
    script = Api(client, tokens, "service-account-am-trade-feed", "trade_feed")

    assert counts(script.ok("POST", PATH, batch(bought()))) == (1, 0, 0, 0, 0)
    # Скрипт присылает сделки, но не смотрит позиции и не вводит сделки руками.
    assert script.call("GET", f"/positions?portfolio_id={OTHER_BONDS}").status_code == 403
    assert script.call("POST", "/trades", {}).status_code == 403
    price_feed = Api(client, tokens, "service-account-am-price-feed", "price_feed")
    assert price_feed.call("POST", PATH, batch(bought())).status_code == 403


def test_two_batches_with_the_same_new_trade(trader: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    trader.ok("POST", PATH, batch(bought()))

    # Второй пакет не увидел сделку первого: так бывает, когда пакеты идут одновременно.
    async def not_found(_self: Repository, _source: str, _key: str) -> None:
        return None

    monkeypatch.setattr(Repository, "trade_by_key", not_found)
    [refused] = trader.ok("POST", PATH, batch(bought()))["rejected"]
    assert refused["error"] == "concurrent_import"


def test_refdata_down_fails_the_batch(trader: Api, world: World) -> None:
    # Повтор пакета безопасен, поэтому при отказе соседа отвечаем 503 на весь пакет.
    world.down.add("refdata")
    response = trader.call("POST", PATH, batch(bought()))
    assert (response.status_code, response.json()["detail"]["code"]) == (503, "refdata_unavailable")


# --- РЕПО (design/07) ----------------------------------------------------------------


async def test_repo_example_from_the_spec(trader: Api, resources: Resources) -> None:
    example = load("repo.example.json")
    first = trader.ok("POST", PATH, example)
    assert counts(first) == (2, 0, 0, 0, 0)
    ksu = trader.ok("GET", f"/trades/{first['trades'][0]['trade_id']}")
    assert (ksu["kind"], ksu["secid"], ksu["amount"], ksu["second_amount"]) == (
        "repo",
        "КСУ",
        "30000000.00",
        "30035013.70",
    )
    # Повтор — без изменений; досрочное исполнение — новая версия с новой второй частью.
    assert counts(trader.ok("POST", PATH, example)) == (0, 0, 0, 2, 0)
    early = copy.deepcopy(example)
    early["trades"][1] |= {"second_date": "2026-12-02", "second_amount": 5614557.34}
    assert counts(trader.ok("POST", PATH, early)) == (0, 1, 0, 1, 0)
    bonds = trader.ok("GET", f"/trades/{first['trades'][1]['trade_id']}")
    assert bonds["versions"][-1]["changed"] == ["second_date", "second_amount"]
    assert "trade.amended" in await journal(resources)


def test_repo_import_checks(trader: Api) -> None:
    ksu, bonds = load("repo.example.json")["trades"]
    report = trader.ok(
        "POST",
        PATH,
        batch(
            ksu | {"external_id": "R-1", "kind": "outright"},
            {k: v for k, v in bonds.items() if k not in {"quantity", "price"}}
            | {"external_id": "R-2"},
        ),
    )
    assert [r["error"] for r in report["rejected"]] == ["instrument_required", "quantity_required"]
