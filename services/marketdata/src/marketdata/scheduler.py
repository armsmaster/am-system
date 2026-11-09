"""Фоновые задачи: список бумаг из справочника, история и ставки ISS, данные дня ISS,
RUONIA из веб-сервиса Банка России.

Каждая задача раз в несколько минут проверяет, есть ли работа. При нескольких
экземплярах сервиса работу забирает один: загрузку истории — по бумаге
(захват в ``instrument``), опрос данных дня — по 5-минутному слоту (``iss_poll``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from am_common.app import Resources, Worker
from am_common.calendar import is_trading_day, previous_trading_day
from am_common.clock import MOSCOW, moscow_now
from am_common.events import SYSTEM_ACTOR
from marketdata.backfill import announce, past_days
from marketdata.cbr import CbrClient, CbrError
from marketdata.commands import load_history, poll_intraday, refresh_instruments
from marketdata.iss import IssClient, IssError
from marketdata.queries import calendar_around
from marketdata.rates import CBR, ISS, ISS_INDICES, RateKind
from marketdata.refdata import RefdataClient, RefdataUnavailableError, refdata_client
from marketdata.repository import InstrumentRecord, Repository
from marketdata.settings import MarketdataSettings

logger = logging.getLogger(__name__)

# Загрузку истории, начатую упавшим экземпляром, через полчаса заберёт другой.
STALE_AFTER = timedelta(minutes=30)
_ERRORS = (SQLAlchemyError, OSError, RefdataUnavailableError)


def iss_client(resources: Resources) -> IssClient:
    settings = cast(MarketdataSettings, resources.settings)
    return IssClient(resources.http, settings.iss_url, timeout=settings.iss_timeout)


def cbr_client(resources: Resources) -> CbrClient:
    settings = cast(MarketdataSettings, resources.settings)
    return CbrClient(resources.http, settings.cbr_url, timeout=settings.cbr_timeout)


async def published_through(refdata: RefdataClient, now: datetime, at: time) -> date:
    """Последний торговый день, данные за который источник публикует назавтра к ``at``."""
    exceptions = await calendar_around(refdata, now.date())
    last = previous_trading_day(now.date() - timedelta(days=1), exceptions)
    if now.time() < at:
        last = previous_trading_day(last - timedelta(days=1), exceptions)
    return last


async def history_target(
    refdata: RefdataClient, settings: MarketdataSettings, now: datetime
) -> date:
    """Последний торговый день, итоги которого ISS уже опубликовал."""
    return await published_through(refdata, now, settings.history_at)


async def load_due_history(
    resources: Resources, refdata: RefdataClient, iss: IssClient, now: datetime
) -> int:
    """Догружает историю ISS по бумагам, у которых её нет до последнего торгового дня.

    Итоги прошлых дней меняют оценку позиций — одно событие на загрузку.
    """
    settings = cast(MarketdataSettings, resources.settings)
    database = resources.require_database()
    target = await history_target(refdata, settings, now)
    async with database.session() as session:
        records = await _with_board(Repository(session))
    loaded = 0
    first_days: list[tuple[UUID, date]] = []
    for record in records:
        since = (
            record.history_till + timedelta(days=1)
            if record.history_till
            else settings.history_from
        )
        if since > target:
            continue
        async with database.session() as session, session.begin():
            if not await Repository(session).claim_history(record.id, now, STALE_AFTER):
                continue
        try:
            first = await load_history(database, iss, record, since, target)
        except (IssError, SQLAlchemyError) as exc:
            # Захват снимаем сразу: иначе следующая попытка ждала бы полчаса.
            logger.warning("ISS history for %s failed, will retry: %s", record.secid, exc)
            async with database.session() as session, session.begin():
                await Repository(session).finish_history(record.id, None)
            continue
        if first is not None:
            first_days.append((record.id, first))
        loaded += 1
    earliest = past_days(first_days, now.astimezone(MOSCOW).date())
    if earliest:
        async with database.session() as session, session.begin():
            await announce(session, earliest, {r.id: r.secid for r in records}, SYSTEM_ACTOR)
    return loaded


async def load_due_rates(
    resources: Resources, refdata: RefdataClient, iss: IssClient, now: datetime
) -> int:
    """Догружает ключевую ставку и RUSFAR из индексов ISS до последнего торгового дня.

    Захват не нужен: ставка на дату записывается поверх, повторная загрузка безвредна.
    """
    settings = cast(MarketdataSettings, resources.settings)
    database = resources.require_database()
    target = await history_target(refdata, settings, now)
    loaded = 0
    for kind, secid in ISS_INDICES.items():
        async with database.session() as session:
            last = await Repository(session).last_rate_date(kind, ISS)
        since = last + timedelta(days=1) if last else settings.history_from
        if since > target:
            continue
        try:
            values = await iss.index_history(secid, since, target)
        except IssError as exc:
            logger.warning("ISS index %s failed, will retry: %s", secid, exc)
            continue
        received = moscow_now()
        async with database.session() as session, session.begin():
            await Repository(session).upsert_rates(
                [
                    {
                        "kind": kind.value,
                        "as_of": day,
                        "source": ISS,
                        "value": value,
                        "received_at": received,
                        "batch_id": None,
                    }
                    for day, value in values
                ]
            )
        loaded += len(values)
    return loaded


class RuoniaLoader:
    """RUONIA из веб-сервиса Банка России до последнего опубликованного дня (решение 148).

    Пока ставки за ожидаемый день нет, ЦБ спрашиваем не чаще ``ruonia_retry_seconds``:
    иначе в торговый день, когда ЦБ не работает, задача спрашивала бы каждые 5 минут.
    Время попытки — в памяти экземпляра: несколько экземпляров спросят по разу, это
    безвредно — ставка на дату записывается поверх.
    """

    def __init__(self, cbr: CbrClient) -> None:
        self._cbr = cbr
        self._tried: datetime | None = None

    async def load_due(self, resources: Resources, refdata: RefdataClient, now: datetime) -> int:
        settings = cast(MarketdataSettings, resources.settings)
        database = resources.require_database()
        target = await published_through(refdata, now, settings.ruonia_at)
        async with database.session() as session:
            last = await Repository(session).last_rate_date(RateKind.RUONIA, CBR)
        if last is not None and last >= target:
            return 0
        retry = timedelta(seconds=settings.ruonia_retry_seconds)
        if self._tried is not None and now - self._tried < retry:
            return 0
        self._tried = now
        # Последние дни — заново: ЦБ мог исправить ставку задним числом.
        since = (
            last - timedelta(days=settings.ruonia_refresh_days) if last else settings.history_from
        )
        try:
            values = await self._cbr.ruonia(since, target)
        except CbrError as exc:
            logger.warning("CBR RUONIA failed, will retry: %s", exc)
            return 0
        received = moscow_now()
        async with database.session() as session, session.begin():
            await Repository(session).upsert_rates(
                [
                    {
                        "kind": RateKind.RUONIA.value,
                        "as_of": day,
                        "source": CBR,
                        "value": value,
                        "received_at": received,
                        "batch_id": None,
                    }
                    for day, value in values
                ]
            )
        return len(values)


async def poll_if_due(
    resources: Resources, refdata: RefdataClient, iss: IssClient, now: datetime
) -> bool:
    """Опрашивает данные дня ISS, если идут торги и этот слот ещё никто не взял."""
    settings = cast(MarketdataSettings, resources.settings)
    if not settings.intraday_from <= now.time() <= settings.intraday_till:
        return False
    if not is_trading_day(now.date(), await calendar_around(refdata, now.date())):
        return False
    step = max(1, int(settings.intraday_seconds // 60))
    slot = now.replace(minute=now.minute - now.minute % step, second=0, microsecond=0)
    database = resources.require_database()
    async with database.session() as session, session.begin():
        repo = Repository(session)
        if not await repo.claim_poll(slot):
            return False
        records = await _with_board(repo)
    try:
        polled, failed = await poll_intraday(database, iss, records), 0
    except IssError as exc:
        logger.warning("ISS intraday poll failed: %s", exc)
        polled, failed = 0, 1
    async with database.session() as session, session.begin():
        await Repository(session).finish_poll(slot, moscow_now(), polled, failed)
    return True


async def _with_board(repo: Repository) -> list[InstrumentRecord]:
    """Бумаги с известным режимом торгов. Пока режима нет, ISS отдал бы строки всех
    режимов сразу (TQOB и SPOB) — такие бумаги ждут следующей сверки со справочником."""
    return [r for r in await repo.instruments() if r.board is not None]


def instruments_worker(settings: MarketdataSettings) -> Worker:
    async def instrument_list(resources: Resources) -> None:
        refdata = refdata_client(resources)
        while True:
            try:
                await refresh_instruments(resources.require_database(), refdata)
            except _ERRORS as exc:
                logger.warning("instrument list sync failed, will retry: %s", exc)
            await asyncio.sleep(settings.refdata_sync_seconds)

    return instrument_list


def history_worker(
    settings: MarketdataSettings, clock: Callable[[], datetime] = moscow_now
) -> Worker:
    async def iss_history(resources: Resources) -> None:
        refdata, iss = refdata_client(resources), iss_client(resources)
        ruonia = RuoniaLoader(cbr_client(resources))
        while True:
            try:
                await load_due_history(resources, refdata, iss, clock())
                await load_due_rates(resources, refdata, iss, clock())
                await ruonia.load_due(resources, refdata, clock())
            except _ERRORS as exc:
                logger.warning("ISS history load failed, will retry: %s", exc)
            await asyncio.sleep(settings.history_check_seconds)

    return iss_history


def intraday_worker(
    settings: MarketdataSettings, clock: Callable[[], datetime] = moscow_now
) -> Worker:
    async def iss_intraday(resources: Resources) -> None:
        refdata, iss = refdata_client(resources), iss_client(resources)
        while True:
            try:
                await poll_if_due(resources, refdata, iss, clock())
            except _ERRORS as exc:
                logger.warning("ISS intraday poll failed, will retry: %s", exc)
            # Проверяем чаще слота, чтобы не пропустить его начало.
            await asyncio.sleep(min(settings.intraday_seconds, 60.0))

    return iss_intraday
