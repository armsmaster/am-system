"""Фоновые задачи: список бумаг, история ISS, данные дня ISS — и что из этого видно в API."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from am_common.app import Resources
from am_common.clock import MOSCOW
from am_common.db import Database
from am_common.testing import TokenFactory
from marketdata.commands import refresh_instruments
from marketdata.db import accrued_interest, face_value, iss_poll, outbox, quote
from marketdata.refdata import refdata_client
from marketdata.repository import Repository
from marketdata.scheduler import (
    history_target,
    history_worker,
    instruments_worker,
    intraday_worker,
    iss_client,
    load_due_history,
    poll_if_due,
)
from marketdata.settings import MarketdataSettings

if TYPE_CHECKING:
    from conftest import World

pytestmark = pytest.mark.integration

OFZ = UUID("00000000-0000-0000-0000-000000026238")
LINKER = UUID("00000000-0000-0000-0000-00000010f504")
FUND = UUID("00000000-0000-0000-0000-0000000001d7")
NOW = datetime(2026, 10, 27, 11, 5, tzinfo=MOSCOW)


def at(day: int, hour: int, minute: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=MOSCOW)


async def prepared(resources: Resources) -> None:
    await refresh_instruments(resources.require_database(), refdata_client(resources))


async def count(resources: Resources, table: Any, **where: Any) -> int:
    async with resources.require_database().session() as session:
        statement = select(table)
        for name, value in where.items():
            statement = statement.where(table.c[name] == value)
        return len((await session.execute(statement)).all())


async def test_instrument_list_and_boards_come_from_refdata(resources: Resources) -> None:
    await prepared(resources)

    async with resources.require_database().session() as session:
        records = {r.secid: r for r in await Repository(session).instruments()}
    assert {s: (r.board, r.short_name) for s, r in records.items()} == {
        "LQDT": ("TQBR", "ВИМ Ликвидность"),
        "RU000A10F504": ("TQCB", "ВЭБ2Р-58"),
        "SU26238RMFS4": ("TQOB", "ОФЗ 26238"),
    }


async def test_history_target_waits_for_the_morning(
    resources: Resources, settings: MarketdataSettings
) -> None:
    refdata = refdata_client(resources)

    # Вторник утром: итоги понедельника уже есть.
    assert await history_target(refdata, settings, at(27, 7)) == date(2026, 10, 26)
    # Понедельник до 6:30: итогов пятницы ещё может не быть — берём четверг.
    assert await history_target(refdata, settings, at(26, 6)) == date(2026, 10, 22)
    # Четверг 5.11 днём: среда 4.11 — праздник, последний торговый день — 3.11.
    assert await history_target(refdata, settings, at(5, 12, month=11)) == date(2026, 11, 3)


async def test_history_is_loaded_once_per_day(resources: Resources, world: World) -> None:
    await prepared(resources)
    refdata, iss = refdata_client(resources), iss_client(resources)

    assert await load_due_history(resources, refdata, iss, NOW) == 3
    assert await load_due_history(resources, refdata, iss, NOW) == 0

    # Итоги прошлых дней меняют оценку позиций — одно событие на загрузку.
    async with resources.require_database().session() as session:
        [event] = [row.payload for row in await session.execute(select(outbox.c.payload))]
    assert (event["type"], event["actor"]["username"]) == ("prices.backfilled", "system")
    assert [i["secid"] for i in event["data"]["instruments"]] == [
        "LQDT",
        "RU000A10F504",
        "SU26238RMFS4",
    ]
    assert event["data"]["since"] == min(i["since"] for i in event["data"]["instruments"])

    assert await count(resources, quote, instrument_id=OFZ) == 5 * 3  # закрытие, VWAP, рыночная
    assert await count(resources, face_value, instrument_id=LINKER) == 116
    assert await count(resources, accrued_interest, instrument_id=FUND) == 0
    async with resources.require_database().session() as session:
        record = await Repository(session).get(OFZ)
    assert record.history_till == date(2026, 10, 26)
    assert (
        world.requests.count(
            "/iss/history/engines/stock/markets/bonds/securities/RU000A10F504.json"
        )
        == 2
    )


async def test_failed_history_is_retried_later(resources: Resources, world: World) -> None:
    await prepared(resources)
    refdata, iss = refdata_client(resources), iss_client(resources)
    world.iss_down = True

    assert await load_due_history(resources, refdata, iss, NOW) == 0

    world.iss_down = False
    assert await load_due_history(resources, refdata, iss, NOW) == 3


async def test_failed_save_releases_the_claim(
    resources: Resources, monkeypatch: pytest.MonkeyPatch
) -> None:
    await prepared(resources)
    refdata, iss = refdata_client(resources), iss_client(resources)

    async def broken(*_args: Any) -> None:
        raise SQLAlchemyError("insert failed")

    with monkeypatch.context() as patch:
        patch.setattr(Repository, "upsert_points", broken)
        assert await load_due_history(resources, refdata, iss, NOW) == 0

    # Сразу, без получасового ожидания захвата.
    assert await load_due_history(resources, refdata, iss, NOW) == 3


async def test_history_already_claimed_by_another_instance(resources: Resources) -> None:
    await prepared(resources)
    async with resources.require_database().session() as session, session.begin():
        for instrument_id in (OFZ, LINKER, FUND):
            await Repository(session).claim_history(instrument_id, NOW, timedelta(minutes=30))

    assert (
        await load_due_history(resources, refdata_client(resources), iss_client(resources), NOW)
        == 0
    )


async def test_intraday_poll_once_per_slot_during_trading(resources: Resources) -> None:
    await prepared(resources)
    refdata, iss = refdata_client(resources), iss_client(resources)

    assert await poll_if_due(resources, refdata, iss, NOW) is True
    assert await poll_if_due(resources, refdata, iss, at(27, 11, 9)) is False  # тот же слот
    # До начала торгов, после конца, в праздник — не опрашиваем.
    assert await poll_if_due(resources, refdata, iss, at(27, 6, 0)) is False
    assert await poll_if_due(resources, refdata, iss, at(27, 23, 58)) is False
    assert await poll_if_due(resources, refdata, iss, at(4, 12, month=11)) is False

    # 4 цены по двум облигациям и по паю; НКД и номинал на дату расчётов — по облигациям.
    assert await count(resources, quote) == 12
    assert await count(resources, accrued_interest, as_of=date(2026, 10, 28)) == 2
    async with resources.require_database().session() as session:
        [slot] = (await session.execute(select(iss_poll))).all()
    assert (slot.slot.astimezone(MOSCOW).time(), slot.polled, slot.failed) == (time(11, 5), 3, 0)


async def settlement_events(resources: Resources) -> list[dict[str, Any]]:
    async with resources.require_database().session() as session:
        payloads = (await session.execute(select(outbox.c.payload))).scalars().all()
    return [p["data"] for p in payloads if p["type"] == "settlement.updated"]


async def test_new_settlement_data_recalculates_today(resources: Resources) -> None:
    """Пересчёт дня мог пройти до того, как ISS опубликовал НКД и номинал на T+1:
    новые значения ставят фонды с бумагой в пересчёт с сегодня (решение 149)."""
    await prepared(resources)
    refdata, iss = refdata_client(resources), iss_client(resources)

    assert await poll_if_due(resources, refdata, iss, NOW) is True
    [first] = await settlement_events(resources)
    assert first["since"] == "2026-10-27"
    assert sorted(i["secid"] for i in first["instruments"]) == ["RU000A10F504", "SU26238RMFS4"]

    # Следующий опрос с теми же значениями — без события.
    assert await poll_if_due(resources, refdata, iss, at(27, 11, 10)) is True
    assert len(await settlement_events(resources)) == 1

    # ISS исправил НКД на дату расчётов по ОФЗ — событие только по ней.
    async with resources.require_database().session() as session, session.begin():
        await session.execute(
            update(accrued_interest)
            .where(accrued_interest.c.instrument_id == OFZ)
            .values(value=Decimal(0))
        )
    assert await poll_if_due(resources, refdata, iss, at(27, 11, 15)) is True
    *_, last = await settlement_events(resources)
    assert [i["secid"] for i in last["instruments"]] == ["SU26238RMFS4"]


async def test_failed_intraday_poll_is_recorded(resources: Resources, world: World) -> None:
    await prepared(resources)
    world.iss_down = True

    assert (
        await poll_if_due(resources, refdata_client(resources), iss_client(resources), NOW) is True
    )

    async with resources.require_database().session() as session:
        [slot] = (await session.execute(select(iss_poll))).all()
    assert (slot.polled, slot.failed) == (0, 1)


async def test_history_without_trading_is_not_announced(resources: Resources, world: World) -> None:
    await prepared(resources)
    world.iss_empty = {"LQDT", "RU000A10F504", "SU26238RMFS4"}

    refdata, iss = refdata_client(resources), iss_client(resources)

    assert await load_due_history(resources, refdata, iss, NOW) == 3
    assert await count(resources, outbox) == 0


async def test_instruments_wait_for_their_board(resources: Resources) -> None:
    """Без режима торгов ISS отдаёт строки всех режимов — такие бумаги ждут."""
    await prepared(resources)
    async with resources.require_database().session() as session, session.begin():
        await Repository(session).set_board(LINKER, None)
    refdata, iss = refdata_client(resources), iss_client(resources)

    assert await load_due_history(resources, refdata, iss, NOW) == 2
    assert await poll_if_due(resources, refdata, iss, NOW) is True
    assert await count(resources, quote, instrument_id=LINKER) == 0


async def test_a_point_twice_in_one_answer_is_stored_once(resources: Resources) -> None:
    await prepared(resources)
    point = {"instrument_id": OFZ, "as_of": date(2026, 10, 28), "source": "iss_settlement"}
    async with resources.require_database().session() as session, session.begin():
        await Repository(session).upsert_points(
            accrued_interest,
            [{**point, "value": Decimal("28.58")}, {**point, "value": Decimal("28.59")}],
        )

    async with resources.require_database().session() as session:
        points = await Repository(session).points(accrued_interest, OFZ)
    assert points == {date(2026, 10, 28): Decimal("28.59")}


async def test_nothing_to_poll_without_instruments(resources: Resources) -> None:
    assert (
        await poll_if_due(resources, refdata_client(resources), iss_client(resources), NOW) is True
    )
    assert await count(resources, quote) == 0


async def test_without_refdata_weekdays_are_trading_days(
    resources: Resources, world: World
) -> None:
    await prepared(resources)
    world.refdata_down = True

    # Справочник недоступен: 4.11 считаем рабочей средой.
    assert (
        await poll_if_due(
            resources, refdata_client(resources), iss_client(resources), at(4, 12, month=11)
        )
        is True
    )


@pytest.mark.parametrize(
    "make_worker",
    [
        instruments_worker,
        # Часы — во время торгов: в выходной опрос данных дня не идёт в БД и не падает.
        lambda s: history_worker(s, clock=lambda: NOW),
        lambda s: intraday_worker(s, clock=lambda: NOW),
    ],
    ids=["instruments", "history", "intraday"],
)
async def test_workers_keep_running_after_errors(
    settings: MarketdataSettings,
    caplog: pytest.LogCaptureFixture,
    make_worker: Callable[[MarketdataSettings], Any],
) -> None:
    broken = Database(
        "postgresql+asyncpg://user:secret@127.0.0.1:1/am", "marketdata", connect_timeout=0.2
    )
    quick = settings.model_copy(
        update={
            "refdata_sync_seconds": 0.05,
            "history_check_seconds": 0.05,
            "intraday_seconds": 0.05,
        }
    )

    def refdata(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/token"):
            return httpx2.Response(200, json={"access_token": "t", "expires_in": 300})
        return httpx2.Response(200, json=[])

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(refdata)) as http:
        resources = Resources(settings=quick, http=http, database=broken, redis=None)
        worker = make_worker(quick)
        task = asyncio.ensure_future(worker(resources))
        await asyncio.sleep(1)

        assert not task.done()
        task.cancel()
    await broken.dispose()
    assert "will retry" in caplog.text


async def test_accrued_interest_and_face_value_on_dates(
    client: TestClient, tokens: TokenFactory, resources: Resources, world: World
) -> None:
    # История ISS загружена — как утром после фоновой задачи.
    await prepared(resources)
    await load_due_history(resources, refdata_client(resources), iss_client(resources), NOW)
    token = tokens.issue(subject="u-ops", username="ops", roles=["operations"])
    headers = {"Authorization": f"Bearer {token}"}

    def get(path: str, **params: str) -> Any:
        return client.get(path, params=params, headers=headers).json()

    # Точка ISS как есть; воскресенье — от начала купонного периода: 35,40 / 182 × 144 дня.
    assert get(f"/instruments/{OFZ}/accrued-interest", date="2026-10-26") == {
        "date": "2026-10-26",
        "value": "28.200000",
        "method": "iss",
        "note": "точка ISS",
    }
    sunday = get(f"/instruments/{OFZ}/accrued-interest", date="2026-10-25")
    assert (sunday["value"], sunday["method"]) == ("28.01", "period_start")
    # Линкер: номинал между точками растёт каждый день.
    saturday = get(f"/instruments/{LINKER}/face-value", date="2026-05-16")
    assert (saturday["value"], saturday["method"]) == ("1000.39", "geometric")
    # Отметка для оценки — номинал на дату расчётов T+1, как НКД (решение 149): в пятницу
    # 23.10 — номинал понедельника 26.10, с индексацией за выходные.
    friday, monday = (
        get(f"/instruments/{LINKER}/face-value", date=day)["value"]
        for day in ("2026-10-23", "2026-10-26")
    )
    [mark] = get(f"/instruments/{LINKER}/marks", **{"from": "2026-10-23", "till": "2026-10-23"})
    assert (mark["settle_date"], mark["face_value"]) == ("2026-10-26", monday)
    assert friday < monday
    # До выпуска номинала нет; у пая нет НКД; без справочника НКД на день без точки не досчитать.
    assert (
        get(f"/instruments/{LINKER}/face-value", date="2026-05-01")["detail"]["code"] == "not_found"
    )
    assert get(f"/instruments/{FUND}/accrued-interest", date="2026-10-25")["detail"]["code"] == (
        "not_found"
    )
    world.refdata_down = True
    assert get(f"/instruments/{OFZ}/accrued-interest", date="2026-10-24")["detail"]["code"] == (
        "refdata_unavailable"
    )
