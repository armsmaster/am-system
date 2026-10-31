"""Пересчёт задним числом (design/05, «Ролл и пересчёт задним числом»).

Изменение входов ставит портфель в очередь с даты, начиная с которой его
состояние могло измениться. Воркер забирает портфель (захват — только этот
экземпляр), роллит дни до сегодня и сохраняет снапшоты; прежние снапшоты
остаются замещёнными. Запросы к справочнику — вне транзакции.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from am_common.app import Resources, Worker
from am_common.clock import MOSCOW, moscow_now
from am_common.consumer import StreamConsumer
from am_common.db import Database
from am_common.events import Event, stream_name
from booking.clients import RefdataClient, ServiceUnavailableError, service_token
from booking.repository import Claim, Repository
from booking.roll import InstrumentKind, Side, Trade, from_json, roll_days, start, to_json
from booking.settings import BookingSettings
from booking.terms import InstrumentHistory, TermsBook

logger = logging.getLogger(__name__)

# Пересчёт «с начала истории»: новая версия бумаги без даты начала.
BEGINNING = date(2000, 1, 1)


def refdata_client(resources: Resources) -> RefdataClient:
    settings = cast(BookingSettings, resources.settings)
    return RefdataClient(resources.http, settings.refdata_url, service_token(resources))


async def recalc_once(
    database: Database, refdata: RefdataClient, now: datetime, stale: timedelta
) -> UUID | None:
    """Пересчитывает один портфель из очереди; None — очередь пуста."""
    async with database.session() as session, session.begin():
        claim = await Repository(session).claim(now, stale)
    if claim is None:
        return None
    try:
        await recompute(database, refdata, claim, now)
    except (ServiceUnavailableError, SQLAlchemyError) as exc:
        # Захват снимаем сразу: следующая попытка не ждёт срока захвата.
        logger.warning("recalc of %s failed, will retry: %s", claim.portfolio_id, exc)
        async with database.session() as session, session.begin():
            await Repository(session).release(claim.portfolio_id)
        return None
    async with database.session() as session, session.begin():
        await Repository(session).finish(claim)
    return claim.portfolio_id


async def recompute(
    database: Database, refdata: RefdataClient, claim: Claim, now: datetime
) -> None:
    today = now.astimezone(MOSCOW).date()
    async with database.session() as session:
        repo = Repository(session)
        rows = await repo.current(portfolio_ids=[claim.portfolio_id], include_cancelled=False)
        previous = await repo.snapshot_before(claim.portfolio_id, claim.since)
    trades = [_trade(row) for row in rows]
    if previous is not None:
        state = from_json(previous.state)
    elif trades:
        state = start(min(t.trade_date for t in trades))
    else:
        # Сделок не осталось — прежние снапшоты замещаются пустым запуском.
        state = start(today + timedelta(days=1))
    instruments = {t.instrument_id for t in trades} | set(state.holdings)
    histories: dict[UUID, InstrumentHistory] = {}
    for instrument_id in instruments:
        history = await refdata.history(instrument_id)
        if history is not None:
            histories[instrument_id] = history
    exceptions = await refdata.calendar_around(state.day, today)
    states = roll_days(state, today, trades, TermsBook(histories, exceptions))
    replace_from = state.day + timedelta(days=1) if previous is not None else claim.since
    async with database.session() as session, session.begin():
        await Repository(session).save_run(
            {
                "id": uuid4(),
                "portfolio_id": claim.portfolio_id,
                "since": replace_from,
                "till": today,
                "started_at": now,
                "finished_at": moscow_now(),
            },
            min(replace_from, state.day + timedelta(days=1)),
            [(s.day, to_json(s)) for s in states],
            now,
        )


def _trade(row: Any) -> Trade:
    return Trade(
        id=row.trade_id,
        instrument_id=row.instrument_id,
        kind=InstrumentKind(row.instrument_kind),
        side=Side(row.side),
        quantity=row.quantity,
        price=row.price,
        face_value=row.face_value,
        accrued=row.accrued,
        amount=row.amount,
        fee=row.fee,
        trade_at=row.trade_at,
        trade_date=row.trade_date,
        settle_date=row.settle_date,
    )


async def extend_to_today(database: Database, now: datetime) -> int:
    """Новый день: портфели досчитываются до сегодня — купоны идут по графику и без сделок."""
    today = now.astimezone(MOSCOW).date()
    async with database.session() as session, session.begin():
        repo = Repository(session)
        behind = {p: d for p, d in (await repo.last_days()).items() if d < today}
        for portfolio_id, last in behind.items():
            await repo.mark_dirty(portfolio_id, last + timedelta(days=1), now)
    return len(behind)


class RefdataEvents:
    """События справочника: новая версия бумаги, правка торгового календаря."""

    def __init__(self, database: Database, clock: Callable[[], datetime] = moscow_now) -> None:
        self._database = database
        self._clock = clock

    async def handle(self, event: Event) -> None:
        if event.type == "instrument.version_added":
            valid_from = event.data.get("valid_from")
            since = BEGINNING if valid_from is None else date.fromisoformat(valid_from)
            await self._mark(since, instrument_id=UUID(event.entity_id))
        elif event.type in {"calendar.day_set", "calendar.day_cleared"}:
            await self._mark(date.fromisoformat(event.entity_id))

    async def _mark(self, since: date, instrument_id: UUID | None = None) -> None:
        now = self._clock()
        async with self._database.session() as session, session.begin():
            repo = Repository(session)
            portfolios = (
                await repo.portfolios_with_trades()
                if instrument_id is None
                else await repo.portfolios_holding(instrument_id)
            )
            for portfolio_id in portfolios:
                await repo.mark_dirty(portfolio_id, since, now)


_ERRORS = (SQLAlchemyError, OSError)


def recalc_worker(settings: BookingSettings, clock: Callable[[], datetime] = moscow_now) -> Worker:
    async def recalc(resources: Resources) -> None:
        database = resources.require_database()
        refdata = refdata_client(resources)
        stale = timedelta(seconds=settings.recalc_stale_seconds)
        day: date | None = None
        while True:
            try:
                now = clock()
                if now.astimezone(MOSCOW).date() != day:
                    await extend_to_today(database, now)
                    day = now.astimezone(MOSCOW).date()
                while await recalc_once(database, refdata, clock(), stale):
                    pass
            except _ERRORS as exc:
                logger.warning("recalc failed, will retry: %s", exc)
            await asyncio.sleep(settings.recalc_seconds)

    return recalc


def refdata_consumer(settings: BookingSettings) -> Worker:
    async def refdata_events(resources: Resources) -> None:
        consumer = StreamConsumer(
            resources.require_redis(),
            streams=[stream_name("refdata")],
            group=settings.consumer_group,
            consumer=settings.consumer_name,
            handler=RefdataEvents(resources.require_database()).handle,
        )
        await consumer.run()

    return refdata_events
