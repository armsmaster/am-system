"""Ежедневная синхронизация: когда запускается, кто её выполняет, что считает."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from sqlalchemy import select

from am_common.app import Resources
from am_common.clock import MOSCOW
from am_common.db import Database
from am_common.events import Actor
from refdata.commands import Commands
from refdata.db import sync_run
from refdata.iss import IssClient
from refdata.repository import Repository
from refdata.scheduler import daily_sync_worker, run_daily_sync_if_due
from refdata.settings import RefdataSettings

if TYPE_CHECKING:
    from conftest import FakeIss

pytestmark = pytest.mark.integration

MORNING = datetime(2026, 10, 26, 7, 0, tzinfo=MOSCOW)
NIGHT = datetime(2026, 10, 26, 5, 0, tzinfo=MOSCOW)
OPS = Actor(subject="u-ops", username="ops")


@pytest.fixture
async def resources(settings: RefdataSettings, iss: FakeIss) -> AsyncIterator[Resources]:
    assert settings.database_url is not None
    assert settings.database_schema is not None
    database = Database(settings.database_url.get_secret_value(), settings.database_schema)
    async with httpx2.AsyncClient(transport=iss.transport()) as http:
        yield Resources(settings=settings, http=http, database=database, redis=None)
    await database.dispose()


async def load(resources: Resources, *codes: str) -> None:
    iss = IssClient(resources.http, "http://iss.test/iss")
    for code in codes:
        async with resources.require_database().session() as session:
            await Commands(session, OPS, clock=lambda: MORNING).load(code, iss)


async def runs(resources: Resources) -> list[tuple[int, int, int]]:
    async with resources.require_database().session() as session:
        rows = await session.execute(
            select(sync_run.c.checked, sync_run.c.changed, sync_run.c.failed)
        )
        return [(row.checked, row.changed, row.failed) for row in rows]


async def test_runs_once_a_day_after_the_set_time(
    resources: Resources, settings: RefdataSettings, iss: FakeIss
) -> None:
    await load(resources, "SU26238RMFS4", "LQDT")
    iss.describe("SU26238RMFS4", "MATDATE", "2041-06-15")

    assert await run_daily_sync_if_due(resources, settings, NIGHT) is False
    assert await run_daily_sync_if_due(resources, settings, MORNING) is True
    assert await run_daily_sync_if_due(resources, settings, MORNING + timedelta(hours=2)) is False

    assert await runs(resources) == [(2, 1, 0)]
    async with resources.require_database().session() as session:
        record = await Repository(session).find_by_code("SU26238RMFS4")
        assert record is not None
        latest = await Repository(session).latest_version(record.id)
    # Версия от синхронизации начинает действовать в день синхронизации.
    assert (latest.number, latest.valid_from, latest.author) == (2, MORNING.date(), "moex-iss")


async def test_counts_failures_and_takes_over_a_stuck_run(
    resources: Resources, settings: RefdataSettings, iss: FakeIss
) -> None:
    await load(resources, "SU26238RMFS4")
    async with resources.require_database().session() as session, session.begin():
        # Экземпляр взял запуск и упал, не закончив его.
        await Repository(session).claim_sync_run(MORNING.date(), MORNING, timedelta(hours=1))
    iss.down = True

    assert (
        await run_daily_sync_if_due(resources, settings, MORNING + timedelta(minutes=30)) is False
    )
    assert await run_daily_sync_if_due(resources, settings, MORNING + timedelta(hours=2)) is True
    assert await runs(resources) == [(1, 0, 1)]


async def test_retries_a_run_with_failures_an_hour_later(
    resources: Resources, settings: RefdataSettings, iss: FakeIss
) -> None:
    await load(resources, "SU26238RMFS4")
    iss.down = True
    assert await run_daily_sync_if_due(resources, settings, MORNING) is True
    assert await runs(resources) == [(1, 0, 1)]

    iss.down = False
    iss.describe("SU26238RMFS4", "MATDATE", "2041-06-15")
    assert (
        await run_daily_sync_if_due(resources, settings, MORNING + timedelta(minutes=30)) is False
    )
    assert await run_daily_sync_if_due(resources, settings, MORNING + timedelta(hours=2)) is True
    assert await runs(resources) == [(1, 1, 0)]
    # Всё прошло — больше в этот день не повторяем.
    assert await run_daily_sync_if_due(resources, settings, MORNING + timedelta(hours=4)) is False


def reprice(face: str, coupon: str) -> Callable[[dict[str, Any]], None]:
    """Ответ ISS по линкеру в другой день: номинал вырос, суммы пересчитаны на него."""

    def patch(payload: dict[str, Any]) -> None:
        for block, values in (
            ("coupons", {"facevalue": face, "value": coupon}),
            ("amortizations", {"facevalue": face, "value": face}),
        ):
            columns = payload[block]["columns"]
            for row in payload[block]["data"]:
                for name, value in values.items():
                    row[columns.index(name)] = float(value)

    return patch


async def test_linker_gets_a_version_on_payment_day_not_every_day(
    resources: Resources, settings: RefdataSettings, iss: FakeIss
) -> None:
    # Загружен 26.10, номинал 1065,10. Первый купон — 19.11.
    await load(resources, "RU000A10F504")

    iss.patches[("bond", "RU000A10F504")] = reprice("1065.5", coupon="10.2")
    assert await run_daily_sync_if_due(resources, settings, MORNING + timedelta(days=1)) is True
    assert await runs(resources) == [(1, 0, 0)]

    pay_day = datetime(2026, 11, 19, 7, 0, tzinfo=MOSCOW)
    iss.patches[("bond", "RU000A10F504")] = reprice("1074.3", coupon="10.31")
    assert await run_daily_sync_if_due(resources, settings, pay_day) is True
    async with resources.require_database().session() as session:
        record = await Repository(session).find_by_code("RU000A10F504")
        assert record is not None
        latest = await Repository(session).latest_version(record.id)
    # Выплаченный купон стал фактом; следующие суммы по-прежнему неизвестны.
    assert (latest.number, latest.valid_from) == (2, pay_day.date())
    assert [c.amount for c in latest.params.coupons[:2]] == [Decimal("10.31"), None]
    assert latest.params.amortizations[0].amount is None


async def test_worker_keeps_running_after_errors(
    settings: RefdataSettings, caplog: pytest.LogCaptureFixture
) -> None:
    broken = Database(
        "postgresql+asyncpg://user:secret@127.0.0.1:1/am", "refdata", connect_timeout=0.2
    )
    async with httpx2.AsyncClient() as http:
        resources = Resources(settings=settings, http=http, database=broken, redis=None)
        quick = settings.model_copy(update={"sync_check_seconds": 0.05})
        task = asyncio.ensure_future(daily_sync_worker(quick, clock=lambda: MORNING)(resources))
        await asyncio.sleep(1)

        assert not task.done()
        task.cancel()
    await broken.dispose()
    assert "daily ISS sync failed" in caplog.text


async def test_concurrent_load_returns_the_existing_bond(
    resources: Resources, monkeypatch: pytest.MonkeyPatch
) -> None:
    await load(resources, "SU26238RMFS4")
    original = Repository.find_by_code
    calls = 0

    async def not_yet_visible(self: Repository, code: str) -> object:
        nonlocal calls
        calls += 1
        # Первая проверка «не видит» бумагу — будто её добавили, пока шёл запрос к ISS.
        return None if calls == 1 else await original(self, code)

    monkeypatch.setattr(Repository, "find_by_code", not_yet_visible)
    iss = IssClient(resources.http, "http://iss.test/iss")
    async with resources.require_database().session() as session:
        loaded = await Commands(session, OPS).load("SU26238RMFS4", iss)

    assert loaded.created is False
    assert calls == 2
