"""Сверка через API: сквозной пример методики шага 8 (design/08)."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select

from am_common.app import Resources
from am_common.migrations import schema_diff
from am_common.testing import TokenFactory
from reconciliation.db import metadata, outbox
from reconciliation.settings import ReconciliationSettings

if TYPE_CHECKING:
    from conftest import World

pytestmark = pytest.mark.integration

# Те же номера, что в conftest: фонд «ОПИФ 1», его счета, ОФЗ 26238, сделки недели.
FUND = UUID("00000000-0000-0000-0000-00000000f001")
BANK = UUID("00000000-0000-0000-0000-00000000a002")
BROKER = UUID("00000000-0000-0000-0000-00000000b002")
OTHER_FUND = UUID("00000000-0000-0000-0000-00000000f002")
OTHER_BROKER = UUID("00000000-0000-0000-0000-00000000c002")
OFZ = UUID("00000000-0000-0000-0000-000000026238")
BUY = UUID("00000000-0000-0000-0000-0000000b0001")
SELL = UUID("00000000-0000-0000-0000-0000000b0002")


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


@pytest.fixture
def feed(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "service-account-am-statement-feed", "statement_feed")


def broker(
    day: str, opening: str, closing: str, *movements: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    return {
        "account": "OPIF1-BROKER",
        "date": day,
        "opening_cash": opening,
        "closing_cash": closing,
        "movements": list(movements),
        **extra,
    }


def statement_trade(**changes: Any) -> dict[str, Any]:
    """Покупка 27.11 по отчёту брокера."""
    return {
        "external_id": "1000000001",
        "side": "buy",
        "security": "SU26238RMFS4",
        "settle_date": "2026-11-30",
        "quantity": 50_000,
        "price": "52.50",
        "accrued": "35.01",
        "amount": "28000500.00",
        "fee": "2800.05",
        **changes,
    }


SOLD = statement_trade(
    external_id="1000000002",
    side="sell",
    settle_date="2026-12-01",
    quantity=25_000,
    price="52.60",
    accrued="35.21",
    amount="14030250.00",
    fee="0",
)


def broker_week() -> list[dict[str, Any]]:
    """Отчёты брокера за неделю, как их переводит в наш формат скрипт (design/08)."""
    return [
        broker("2026-11-26", "0", "60000000", {"kind": "transfer", "amount": "60000000"}),
        broker("2026-11-27", "60000000", "60000000", trades=[statement_trade()]),
        broker(
            "2026-11-30",
            "60000000.00",
            "31996699.95",
            {"kind": "settlement", "trade": "1000000001", "amount": "-28000500.00"},
            {"kind": "fee", "trade": "1000000001", "amount": "-2800.05"},
            trades=[SOLD],
        ),
        broker(
            "2026-12-01",
            "31996699.95",
            "46026949.95",
            {"kind": "settlement", "trade": "1000000002", "amount": "14030250.00"},
        ),
        broker("2026-12-02", "46026949.95", "46026949.95"),
        broker(
            "2026-12-03",
            "46026949.95",
            "46911949.95",
            {"kind": "coupon", "security": "SU26238RMFS4", "amount": "885000.00"},
        ),
    ]


def bank(day: str, opening: str, closing: str, *movements: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": str(BANK),
        "date": day,
        "opening_cash": opening,
        "closing_cash": closing,
        "movements": list(movements),
    }


def bank_week(ops: Api) -> None:
    """Выписки банка сопровождение вводит вручную; дни без движений — «движений нет»."""
    ops.ok(
        "POST",
        "/statements",
        bank(
            "2026-11-26",
            "0",
            "40000000",
            {"kind": "unit_settlement", "amount": "100000000", "comment": "Пайщики"},
            {"kind": "transfer", "amount": "-60000000"},
        ),
    )
    for day in ("2026-11-27", "2026-11-30", "2026-12-01", "2026-12-02"):
        ops.ok("POST", "/statements", bank(day, "40000000", "40000000"))
    ops.ok(
        "POST",
        "/statements",
        bank("2026-12-03", "40000000", "39979450", {"kind": "fee_payment", "amount": "-20550"}),
    )


def fund_day(api: Api, day: str) -> dict[str, Any]:
    found: dict[str, Any] = api.ok("GET", f"/funds/{FUND}/days/{day}")
    return found


def account(found: dict[str, Any], code: str) -> dict[str, Any]:
    mine: list[dict[str, Any]] = [a for a in found["accounts"] if a["code"] == code]
    assert len(mine) == 1
    return mine[0]


async def events(resources: Resources) -> list[dict[str, Any]]:
    async with resources.require_database().session() as session:
        rows = await session.execute(select(outbox.c.payload).order_by(outbox.c.id))
    return [row.payload for row in rows]


async def journal_of_the_week(resources: Resources, line: str) -> None:
    """В журнал — каждая выписка и принятие."""
    found_events = await events(resources)
    assert [e["type"] for e in found_events].count("statement.received") == 12
    [acceptance] = [e for e in found_events if e["type"] == "discrepancy.accepted"]
    assert (acceptance["data"]["after"], acceptance["comment"]) == (
        {
            "line": line,
            "status": "not_in_statement",
            "system": "885000",
            "statement": None,
        },
        "Купон ОФЗ 26238 брокер зачислит 03.12",
    )
    first = next(e for e in found_events if e["type"] == "statement.received")
    assert (first["data"]["account"], first["data"]["before"], first["data"]["after"]) == (
        {"code": "OPIF1-BROKER"},
        None,
        {"opening_cash": "0", "closing_cash": "60000000", "trades": 0, "movements": 1},
    )


# --- схема БД ------------------------------------------------------------------


async def test_migrations_match_the_model(settings: ReconciliationSettings) -> None:
    assert settings.database_url is not None
    assert settings.database_schema is not None
    diff = await schema_diff(
        settings.database_url.get_secret_value(), settings.database_schema, metadata
    )
    assert diff == []


# --- неделя методики -----------------------------------------------------------


async def test_week_from_the_methodology(
    feed: Api, ops: Api, trader: Api, world: World, resources: Resources
) -> None:
    received = feed.ok(
        "POST", "/feed/v1/statements", {"source": "broker-x", "statements": broker_week()}
    )
    assert (received["created"], received["replaced"], received["rejected"]) == (6, 0, [])
    bank_week(ops)

    # 27.11: комиссию по покупке в системе не ввели.
    world.ledger[BROKER][date(2026, 11, 27)]["trades"] = [world.trade(fee="0.00")]
    found = fund_day(trader, "2026-11-27")
    assert (found["code"], found["trading"], found["reconciled"]) == ("OPIF1", True, False)
    assert [a["code"] for a in found["accounts"]] == ["OPIF1-BANK", "OPIF1-BROKER"]
    friday = account(found, "OPIF1-BROKER")
    assert (friday["status"], friday["portfolios"]) == ("discrepancies", ["OPIF1-BONDS"])
    [line] = friday["trades"]
    assert (line["line"], line["status"], line["differences"]) == (
        "trade:1000000001",
        "mismatched",
        [{"field": "fee", "system": "0", "statement": "2800.05"}],
    )
    assert line["system"]["trade_id"] == str(BUY)
    assert account(found, "OPIF1-BANK")["status"] == "reconciled"
    # Сопровождение ввело комиссию — сервис сделок пересчитал фонд.
    world.ledger[BROKER][date(2026, 11, 27)]["trades"] = [world.trade()]
    assert fund_day(trader, "2026-11-27")["reconciled"] is True

    # Выходные: сверять нечего.
    weekend = fund_day(trader, "2026-11-28")
    assert (weekend["trading"], weekend["reconciled"]) == (False, True)
    assert {a["status"] for a in weekend["accounts"]} == {"non_trading"}

    # 30.11: продажу ввели на 20 000 бумаг, по отчёту — 25 000.
    world.ledger[BROKER][date(2026, 11, 30)]["trades"] = [
        world.trade(SELL, **(world.sale | {"quantity": 20_000, "amount": "11224200.00"}))
    ]
    monday = account(fund_day(trader, "2026-11-30"), "OPIF1-BROKER")
    assert [
        (d["field"], d["system"], d["statement"]) for d in monday["trades"][0]["differences"]
    ] == [
        ("quantity", "20000", "25000"),
        ("amount", "11224200", "14030250"),
    ]
    # Движения 30.11 сошлись: покупка и комиссия по ней.
    assert [(m["kind"], m["trade"], m["status"]) for m in monday["movements"]] == [
        ("settlement", "1000000001", "matched"),
        ("fee", "1000000001", "matched"),
    ]
    assert (monday["closing"]["system"], monday["closing"]["status"]) == ("31996699.95", "matched")
    world.ledger[BROKER][date(2026, 11, 30)]["trades"] = [world.trade(SELL, **world.sale)]
    assert fund_day(trader, "2026-11-30")["reconciled"] is True
    assert fund_day(trader, "2026-12-01")["reconciled"] is True

    # 02.12: купон в системе есть, брокер зачислит его 03.12.
    tuesday = account(fund_day(trader, "2026-12-02"), "OPIF1-BROKER")
    [coupon] = tuesday["movements"]
    assert (coupon["status"], coupon["secid"], coupon["discrepancy"]) == (
        "not_in_statement",
        "SU26238RMFS4",
        "-885000.00",
    )
    assert (tuesday["closing"]["discrepancy"], tuesday["closing"]["status"]) == (
        "-885000.00",
        "mismatched",
    )
    accepted = ops.ok(
        "POST",
        "/acceptances",
        {
            "account_id": str(BROKER),
            "date": "2026-12-02",
            "line": coupon["line"],
            "comment": "Купон ОФЗ 26238 брокер зачислит 03.12",
        },
    )
    assert accepted["status"] == "reconciled"
    assert accepted["movements"][0]["acceptance"]["accepted_by"] == "ops"
    assert (accepted["closing"]["status"], accepted["closing"]["expected"]) == (
        "explained",
        "-885000.00",
    )
    assert [(w["date"], w["secid"], w["discrepancy"]) for w in accepted["waiting"]] == [
        ("2026-12-02", "SU26238RMFS4", "-885000.00")
    ]
    assert fund_day(trader, "2026-12-02")["reconciled"] is True

    # 03.12: купон у брокера сводится с принятой строкой 02.12; банк — вознаграждение.
    thursday = fund_day(trader, "2026-12-03")
    broker_day = account(thursday, "OPIF1-BROKER")
    assert [(m["status"], m["paired"]) for m in broker_day["movements"]] == [
        ("paired", "2026-12-02")
    ]
    assert (broker_day["opening"]["status"], broker_day["closing"]["status"]) == (
        "explained",
        "matched",
    )
    assert broker_day["waiting"] == []
    bank_day = account(thursday, "OPIF1-BANK")
    assert [(m["kind"], m["system"], m["statement"]) for m in bank_day["movements"]] == [
        ("fee_payment", "-20550.00", "-20550.00")
    ]
    assert (bank_day["closing"]["statement"], bank_day["previous"]) == (
        "39979450.00",
        {"date": "2026-12-02", "closing_cash": "40000000.00"},
    )
    assert thursday["reconciled"] is True

    await journal_of_the_week(resources, coupon["line"])


async def test_same_statement_again_changes_nothing(
    feed: Api, ops: Api, trader: Api, resources: Resources
) -> None:
    batch = {"source": "broker-x", "statements": broker_week()}
    feed.ok("POST", "/feed/v1/statements", batch)
    again = feed.ok("POST", "/feed/v1/statements", batch)
    assert (again["created"], again["unchanged"]) == (0, 6)

    # Брокер исправил отчёт за 02.12 — действует новая выписка, прежняя в истории.
    fixed = broker(
        "2026-12-02",
        "46026949.95",
        "46911949.95",
        {"kind": "coupon", "security": "RU000A1038V6", "amount": "885000.00"},
    )
    replaced = feed.ok("POST", "/feed/v1/statements", {"source": "broker-x", "statements": [fixed]})
    assert [s["result"] for s in replaced["statements"]] == ["replaced"]
    # В журнале — что было и что стало.
    async with resources.require_database().session() as session:
        rows = await session.execute(select(outbox.c.payload).order_by(outbox.c.id.desc()))
        last = rows.first()
    assert last is not None
    assert (
        last.payload["data"]["before"]["closing_cash"],
        last.payload["data"]["after"]["closing_cash"],
    ) == (
        "46026949.95",
        "46911949.95",
    )
    history = trader.ok("GET", "/statements", account_id=str(BROKER), date="2026-12-02")
    assert [h["closing_cash"] for h in history] == ["46911949.95", "46026949.95"]
    assert history[0]["movements"][0]["instrument_id"] == str(OFZ)
    tuesday = account(fund_day(trader, "2026-12-02"), "OPIF1-BROKER")
    assert (tuesday["status"], tuesday["statement"]["source"]) == ("reconciled", "broker-x")

    # Пакет хранится как пришёл.
    stored = trader.ok("GET", f"/batches/{history[0]['batch_id']}")
    assert (stored["source"], stored["payload"]["statements"][0]["movements"]) == (
        "broker-x",
        [{"kind": "coupon", "security": "RU000A1038V6", "amount": "885000.00"}],
    )
    manual = ops.ok("POST", "/statements", bank("2026-11-26", "0", "0"))
    assert manual["result"] == "created"
    [entered] = trader.ok("GET", "/statements", account_id=str(BANK), date="2026-11-26")
    assert (entered["source"], entered["received_by"]) == ("manual", "ops")
    assert trader.ok("GET", f"/batches/{entered['batch_id']}")["payload"]["account_id"] == str(BANK)


def test_statement_that_does_not_add_up(feed: Api) -> None:
    # Скрипт потерял строку комиссии: выписка не сходится сама с собой.
    lost = broker(
        "2026-11-30",
        "60000000.00",
        "31996699.95",
        {"kind": "settlement", "trade": "1000000001", "amount": "-28000500.00"},
    )
    found = feed.ok("POST", "/feed/v1/statements", {"source": "broker-x", "statements": [lost]})
    [rejected] = found["rejected"]
    assert rejected["error"] == "statement_does_not_add_up"
    assert rejected["message"] == (
        "OPIF1-BROKER, 30.11.2026: остаток на начало 60 000 000,00 ₽ и движения "
        "−28 000 500,00 ₽ дают 31 999 500,00 ₽, а остаток на конец — 31 996 699,95 ₽. "
        "Не хватает движений на −2 800,05 ₽."
    )


@pytest.mark.parametrize(
    ("statement", "code"),
    [
        (broker("2026-11-27", "0", "0", account="OPIF9-BROKER"), "unknown_account"),
        (broker("2026-12-05", "0", "0"), "future_statement"),
        (
            broker("2026-11-27", "0", "0", trades=[statement_trade(security="SU99999RMFS0")]),
            "unknown_security",
        ),
        (
            broker("2026-11-27", "0", "0", trades=[statement_trade(), statement_trade()]),
            "repeated_trade",
        ),
        (
            broker(
                "2026-11-27",
                "0",
                "0",
                trades=[statement_trade(security="KSU", quantity=0, price="0")],
            ),
            "ksu_not_repo",
        ),
    ],
)
def test_rejected_statements(feed: Api, statement: dict[str, Any], code: str) -> None:
    found = feed.ok(
        "POST",
        "/feed/v1/statements",
        {"source": "broker-x", "statements": [statement, broker("2026-11-26", "0", "0")]},
    )
    assert [r["error"] for r in found["rejected"]] == [code]
    # Хорошая выписка пакета принята.
    assert [(s["index"], s["result"]) for s in found["statements"]] == [(1, "created")]


def test_repeated_statement_in_a_batch(feed: Api) -> None:
    twice = broker("2026-11-26", "0", "0")
    found = feed.ok(
        "POST", "/feed/v1/statements", {"source": "broker-x", "statements": [twice, twice]}
    )
    assert [r["error"] for r in found["rejected"]] == ["repeated_statement"] * 2


def test_repo_with_ksu_statement(feed: Api, world: World, trader: Api) -> None:
    # Прямое РЕПО с КСУ: бумаги нет, только деньги; поля второй части сравниваются.
    repo = {
        "kind": "repo",
        "side": "sell",
        "security": "KSU",
        "settle_date": "2026-11-27",
        "quantity": 0,
        "price": "0",
        "accrued": None,
        "amount": "5000000",
        "fee": "0",
        "repo_rate": "14.5",
        "second_date": "2026-11-30",
        "second_amount": "5005958.90",
    }
    world.ledger[BROKER][date(2026, 11, 27)]["trades"] = [
        world.trade(
            external_id="3000000001",
            kind="repo",
            side="sell",
            instrument_id=None,
            instrument_kind="ksu",
            secid="KSU",
            quantity=0,
            price="0",
            accrued=None,
            amount="5000000.00",
            fee="0.00",
            settle_date="2026-11-27",
            repo_rate="14.500000",
            second_date="2026-11-30",
            second_amount="5005958.90",
        )
    ]
    feed.ok(
        "POST",
        "/feed/v1/statements",
        {
            "source": "broker-x",
            "statements": [
                broker(
                    "2026-11-27",
                    "60000000",
                    "60000000",
                    trades=[statement_trade(external_id="3000000001", **repo)],
                )
            ],
        },
    )
    friday = account(fund_day(trader, "2026-11-27"), "OPIF1-BROKER")
    assert [
        (t["status"], t["differences"], t["statement"]["instrument_id"]) for t in friday["trades"]
    ] == [("matched", [], None)]


@pytest.mark.parametrize(
    "body",
    [
        broker("2026-11-27", "0", "0", trades=[statement_trade(kind="repo")]),
        broker("2026-11-27", "0", "0", trades=[statement_trade(repo_rate="14")]),
        broker("2026-11-27", "0", "0", trades=[statement_trade(security="KSU")]),
        broker("2026-11-27", "0", "0", trades=[statement_trade(quantity=0)]),
        broker("2026-11-27", "0", "0", {"kind": "fee", "amount": "-1"}),
        broker("2026-11-27", "0", "0", {"kind": "coupon", "amount": "1"}),
        broker("2026-11-27", "0", "0", {"kind": "transfer", "trade": "1", "amount": "1"}),
        broker("2026-11-27", "0", "0", {"kind": "transfer", "amount": "0"}),
    ],
)
def test_malformed_statements(feed: Api, body: dict[str, Any]) -> None:
    response = feed.call(
        "POST", "/feed/v1/statements", {"source": "broker-x", "statements": [body]}
    )
    assert response.status_code == 422


# --- кто что может -------------------------------------------------------------


def test_who_may_do_what(feed: Api, ops: Api, trader: Api) -> None:
    batch = {"source": "broker-x", "statements": [broker("2026-11-26", "0", "0")]}
    # Пакет — только от скрипта брокера; выписку вручную и принятие — сопровождение.
    assert ops.call("POST", "/feed/v1/statements", batch).status_code == 403
    assert trader.call("POST", "/statements", bank("2026-11-26", "0", "0")).status_code == 403
    assert feed.call("GET", f"/funds/{FUND}/days/2026-11-26").status_code == 403
    accept = {
        "account_id": str(BANK),
        "date": "2026-11-26",
        "line": "balance:closing",
        "comment": "Банк",
    }
    assert trader.call("POST", "/acceptances", accept).status_code == 403


def test_hidden_fund_and_account(trader: Api, client: TestClient, tokens: TokenFactory) -> None:
    assert trader.call("GET", f"/funds/{OTHER_FUND}/days/2026-11-26").status_code == 404
    # Портфель — не фонд.
    assert trader.call("GET", f"/funds/{BROKER}/days/2026-11-26").status_code == 404
    hidden = trader.call("GET", "/statements", account_id=str(OTHER_BROKER), date="2026-11-26")
    assert hidden.status_code == 404
    ops = Api(client, tokens, "trader", "operations")
    body = bank("2026-11-26", "0", "0") | {"account_id": str(OTHER_BROKER)}
    assert ops.call("POST", "/statements", body).status_code == 404


def test_batch_of_a_hidden_account(feed: Api, trader: Api) -> None:
    found = feed.ok(
        "POST",
        "/feed/v1/statements",
        {
            "source": "broker-x",
            "statements": [broker("2026-11-26", "0", "0", account="OPIF2-BROKER")],
        },
    )
    assert trader.call("GET", f"/batches/{found['batch_id']}").status_code == 404
    assert trader.call("GET", f"/batches/{BUY}").status_code == 404


# --- принятие ------------------------------------------------------------------


def test_what_cannot_be_accepted(feed: Api, ops: Api) -> None:
    feed.ok("POST", "/feed/v1/statements", {"source": "broker-x", "statements": broker_week()})
    body = {"account_id": str(BROKER), "date": "2026-11-27", "comment": "Проверили"}
    missing = ops.call("POST", "/acceptances", body | {"line": "movement:coupon::"})
    assert (missing.status_code, missing.json()["detail"]["code"]) == (404, "line_not_found")
    matched = ops.call("POST", "/acceptances", body | {"line": "trade:1000000001"})
    assert (matched.status_code, matched.json()["detail"]["code"]) == (422, "nothing_to_accept")
    short = ops.call("POST", "/acceptances", body | {"line": "trade:1000000001", "comment": "ок"})
    assert short.status_code == 422


def test_trade_line_acceptance(feed: Api, ops: Api, world: World) -> None:
    feed.ok("POST", "/feed/v1/statements", {"source": "broker-x", "statements": broker_week()})
    world.ledger[BROKER][date(2026, 11, 27)]["trades"] = [world.trade(fee="2800.00")]
    found = ops.ok(
        "POST",
        "/acceptances",
        {
            "account_id": str(BROKER),
            "date": "2026-11-27",
            "line": "trade:1000000001",
            "comment": "Брокер округлил комиссию",
        },
    )
    assert (found["status"], found["trades"][0]["acceptance"]["comment"]) == (
        "reconciled",
        "Брокер округлил комиссию",
    )


# --- статусы дня ---------------------------------------------------------------


def test_day_statuses(feed: Api, trader: Api, world: World) -> None:
    # Выписки нет — сверять не с чем.
    assert account(fund_day(trader, "2026-11-27"), "OPIF1-BROKER")["status"] == "no_statement"
    feed.ok("POST", "/feed/v1/statements", {"source": "broker-x", "statements": broker_week()})
    # Фонд ждёт пересчёта с 30.11 — цифры системы за 30.11 и позже ещё не новые.
    world.waiting[BROKER] = "2026-11-30"
    assert account(fund_day(trader, "2026-11-30"), "OPIF1-BROKER")["status"] == "recalculating"
    assert account(fund_day(trader, "2026-11-27"), "OPIF1-BROKER")["status"] == "reconciled"
    world.waiting.clear()
    world.reached = date(2026, 12, 2)
    third = account(fund_day(trader, "2026-12-03"), "OPIF1-BROKER")
    assert (third["status"], third["calculated"]) == ("not_calculated", False)


@pytest.mark.parametrize(
    ("service", "code"),
    [
        ("booking", "booking_unavailable"),
        ("portfolio", "portfolio_unavailable"),
    ],
)
def test_neighbours_down(trader: Api, world: World, service: str, code: str) -> None:
    world.down.add(service)
    response = trader.call("GET", f"/funds/{FUND}/days/2026-11-27")
    assert (response.status_code, response.json()["detail"]["code"]) == (503, code)


def test_refdata_down_at_intake(feed: Api, world: World) -> None:
    world.down.add("refdata")
    batch = {
        "source": "broker-x",
        "statements": [broker("2026-11-27", "0", "0", trades=[statement_trade()])],
    }
    response = feed.call("POST", "/feed/v1/statements", batch)
    assert (response.status_code, response.json()["detail"]["code"]) == (503, "refdata_unavailable")


def test_booking_error_is_unavailable(trader: Api, world: World) -> None:
    # Сервис сделок ответил ошибкой — для сверки он недоступен.
    world.failing.add("booking")
    response = trader.call("GET", f"/funds/{FUND}/days/2026-11-27")
    assert (response.status_code, response.json()["detail"]["code"]) == (
        503,
        "booking_unavailable",
    )
