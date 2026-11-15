"""Чтение рыночных данных: цена для оценки на момент, ряды для графика, НКД и номинал,
отметки для оценки позиций по дням."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from am_common.calendar import add_trading_days, previous_trading_day
from am_common.clock import MOSCOW
from marketdata.accrual import CouponPeriod, OnDate, accrued_on, face_value_on
from marketdata.db import accrued_interest, face_value
from marketdata.dema import (
    DEFAULT_HALF_LIFE,
    DEFAULT_TIMEFRAME,
    Bar,
    DemaPoint,
    HalfLife,
    dema,
    window,
)
from marketdata.prices import (
    DEFAULT_RULE,
    Choice,
    ManualPrice,
    Observation,
    PriceType,
    Rule,
    choose_price,
    latest,
    moscow_date,
    spread_bp,
)
from marketdata.refdata import RefdataClient, RefdataUnavailableError
from marketdata.repository import ISS_BARS, InstrumentRecord, Repository, RuleRecord

logger = logging.getLogger(__name__)

BARS = "bars"  # источник DEMA по барам скрипта; по свечам ISS — источник iss


@dataclass(frozen=True, slots=True)
class Candidate:
    type: PriceType
    source: str
    value: Decimal
    time: datetime
    today: bool  # цена за текущий торговый день


@dataclass(frozen=True, slots=True)
class PriceView:
    instrument: InstrumentRecord
    at: datetime
    trading_day: date
    price: Choice | None
    candidates: list[Candidate]
    rule: Rule
    spread_bp: Decimal | None

    @property
    def wide_spread(self) -> bool:
        return self.spread_bp is not None and self.spread_bp > self.rule.spread_threshold_bp


@dataclass(frozen=True, slots=True)
class SeriesView:
    instrument: InstrumentRecord
    quotes: list[Observation]
    bars: dict[int, list[Bar]]
    dema: list[DemaPoint]
    manual: list[ManualPrice]
    rules: list[RuleRecord]


async def calendar_around(refdata: RefdataClient, day: date) -> dict[date, bool]:
    """Исключения календаря за год даты и за прошлый; без справочника — только пн–пт."""
    try:
        exceptions = await refdata.calendar(day.year)
        # В начале января последний торговый день может быть в прошлом году.
        return exceptions | await refdata.calendar(day.year - 1)
    except RefdataUnavailableError as exc:
        logger.warning("trading calendar unavailable, using weekdays: %s", exc)
        return {}


async def trading_day_of(refdata: RefdataClient, at: datetime) -> date:
    """Торговый день момента: в субботу — пятница."""
    day = moscow_date(at)
    return previous_trading_day(day, await calendar_around(refdata, day))


async def price_views(
    session: AsyncSession,
    refdata: RefdataClient,
    at: datetime,
    instrument_ids: Sequence[UUID] | None = None,
) -> list[PriceView]:
    """Цена для оценки на момент ``at`` и все цены-кандидаты по бумагам."""
    repo = Repository(session)
    records = (
        [await repo.get(i) for i in instrument_ids]
        if instrument_ids is not None
        else await repo.instruments()
    )
    ids = [r.id for r in records]
    quotes = await repo.latest_quotes(ids, at)
    rules = await repo.rules(ids)
    manual = await repo.latest_manual(ids, at)
    day = await trading_day_of(refdata, at)

    views: list[PriceView] = []
    for record in records:
        history = rules.get(record.id, [])
        rule = rule_at(history, at)
        found = list(quotes.get(record.id, []))
        smoothed, source = await _dema(repo, record.id, history, rule, at)
        if smoothed:
            last = smoothed[-1]
            found.append(Observation(PriceType.DEMA, source, last.time, last.value))
        by_type = {
            kind: chosen
            for kind in PriceType
            if (chosen := latest(o for o in found if o.type is kind)) is not None
        }
        choice = choose_price(
            at=at, rule=rule, latest_by_type=by_type, manual=manual.get(record.id), trading_day=day
        )
        order = list(PriceType)
        candidates = [
            Candidate(o.type, o.source, o.value, o.time, moscow_date(o.time) >= day)
            for o in sorted(found, key=lambda o: (order.index(o.type), o.source))
        ]
        today = [c.value for c in candidates if c.today]
        views.append(
            PriceView(
                instrument=record,
                at=at,
                trading_day=day,
                price=choice,
                candidates=candidates,
                rule=rule,
                spread_bp=None if choice is None else spread_bp(today, choice.value),
            )
        )
    return views


async def series(
    session: AsyncSession, instrument_id: UUID, since: datetime, till: datetime
) -> SeriesView:
    """Всё для графика за период: цены, бары всех таймфреймов, DEMA, ручные цены, правила."""
    repo = Repository(session)
    record = await repo.get(instrument_id)
    history = (await repo.rules([instrument_id])).get(instrument_id, [])
    rule = rule_at(history, till)
    # Свечи графика — того же источника, что DEMA: скрипта или ISS (решение 155).
    bars = {
        timeframe: await repo.bars(
            instrument_id,
            timeframe,
            ending_by=till,
            starting_from=since,
            iss=await repo.prefers_iss_bars(instrument_id, timeframe, till),
        )
        for timeframe in await repo.bar_timeframes(instrument_id)
    }
    smoothed, _source = await _dema(repo, instrument_id, history, rule, till, since=since)
    smoothed = [p for p in smoothed if p.time >= since]
    return SeriesView(
        instrument=record,
        quotes=await repo.quotes_between(instrument_id, since, till),
        bars=bars,
        dema=smoothed,
        manual=await repo.manual_between(instrument_id, since, till),
        rules=history,
    )


@dataclass(frozen=True, slots=True)
class Mark:
    """Всё для оценки бумаги на конец дня (design/06)."""

    day: date
    price: Choice | None  # цена для оценки на конец дня
    settle_date: date  # T+1: расчёты по сделке, заключённой в этот день
    accrued: OnDate | None  # облигация: НКД на дату расчётов T+1
    face: OnDate | None  # облигация: номинал на дату расчётов T+1 (решение 149)


def end_of_day(day: date) -> datetime:
    return datetime.combine(day, time.max, tzinfo=MOSCOW)


async def marks(
    session: AsyncSession, refdata: RefdataClient, instrument_id: UUID, first: date, last: date
) -> list[Mark]:
    """Отметки на каждый календарный день: цена на 23:59:59 МСК по правилу бумаги,
    НКД и номинал на дату расчётов T+1 (решения 102 и 149)."""
    repo = Repository(session)
    record = await repo.get(instrument_id)
    bond = record.kind == "bond"
    accrued_points = await repo.points(accrued_interest, instrument_id) if bond else {}
    face_points = await repo.points(face_value, instrument_id) if bond else {}
    exceptions: dict[date, bool] = {}
    for year in range(first.year, last.year + 2):
        exceptions |= await calendar_around(refdata, date(year, 1, 1))
    found: list[Mark] = []
    day = first
    while day <= last:
        [view] = await price_views(session, refdata, end_of_day(day), [instrument_id])
        settle = add_trading_days(day + timedelta(days=1), 0, exceptions)
        accrued = face = None
        if bond:
            periods = (
                []
                if settle in accrued_points
                else await refdata.coupon_periods(instrument_id, settle)
            )
            accrued = accrued_on(settle, accrued_points, periods)
            face = face_value_on(settle, face_points)
        found.append(Mark(day, view.price, settle, accrued, face))
        day += timedelta(days=1)
    return found


@dataclass(frozen=True, slots=True)
class Reference:
    """Цена для оценки на конец торгового дня — с ней сравнивают текущую."""

    day: date
    price: Choice | None


@dataclass(frozen=True, slots=True)
class Overview:
    """Строка обзора цен (решение 152): цена на момент, с чем её сравнить, номинал и НКД."""

    view: PriceView
    day_ago: Reference  # конец прошлого торгового дня
    week_ago: Reference  # конец торгового дня неделей раньше
    settle_date: date  # T+1 торгового дня момента
    accrued: OnDate | None  # облигация: НКД на дату расчётов
    face: OnDate | None  # облигация: номинал на дату расчётов


async def overview_rows(
    session: AsyncSession,
    refdata: RefdataClient,
    at: datetime,
    instrument_ids: Sequence[UUID] | None = None,
) -> list[Overview]:
    """Цены на момент и всё, что к ним показывает обзор (решение 152).

    Изменение считают к цене для оценки на 23:59:59 МСК прошлого торгового дня и торгового
    дня неделей раньше. Номинал и НКД — на дату расчётов T+1 торгового дня момента, как в
    отметках для оценки позиций (решения 102 и 149).
    """
    views = await price_views(session, refdata, at, instrument_ids)
    if not views:
        return []
    day = views[0].trading_day
    # Неделя назад может уйти в прошлый год, дата расчётов — в следующий.
    exceptions = await calendar_around(refdata, day)
    exceptions |= await calendar_around(refdata, day + timedelta(days=15))
    previous = previous_trading_day(day - timedelta(days=1), exceptions)
    week = previous_trading_day(day - timedelta(days=7), exceptions)
    ids = [v.instrument.id for v in views]
    day_ago = {
        v.instrument.id: v.price
        for v in await price_views(session, refdata, end_of_day(previous), ids)
    }
    week_ago = {
        v.instrument.id: v.price for v in await price_views(session, refdata, end_of_day(week), ids)
    }
    settle = add_trading_days(day, 1, exceptions)
    repo = Repository(session)
    found: list[Overview] = []
    for view in views:
        record = view.instrument
        accrued = face = None
        if record.kind == "bond":
            accrued_points = await repo.points(accrued_interest, record.id)
            periods = [] if settle in accrued_points else await _periods(refdata, record.id, settle)
            accrued = accrued_on(settle, accrued_points, periods)
            face = face_value_on(settle, await repo.points(face_value, record.id))
        found.append(
            Overview(
                view=view,
                day_ago=Reference(previous, day_ago[record.id]),
                week_ago=Reference(week, week_ago[record.id]),
                settle_date=settle,
                accrued=accrued,
                face=face,
            )
        )
    return found


async def _periods(refdata: RefdataClient, instrument_id: UUID, day: date) -> list[CouponPeriod]:
    """Купонные периоды для досчёта НКД; без справочника — без досчёта: обзор важнее."""
    try:
        return await refdata.coupon_periods(instrument_id, day)
    except RefdataUnavailableError as exc:
        logger.warning("refdata unavailable, accrued interest not estimated: %s", exc)
        return []


async def accrued_on_date(
    session: AsyncSession, refdata: RefdataClient, instrument_id: UUID, day: date
) -> OnDate | None:
    repo = Repository(session)
    await repo.get(instrument_id)
    points = await repo.points(accrued_interest, instrument_id)
    if day in points:
        return accrued_on(day, points, [])
    return accrued_on(day, points, await refdata.coupon_periods(instrument_id, day))


async def face_value_on_date(
    session: AsyncSession, instrument_id: UUID, day: date
) -> OnDate | None:
    repo = Repository(session)
    await repo.get(instrument_id)
    return face_value_on(day, await repo.points(face_value, instrument_id))


def rule_at(history: Sequence[RuleRecord], at: datetime) -> Rule:
    """Правило, действовавшее в момент ``at``; до первого изменения — правило по умолчанию."""
    current = DEFAULT_RULE
    for record in history:
        if record.valid_from > at:
            break
        current = record.rule
    return current


async def _dema(
    repo: Repository,
    instrument_id: UUID,
    history: Sequence[RuleRecord],
    rule: Rule,
    at: datetime,
    *,
    since: datetime | None = None,
) -> tuple[list[DemaPoint], str]:
    """DEMA бумаги к моменту ``at`` и источник её баров.

    Таймфрейм и H — из правила оценки, а если в нём их нет, по умолчанию: часовые свечи
    ISS, H = 240 минут (решение 154). Бары — скрипта или ISS, чьи свежее (решение 155),
    за окно в 30 периодов полураспада; с ``since`` — все бары с этого момента и окно до него.
    """
    if rule.dema_timeframe is None:
        timeframe = DEFAULT_TIMEFRAME
        half_lives = [
            HalfLife(since=datetime.min.replace(tzinfo=MOSCOW), minutes=DEFAULT_HALF_LIFE)
        ]
    else:
        timeframe = rule.dema_timeframe
        half_lives = [
            HalfLife(since=r.valid_from, minutes=r.rule.dema_half_life)
            for r in history
            if r.rule.dema_half_life is not None
        ]
    iss = await repo.prefers_iss_bars(instrument_id, timeframe, at)
    count = window(timeframe, half_lives)
    if since is None:
        bars = await repo.bars(instrument_id, timeframe, ending_by=at, iss=iss, last=count)
    else:
        warmup = await repo.bars(instrument_id, timeframe, ending_by=since, iss=iss, last=count)
        inside = await repo.bars(
            instrument_id, timeframe, ending_by=at, starting_from=since, iss=iss
        )
        bars = warmup + inside
    return dema(bars, half_lives), ISS_BARS if iss else BARS
