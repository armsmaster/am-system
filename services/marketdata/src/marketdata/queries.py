"""Чтение рыночных данных: цена для оценки на момент, ряды для графика, НКД и номинал."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from am_common.calendar import previous_trading_day
from marketdata.accrual import OnDate, accrued_on, face_value_on
from marketdata.db import accrued_interest, face_value
from marketdata.dema import Bar, DemaPoint, HalfLife, dema
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
from marketdata.repository import InstrumentRecord, Repository, RuleRecord

logger = logging.getLogger(__name__)

BARS = "bars"  # источник DEMA: сервис считает её сам по барам


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
        smoothed = await _dema(repo, record.id, history, rule, at)
        if smoothed:
            last = smoothed[-1]
            found.append(Observation(PriceType.DEMA, BARS, last.time, last.value))
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
    bars = {
        timeframe: await repo.bars(instrument_id, timeframe, ending_by=till, starting_from=since)
        for timeframe in await repo.bar_timeframes(instrument_id)
    }
    smoothed = [p for p in await _dema(repo, instrument_id, history, rule, till) if p.time >= since]
    return SeriesView(
        instrument=record,
        quotes=await repo.quotes_between(instrument_id, since, till),
        bars=bars,
        dema=smoothed,
        manual=await repo.manual_between(instrument_id, since, till),
        rules=history,
    )


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
    repo: Repository, instrument_id: UUID, history: Sequence[RuleRecord], rule: Rule, at: datetime
) -> list[DemaPoint]:
    if rule.dema_timeframe is None:
        return []
    half_lives = [
        HalfLife(since=r.valid_from, minutes=r.rule.dema_half_life)
        for r in history
        if r.rule.dema_half_life is not None
    ]
    bars = await repo.bars(instrument_id, rule.dema_timeframe, ending_by=at)
    return dema(bars, half_lives)
