"""Фоновые задачи: список бумаг из справочника, история и ставки ISS, данные дня ISS,
часовые свечи ISS, RUONIA из веб-сервиса Банка России.

Каждая задача раз в несколько минут проверяет, есть ли работа. При нескольких
экземплярах сервиса работу забирает один: загрузку истории — по бумаге
(захват в ``instrument``), опрос данных дня — по 5-минутному слоту (``iss_poll``),
свечи — по 15-минутному (``candle_poll``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
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
# Историю ставок догружаем назад, если она начинается позже нужного больше чем на неделю:
# в выходные и праздники ставок нет, и первая дата бывает на пару дней позже начала.
RATES_SLACK = timedelta(days=7)
# Часовые свечи ISS (решение 153): открытые данные — с задержкой 15 минут;
# последнюю вечернюю свечу забираем в первый час следующего дня.
CANDLE_MINUTES = 60
CANDLE = timedelta(minutes=CANDLE_MINUTES)
ISS_DELAY = timedelta(minutes=15)
AFTER_SESSION = time(1, 0)
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


class RatesLoader:
    """Ключевая ставка и RUSFAR из индексов ISS: до последнего торгового дня и назад —
    на 5 лет (решение 157).

    Захват не нужен: ставка на дату записывается поверх, повторная загрузка безвредна.
    Историю назад спрашиваем раз за жизнь экземпляра: если у ISS нет таких старых
    значений, иначе спрашивали бы их на каждом цикле.
    """

    def __init__(self, iss: IssClient) -> None:
        self._iss = iss
        self._backfilled: set[RateKind] = set()

    async def load_due(self, resources: Resources, refdata: RefdataClient, now: datetime) -> int:
        settings = cast(MarketdataSettings, resources.settings)
        database = resources.require_database()
        target = await history_target(refdata, settings, now)
        start = now.date() - timedelta(days=settings.rates_history_days)
        loaded = 0
        for kind, secid in ISS_INDICES.items():
            async with database.session() as session:
                repo = Repository(session)
                first, last = (
                    await repo.first_rate_date(kind, ISS),
                    await repo.last_rate_date(kind, ISS),
                )
            backfill = kind not in self._backfilled
            periods = _rate_periods(start, first, last, target, backfill=backfill)
            if not periods:
                continue
            try:
                values = [
                    v
                    for since, till in periods
                    for v in await self._iss.index_history(secid, since, till)
                ]
            except IssError as exc:
                logger.warning("ISS index %s failed, will retry: %s", secid, exc)
                continue
            self._backfilled.add(kind)
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


def _rate_periods(
    start: date, first: date | None, last: date | None, target: date, *, backfill: bool
) -> list[tuple[date, date]]:
    """Что догрузить: всё с ``start``, если ставок нет; иначе — назад до ``start``
    (если ``backfill``) и вперёд до ``target``."""
    if first is None or last is None:
        return [(start, target)]
    periods: list[tuple[date, date]] = []
    if backfill and first - start > RATES_SLACK:
        periods.append((start, first - timedelta(days=1)))
    if last < target:
        periods.append((last + timedelta(days=1), target))
    return periods


class CbrRateLoader:
    """Ставка из веб-сервиса Банка России до последнего опубликованного дня: RUONIA
    (решение 148) и ключевая ставка — её история для базиса (решение 157).

    Пока ставки за ожидаемый день нет, ЦБ спрашиваем не чаще ``ruonia_retry_seconds``:
    иначе в торговый день, когда ЦБ не работает, задача спрашивала бы каждые 5 минут.
    Время попытки — в памяти экземпляра: несколько экземпляров спросят по разу, это
    безвредно — ставка на дату записывается поверх. Историю назад, на 5 лет
    (решение 157), — тоже раз за жизнь экземпляра.
    """

    def __init__(self, cbr: CbrClient, kind: RateKind) -> None:
        self._cbr = cbr
        self._kind = kind
        self._tried: datetime | None = None
        self._backfilled = False

    async def load_due(self, resources: Resources, refdata: RefdataClient, now: datetime) -> int:
        settings = cast(MarketdataSettings, resources.settings)
        database = resources.require_database()
        target = await published_through(refdata, now, settings.ruonia_at)
        start = now.date() - timedelta(days=settings.rates_history_days)
        async with database.session() as session:
            repo = Repository(session)
            first = await repo.first_rate_date(self._kind, CBR)
            last = await repo.last_rate_date(self._kind, CBR)
        # История за 5 лет — для базиса RUONIA к ключевой ставке (решение 157).
        backfill = not self._backfilled and first is not None and first - start > RATES_SLACK
        if last is not None and last >= target and not backfill:
            return 0
        retry = timedelta(seconds=settings.ruonia_retry_seconds)
        if self._tried is not None and now - self._tried < retry:
            return 0
        self._tried = now
        # Последние дни — заново: ЦБ мог исправить ставку задним числом.
        since = (
            start
            if last is None or backfill
            else last - timedelta(days=settings.ruonia_refresh_days)
        )
        try:
            values = await self._cbr.rates(self._kind, since, target)
        except CbrError as exc:
            logger.warning("CBR %s failed, will retry: %s", self._kind.value, exc)
            return 0
        self._backfilled = True
        received = moscow_now()
        async with database.session() as session, session.begin():
            await Repository(session).upsert_rates(
                [
                    {
                        "kind": self._kind.value,
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
        polled, failed = await poll_intraday(database, iss, records, now.date()), 0
    except IssError as exc:
        logger.warning("ISS intraday poll failed: %s", exc)
        polled, failed = 0, 1
    async with database.session() as session, session.begin():
        await Repository(session).finish_poll(slot, moscow_now(), polled, failed)
    return True


def live(records: Sequence[InstrumentRecord], today: date) -> list[InstrumentRecord]:
    """Живые бумаги (решение 153): облигация ещё не погашена; паи — все."""
    return [
        r
        for r in records
        if r.kind != "bond" or r.maturity_date is None or r.maturity_date >= today
    ]


async def load_candles_if_due(
    resources: Resources, refdata: RefdataClient, iss: IssClient, now: datetime
) -> bool:
    """Догружает часовые свечи ISS по живым бумагам, если этот слот ещё никто не взял.

    В торговый день (и в первый час после него — за последнюю вечернюю свечу) —
    по всем живым бумагам; в остальные дни — только по новым, у которых свечей нет.
    """
    settings = cast(MarketdataSettings, resources.settings)
    step = max(1, int(settings.candles_seconds // 60))
    slot = now.replace(minute=now.minute - now.minute % step, second=0, microsecond=0)
    exceptions = await calendar_around(refdata, now.date())
    trading = is_trading_day(now.date(), exceptions) or (
        now.time() < AFTER_SESSION and is_trading_day(now.date() - timedelta(days=1), exceptions)
    )
    database = resources.require_database()
    async with database.session() as session, session.begin():
        repo = Repository(session)
        if not await repo.claim_candle_poll(slot):
            return False
        records = live(await _with_board(repo), now.date())
    loaded = failed = 0
    for record in records:
        async with database.session() as session:
            last = await Repository(session).last_candle(record.id, CANDLE_MINUTES)
        if last is not None and not trading:
            continue
        since = last.date() if last else now.date() - timedelta(days=settings.candles_backfill_days)
        try:
            candles = await iss.candles(record.secid, record.kind, cast(str, record.board), since)
        except IssError as exc:
            logger.warning("ISS candles for %s failed: %s", record.secid, exc)
            failed += 1
            continue
        # Закрытая свеча — закончилась не меньше 15 минут назад: открытые данные ISS с задержкой.
        closed = [c for c in candles if c.start + CANDLE + ISS_DELAY <= now]
        async with database.session() as session, session.begin():
            loaded += await Repository(session).add_candles(
                record.id, CANDLE_MINUTES, closed, moscow_now()
            )
    async with database.session() as session, session.begin():
        await Repository(session).finish_candle_poll(slot, moscow_now(), loaded, failed)
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
        cbr, rates = cbr_client(resources), RatesLoader(iss)
        ruonia = CbrRateLoader(cbr, RateKind.RUONIA)
        key_rate = CbrRateLoader(cbr, RateKind.KEY_RATE)
        while True:
            try:
                await load_due_history(resources, refdata, iss, clock())
                await rates.load_due(resources, refdata, clock())
                await ruonia.load_due(resources, refdata, clock())
                await key_rate.load_due(resources, refdata, clock())
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


def candles_worker(
    settings: MarketdataSettings, clock: Callable[[], datetime] = moscow_now
) -> Worker:
    async def iss_candles(resources: Resources) -> None:
        refdata, iss = refdata_client(resources), iss_client(resources)
        while True:
            try:
                await load_candles_if_due(resources, refdata, iss, clock())
            except _ERRORS as exc:
                logger.warning("ISS candles load failed, will retry: %s", exc)
            # Проверяем чаще слота, чтобы не пропустить его начало.
            await asyncio.sleep(min(settings.candles_seconds, 60.0))

    return iss_candles
