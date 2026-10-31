"""Пересчёт: очередь, захват, отказ справочника, новый день, события справочника, воркеры."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
import redis.asyncio as aioredis
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from am_common.app import Resources
from am_common.clock import MOSCOW
from am_common.db import Database
from am_common.events import SYSTEM_ACTOR, Event, stream_name
from am_common.testing import TokenFactory
from booking.db import day_snapshot, recalc_queue
from booking.recalc import (
    MarketEvents,
    extend_to_today,
    market_consumer,
    recalc_once,
    recalc_worker,
    refdata_client,
)
from booking.repository import Repository
from booking.settings import BookingSettings

if TYPE_CHECKING:
    from conftest import World

pytestmark = pytest.mark.integration

FUND = UUID("00000000-0000-0000-0000-00000000f001")
BONDS = UUID("00000000-0000-0000-0000-00000000b001")
OTHER_FUND = UUID("00000000-0000-0000-0000-00000000f002")
OTHER_BONDS = UUID("00000000-0000-0000-0000-00000000b002")
OFZ = UUID("00000000-0000-0000-0000-000000026238")
LQDT = UUID("00000000-0000-0000-0000-0000000001d7")
NOW = datetime(2026, 12, 3, 12, 0, tzinfo=MOSCOW)
STALE = timedelta(minutes=10)


def enter(client: TestClient, tokens: TokenFactory, **changes: Any) -> dict[str, Any]:
    token = tokens.issue(subject="u-ops", username="ops", roles=["operations"])
    response = client.post(
        "/trades",
        json={
            "portfolio_id": str(BONDS),
            "instrument_id": str(OFZ),
            "side": "buy",
            "quantity": 50_000,
            "price": "52.50",
            "trade_at": "2026-11-27T11:42:00+03:00",
            "settle_date": "2026-11-30",
            **changes,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.is_success, response.text
    result: dict[str, Any] = response.json()
    return result


async def queue(resources: Resources) -> dict[UUID, date]:
    async with resources.require_database().session() as session:
        rows = (await session.execute(select(recalc_queue))).all()
    return {row.fund_id: row.since for row in rows}


async def run(resources: Resources, now: datetime = NOW) -> UUID | None:
    return await recalc_once(resources.require_database(), refdata_client(resources), now, STALE)


async def test_failed_recalc_is_retried(
    client: TestClient, tokens: TokenFactory, resources: Resources, world: World
) -> None:
    enter(client, tokens)
    world.down.add("refdata")

    assert await run(resources) is None
    assert await queue(resources) == {FUND: date(2026, 11, 27)}  # захват снят, фонд ждёт

    world.down.clear()
    assert await run(resources) == FUND
    assert await queue(resources) == {}


async def test_claimed_fund_waits_for_the_claim(
    client: TestClient, tokens: TokenFactory, resources: Resources
) -> None:
    enter(client, tokens)
    async with resources.require_database().session() as session, session.begin():
        assert await Repository(session).claim(NOW, STALE) is not None

    assert await run(resources) is None
    # Упавший экземпляр: через срок захвата фонд заберёт другой.
    assert await run(resources, NOW + STALE + timedelta(seconds=1)) == FUND


async def test_marked_again_during_recalc_stays_in_queue(
    client: TestClient, tokens: TokenFactory, resources: Resources
) -> None:
    enter(client, tokens)
    async with resources.require_database().session() as session, session.begin():
        claim = await Repository(session).claim(NOW, STALE)
        assert claim is not None
        await Repository(session).mark_dirty(FUND, date(2026, 11, 1), NOW + timedelta(seconds=1))
    async with resources.require_database().session() as session, session.begin():
        await Repository(session).finish(claim)

    assert await queue(resources) == {FUND: date(2026, 11, 1)}


async def test_all_trades_cancelled_leaves_no_positions(
    client: TestClient, tokens: TokenFactory, resources: Resources
) -> None:
    trade = enter(client, tokens)
    await run(resources)
    token = tokens.issue(subject="u-ops", username="ops", roles=["operations"])
    client.post(
        f"/trades/{trade['id']}/cancel",
        json={"comment": "Ошибка ввода"},
        headers={"Authorization": f"Bearer {token}"},
    )
    await run(resources)

    async with resources.require_database().session() as session:
        current = await session.scalar(
            select(func.count())
            .select_from(day_snapshot)
            .where(day_snapshot.c.replaced_at.is_(None))
        )
    assert current == 0


async def test_instrument_gone_from_refdata(
    client: TestClient, tokens: TokenFactory, resources: Resources, world: World
) -> None:
    enter(client, tokens)
    del world.instruments[OFZ]

    assert await run(resources) == FUND  # без условий бумаги — без купонов, но позиция есть


async def test_new_day_extends_funds(
    client: TestClient, tokens: TokenFactory, resources: Resources
) -> None:
    enter(client, tokens)
    await run(resources)

    assert await extend_to_today(resources.require_database(), NOW) == 0
    tomorrow = NOW + timedelta(days=1)
    assert await extend_to_today(resources.require_database(), tomorrow) == 1
    # Вчерашний день — заново, по ценам закрытия; дальше — до сегодня.
    assert await queue(resources) == {FUND: date(2026, 12, 3)}


async def test_refdata_events_mark_funds(
    client: TestClient, tokens: TokenFactory, resources: Resources
) -> None:
    enter(client, tokens)
    enter(
        client,
        tokens,
        portfolio_id=str(OTHER_BONDS),
        instrument_id=str(LQDT),
        settle_date="2026-11-27",
    )
    await run(resources)
    await run(resources)
    events = MarketEvents(resources.require_database(), clock=lambda: NOW)

    def event(kind: str, entity: str, **data: Any) -> Event:
        return Event(
            service="refdata",
            type=kind,
            actor=SYSTEM_ACTOR,
            entity_type="x",
            entity_id=entity,
            data=data,
        )

    await events.handle(event("instrument.version_added", str(OFZ), valid_from="2026-12-01"))
    assert await queue(resources) == {FUND: date(2026, 12, 1)}
    await events.handle(event("instrument.version_added", str(OFZ), valid_from=None))
    assert await queue(resources) == {FUND: date(2000, 1, 1)}
    await events.handle(event("calendar.day_set", "2026-11-30"))
    assert await queue(resources) == {FUND: date(2000, 1, 1), OTHER_FUND: date(2026, 11, 30)}
    await events.handle(event("instrument.loaded", str(OFZ)))  # не про условия бумаги
    assert len(await queue(resources)) == 2
    # Сервис цен прислал цены за прошлые дни: пересчёт оценки с этой даты.
    await events.handle(
        event(
            "prices.backfilled",
            "2026-11-20",
            instruments=[{"id": str(LQDT), "secid": "LQDT", "since": "2026-11-20"}],
        )
    )
    assert (await queue(resources))[OTHER_FUND] == date(2026, 11, 20)


async def test_workers(
    client: TestClient,
    tokens: TokenFactory,
    resources: Resources,
    settings: BookingSettings,
    redis_url: str,
) -> None:
    enter(client, tokens)
    quick = settings.model_copy(update={"recalc_seconds": 0.05})
    connection = aioredis.Redis.from_url(redis_url, decode_responses=True)
    live = Resources(
        settings=quick, http=resources.http, database=resources.database, redis=connection
    )
    tasks = [
        asyncio.ensure_future(recalc_worker(quick, clock=lambda: NOW)(live)),
        asyncio.ensure_future(market_consumer(quick)(live)),
    ]
    for _ in range(100):
        if not await queue(resources):
            break
        await asyncio.sleep(0.05)
    assert await queue(resources) == {}

    # Событие справочника через Redis ставит фонд в очередь — воркер его пересчитывает.
    event = Event(
        service="refdata",
        type="instrument.version_added",
        actor=SYSTEM_ACTOR,
        entity_type="instrument",
        entity_id=str(OFZ),
        data={"valid_from": "2026-12-02"},
    )
    await connection.xadd(stream_name("refdata"), {"event": event.model_dump_json()})
    async with resources.require_database().session() as session:
        before = await session.scalar(select(func.count()).select_from(day_snapshot))
    for _ in range(200):
        async with resources.require_database().session() as session:
            after = await session.scalar(select(func.count()).select_from(day_snapshot))
        if after != before:
            break
        await asyncio.sleep(0.05)
    for task in tasks:
        task.cancel()
    await connection.aclose()
    assert before is not None
    assert after == before + 2  # 02.12 и 03.12 пересчитаны заново


async def test_recalc_worker_survives_database_errors(
    settings: BookingSettings, resources: Resources, caplog: pytest.LogCaptureFixture
) -> None:
    broken = Database(
        "postgresql+asyncpg://user:secret@127.0.0.1:1/am", "booking", connect_timeout=0.2
    )
    quick = settings.model_copy(update={"recalc_seconds": 0.05})
    task = asyncio.ensure_future(
        recalc_worker(quick, clock=lambda: NOW)(
            Resources(settings=quick, http=resources.http, database=broken, redis=None)
        )
    )
    await asyncio.sleep(0.5)
    assert not task.done()
    task.cancel()
    await broken.dispose()
    assert "will retry" in caplog.text
