"""Закрытый период: сквозной пример методики шага 8 (design/08, «Закрытие дня»)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import func, select

from am_common.app import Resources
from am_common.clock import MOSCOW
from am_common.events import SYSTEM_ACTOR, Event
from am_common.testing import TokenFactory
from booking.db import day_snapshot, outbox, recalc_queue, roll_run
from booking.recalc import MarketEvents, marketdata_client, recalc_once, refdata_client

pytestmark = pytest.mark.integration

FUND = UUID("00000000-0000-0000-0000-00000000f001")
TREASURY = UUID("00000000-0000-0000-0000-00000000a001")
BONDS = UUID("00000000-0000-0000-0000-00000000b001")
OTHER_FUND = UUID("00000000-0000-0000-0000-00000000f002")
OFZ = UUID("00000000-0000-0000-0000-000000026238")
STALE = timedelta(minutes=10)
NOW = datetime(2026, 12, 3, 12, 0, tzinfo=MOSCOW)
CLOSING = f"/funds/{FUND}/closing"


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

    def refused(self, method: str, path: str, body: Any = None) -> tuple[str, str, str]:
        detail = self.call(method, path, body).json()["detail"]
        return detail["code"], detail["message"], method


@pytest.fixture
def trader(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "trader", "trading")


@pytest.fixture
def ops(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "ops", "operations")


@pytest.fixture
def admin(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "admin", "admin")


@pytest.fixture
def closer(client: TestClient, tokens: TokenFactory) -> Api:
    """Сервис сверки под своей служебной учётной записью."""
    return Api(client, tokens, "service-account-am-reconciliation", "close_day")


async def recalc(resources: Resources, now: datetime = NOW) -> None:
    refdata, marketdata = refdata_client(resources), marketdata_client(resources)
    while await recalc_once(
        resources.require_database(), refdata, now, STALE, marketdata=marketdata
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


SALE = {
    "side": "sell",
    "quantity": 25_000,
    "price": "52.60",
    "trade_at": "2026-11-30T12:10:00+03:00",
    "settle_date": "2026-12-01",
}


def units(day: str = "2026-11-26") -> dict[str, Any]:
    return {
        "kind": "unit_issue",
        "fund_id": str(FUND),
        "op_date": day,
        "units": "100000",
        "amount": "100000000",
        "planned_date": day,
    }


@pytest.fixture
async def week(trader: Api, ops: Api, resources: Resources) -> dict[str, Any]:
    """Неделя «ОПИФ 1»: паи, перевод, покупка 27.11, продажа 30.11."""
    issue = ops.ok("POST", "/operations", units())
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
    buy = trader.ok("POST", "/trades", trade())
    sale = trader.ok("POST", "/trades", trade(**SALE))
    await recalc(resources)
    return {"buy": buy, "sale": sale, "issue": issue}


def close(closer: Api, day: str) -> Any:
    body = {"day": day, "fund_code": "OPIF1", "closed_by": {"subject": "u-ops", "username": "ops"}}
    return closer.call("POST", f"/funds/{FUND}/close", body)


def closing(api: Api) -> dict[str, Any]:
    found: dict[str, Any] = api.ok("GET", CLOSING)
    return found


async def test_days_close_in_order(week: dict[str, Any], closer: Api, trader: Api) -> None:
    first = closing(trader)
    assert (first["closed_through"], first["next_day"], first["history"]) == (
        None,
        "2026-11-26",
        [],
    )
    # Строго подряд: не закрыт 26.11 — не закрыть и 27.11.
    early = close(closer, "2026-11-27").json()["detail"]
    assert (early["code"], early["message"]) == (
        "close_out_of_order",
        "Дни закрываются подряд: следующий к закрытию — 26.11.2026.",
    )

    for day in ("2026-11-26", "2026-11-27", "2026-11-30"):
        assert close(closer, day).is_success

    found = closing(trader)
    # Закрыли понедельник — закрыты и суббота с воскресеньем (решение 126).
    assert (found["closed_through"], found["next_day"]) == ("2026-11-30", "2026-12-01")
    assert [
        (h["action"], h["first_day"], h["last_day"], h["author"]) for h in found["history"]
    ] == [
        ("close", "2026-11-26", "2026-11-26", "ops"),
        ("close", "2026-11-27", "2026-11-27", "ops"),
        ("close", "2026-11-28", "2026-11-30", "ops"),
    ]


async def test_closed_day_refuses_changes(
    week: dict[str, Any], closer: Api, trader: Api, ops: Api
) -> None:
    for day in ("2026-11-26", "2026-11-27", "2026-11-30"):
        close(closer, day)
    sale, buy = week["sale"], week["buy"]
    message = (
        "30.11.2026 закрыт 03.12.2026. Ввести, исправить, отменить или подтвердить сделку "
        "можно после повторного открытия дня — его открывает администратор."
    )
    # Продажу 30.11 исправить нельзя, хотя дата расчётов 01.12 ещё открыта.
    assert ops.refused(
        "POST",
        f"/trades/{sale['id']}/versions",
        trade(**SALE) | {"quantity": 20_000, "comment": "По отчёту брокера"},
    ) == (
        "day_closed",
        message,
        "POST",
    )
    assert trader.refused("POST", f"/trades/{sale['id']}/cancel", {"comment": "ошибка"})[0] == (
        "day_closed"
    )
    assert ops.refused("POST", f"/trades/{buy['id']}/confirm", {})[0] == "day_closed"
    assert trader.refused("POST", "/trades", trade())[0] == "day_closed"
    # Перенести сделку из открытого дня в закрытый — тоже нельзя.
    later = trader.ok(
        "POST", "/trades", trade(trade_at="2026-12-01T10:00:00+03:00", settle_date="2026-12-02")
    )
    assert (
        trader.refused("POST", f"/trades/{later['id']}/versions", trade(comment="Перенос"))[0]
        == "day_closed"
    )
    operation = ops.refused("POST", "/operations", units("2026-11-27"))
    assert operation[:2] == (
        "day_closed",
        "27.11.2026 закрыт 03.12.2026. Провести, отметить или отменить операцию можно после "
        "повторного открытия дня — его открывает администратор.",
    )
    assert ops.refused("POST", f"/operations/{week['issue']['id']}/cancel", {"comment": "ошибка"})[
        0
    ] == ("day_closed")
    rate = {"kind": "management", "rate": "1.5", "valid_from": "2026-11-30"}
    assert ops.refused("POST", f"/funds/{FUND}/fee-rates", rate)[1].startswith(
        "30.11.2026 закрыт 03.12.2026. Изменить ставку комиссии"
    )
    # День до начала истории фонда тоже закрыт: его закрыло первое закрытие.
    assert trader.refused("POST", "/trades", trade(trade_at="2026-11-20T10:00:00+03:00"))[
        1
    ].startswith("20.11.2026 закрыт 03.12.2026.")
    # Открытый период — как прежде; карточка сделки знает границу.
    assert ops.ok("POST", f"/trades/{later['id']}/confirm", {})["closed_through"] == "2026-11-30"
    assert trader.ok("GET", f"/trades/{sale['id']}")["closed_through"] == "2026-11-30"
    settle = ops.ok("POST", "/operations", units("2026-12-01"))
    assert ops.ok("POST", f"/operations/{settle['id']}/settle", {"settled_date": "2026-12-01"})


async def test_settlement_mark_in_a_closed_day(
    week: dict[str, Any], closer: Api, ops: Api, resources: Resources
) -> None:
    # Выдачу 27.11 ввели, а расчёт не отметили; день закрыли — отметить задним числом нельзя.
    late = ops.ok("POST", "/operations", units("2026-11-27"))
    await recalc(resources)
    for day in ("2026-11-26", "2026-11-27"):
        close(closer, day)
    assert ops.refused("POST", f"/operations/{late['id']}/settle", {"settled_date": "2026-11-27"})[
        0
    ] == ("day_closed")


async def test_closed_days_are_not_recalculated(
    week: dict[str, Any], closer: Api, resources: Resources
) -> None:
    for day in ("2026-11-26", "2026-11-27", "2026-11-30"):
        close(closer, day)
    database = resources.require_database()
    # Сервис цен дослал цены с 26.11 — пересчёт только с первого открытого дня (решение 128).
    await MarketEvents(database, clock=lambda: NOW).handle(
        Event(
            service="marketdata",
            type="prices.backfilled",
            actor=SYSTEM_ACTOR,
            entity_type="x",
            entity_id="2026-11-26",
            data={
                "instruments": [{"id": str(OFZ), "secid": "SU26238RMFS4", "since": "2026-11-26"}]
            },
        )
    )
    async with database.session() as session:
        assert (await session.execute(select(recalc_queue.c.since))).scalar_one() == date(
            2026, 12, 1
        )
    # Фонд ждал пересчёта с 27.11, а его закрыли по 30.11: воркер всё равно начнёт с 01.12.
    async with database.session() as session, session.begin():
        await session.execute(recalc_queue.update().values(since=date(2026, 11, 27)))
    await recalc(resources)
    async with database.session() as session:
        last = (
            await session.execute(select(roll_run).order_by(roll_run.c.finished_at.desc()))
        ).first()
        replaced = (
            await session.execute(
                select(func.count()).where(
                    day_snapshot.c.day <= date(2026, 11, 30),
                    day_snapshot.c.replaced_at.is_not(None),
                )
            )
        ).scalar_one()
    assert last is not None
    assert last.since == date(2026, 12, 1)
    assert replaced == 0


async def test_what_stops_a_closing(
    week: dict[str, Any], closer: Api, trader: Api, resources: Resources
) -> None:
    for day in ("2026-11-26", "2026-11-27", "2026-11-30"):
        close(closer, day)
    # Правка за 01.12 ждёт пересчёта: сначала пересчёт, потом сверка и закрытие.
    trader.ok(
        "POST", "/trades", trade(trade_at="2026-12-01T10:00:00+03:00", settle_date="2026-12-02")
    )
    waiting = close(closer, "2026-12-01").json()["detail"]
    assert (waiting["code"], waiting["message"]) == (
        "recalculating",
        "Фонд пересчитывается с 01.12.2026: сначала пересчёт, потом сверка и закрытие.",
    )
    await recalc(resources)
    assert close(closer, "2026-12-01").is_success
    assert close(closer, "2026-12-02").is_success
    # Сегодня ещё не кончилось.
    today = close(closer, "2026-12-03").json()["detail"]
    assert today["code"] == "day_not_over"


async def test_a_day_the_roll_has_not_reached(
    ops: Api, closer: Api, trader: Api, resources: Resources
) -> None:
    # Без истории закрывать нечего.
    assert closing(trader)["next_day"] is None
    assert close(closer, "2026-11-26").json()["detail"]["code"] == "no_history"
    ops.ok("POST", "/operations", units())
    # Ролл дошёл только до 27.11.
    await recalc(resources, datetime(2026, 11, 27, 20, 0, tzinfo=MOSCOW))
    assert close(closer, "2026-11-26").is_success
    assert close(closer, "2026-11-27").is_success
    found = close(closer, "2026-11-30").json()["detail"]
    assert (found["code"], found["message"]) == (
        "not_calculated",
        "Расчёта фонда за 30.11.2026 ещё нет.",
    )


async def test_admin_reopens(
    week: dict[str, Any], closer: Api, admin: Api, ops: Api, resources: Resources
) -> None:
    for day in ("2026-11-26", "2026-11-27", "2026-11-30"):
        close(closer, day)
    body = {"day": "2026-11-30", "comment": "Брокер исправил отчёты: комиссия по продаже 30.11"}
    # Открывает только администратор.
    assert ops.call("POST", f"/funds/{FUND}/reopen", body).status_code == 403
    # Закрывает только сервис сверки.
    assert close(ops, "2026-12-01").status_code == 403

    found = admin.ok("POST", f"/funds/{FUND}/reopen", body)
    assert (found["closed_through"], found["next_day"]) == ("2026-11-29", "2026-11-30")
    assert found["history"][-1] | {"created_at": None} == {
        "action": "reopen",
        "first_day": "2026-11-30",
        "last_day": "2026-11-30",
        "closed_through": "2026-11-29",
        "author": "admin",
        "comment": "Брокер исправил отчёты: комиссия по продаже 30.11",
        "created_at": None,
    }
    # Сопровождение исправляет продажу, фонд пересчитывается, день закрывают снова.
    ops.ok(
        "POST",
        f"/trades/{week['sale']['id']}/versions",
        trade(**SALE) | {"fee": "1400.50", "comment": "Комиссия по отчёту брокера"},
    )
    await recalc(resources)
    assert close(closer, "2026-11-30").is_success

    late = admin.call("POST", f"/funds/{FUND}/reopen", body | {"day": "2026-12-01"}).json()
    assert late["detail"]["message"] == "01.12.2026 не закрыт: закрыто по 30.11.2026."
    async with resources.require_database().session() as session:
        rows = (await session.execute(select(outbox.c.payload).order_by(outbox.c.id))).all()
    journal = [r.payload for r in rows if r.payload["type"].startswith("day.")]
    assert [(e["type"], e["actor"]["username"], e["data"]["fund"]["code"]) for e in journal] == [
        ("day.closed", "ops", "OPIF1"),
        ("day.closed", "ops", "OPIF1"),
        ("day.closed", "ops", "OPIF1"),
        ("day.reopened", "admin", "OPIF1"),
        ("day.closed", "ops", "OPIF1"),
    ]
    assert journal[3]["data"]["after"] == {
        "first_day": "2026-11-30",
        "last_day": "2026-11-30",
        "closed_through": "2026-11-29",
    }


def test_reopen_without_closings_and_hidden_funds(admin: Api, trader: Api) -> None:
    found = admin.call(
        "POST", f"/funds/{FUND}/reopen", {"day": "2026-11-30", "comment": "Проверка"}
    )
    assert found.json()["detail"]["message"] == "30.11.2026 не закрыт."
    # Второй фонд трейдеру не виден; портфель — не фонд.
    assert trader.call("GET", f"/funds/{OTHER_FUND}/closing").status_code == 404
    assert admin.call("GET", f"/funds/{BONDS}/closing").status_code == 404
