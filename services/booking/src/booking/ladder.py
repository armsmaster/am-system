"""Лестница расчётов по счёту (design/05, «Лестница расчётов»).

Прогноз — тот же ролл фонда от последнего снапшота до горизонта. Отличий
два: расчёт по паям, который ещё не отмечен, проходит в плановую дату
(вопрос 51), а просроченная плановая дата — сегодня; необъявленный купон
флоатера с параметрами в справочнике приходит в сумме прогноза по текущей
ставке индекса (решение 14). Лестница суммирует
портфели счёта (решение 33): на конец каждого дня — деньги и бумаги на счёте
депо, движения дня и дефициты.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from am_common.calendar import add_trading_days, is_trading_day
from booking.clients import MarketdataClient, PortfolioRef, RefdataClient, ServiceUnavailableError
from booking.recalc import load_inputs, terms_book
from booking.repository import Repository
from booking.roll import (
    ZERO,
    Forecast,
    Movement,
    MovementKind,
    Operation,
    Payment,
    Repo,
    State,
    TermsOn,
    Trade,
    roll_fund_days,
)

# Горизонт — не меньше 10 торговых дней (вопрос 93).
HORIZON_TRADING_DAYS = 10


@dataclass(frozen=True, slots=True)
class LadderDay:
    day: date
    trading: bool
    movements: tuple[tuple[UUID, Movement], ...]  # портфель, движение
    cash: Decimal
    securities: Mapping[UUID, int]  # бумага → на счёте депо на конец дня

    @property
    def short(self) -> list[UUID]:
        """Бумаги, которых не хватает к расчётам."""
        return [i for i, quantity in self.securities.items() if quantity < 0]


@dataclass(frozen=True, slots=True)
class CouponForecast:
    """Купон флоатера в лестнице по прогнозу: на бумагу и из какой ставки."""

    instrument_id: UUID
    pay_date: date
    coupon: Decimal
    forecast: Forecast


@dataclass(frozen=True, slots=True)
class Ladder:
    account_id: UUID
    portfolios: list[PortfolioRef]
    as_of: date | None  # последний снапшот, от которого идёт прогноз
    waiting_since: date | None
    days: list[LadderDay]
    names: Mapping[UUID, Any]  # бумага → код и название
    forecasts: list[CouponForecast] = field(default_factory=list)


def horizon(
    today: date,
    trades: Mapping[UUID, Sequence[Trade]],
    operations: Sequence[Operation],
    exceptions: Mapping[date, bool],
    repos: Mapping[UUID, Sequence[Repo]] | None = None,
) -> date:
    """До последнего известного будущего расчёта, но не меньше 10 торговых дней."""
    known = (
        [t.settle_date for portfolio in trades.values() for t in portfolio]
        + [o.planned for o in operations if o.settled is None and o.planned is not None]
        + [r.second_date for portfolio in (repos or {}).values() for r in portfolio]
    )
    return max([add_trading_days(today, HORIZON_TRADING_DAYS, exceptions), *known])


def project(
    previous: Mapping[UUID, State],
    first_day: date,
    today: date,
    *,
    account: Collection[UUID],
    trades: Mapping[UUID, Sequence[Trade]],
    operations: Sequence[Operation],
    terms_on: TermsOn,
    exceptions: Mapping[date, bool],
    repos: Mapping[UUID, Sequence[Repo]] | None = None,
) -> list[LadderDay]:
    """Дни лестницы с сегодняшнего: торговые и те, где есть движения по счёту."""
    planned = [
        replace(o, planned=today)
        if o.settled is None and o.planned is not None and o.planned < today
        else o
        for o in operations
    ]
    till = horizon(today, trades, planned, exceptions, repos)
    days = roll_fund_days(
        previous,
        first_day,
        till,
        trades=trades,
        operations=planned,
        terms_on=terms_on,
        projected=True,
        repos=repos,
    )
    instruments: set[UUID] = set()
    for states in days:
        for portfolio_id, state in states.items():
            if portfolio_id in account:
                instruments |= set(state.holdings)
    result: list[LadderDay] = []
    unknown: set[tuple[UUID, UUID | None]] = set()
    for offset, states in enumerate(days):
        day = first_day + timedelta(days=offset)
        if day < today:
            continue
        mine = sorted(
            ((p, s) for p, s in states.items() if p in account), key=lambda item: str(item[0])
        )
        movements: list[tuple[UUID, Movement]] = []
        for portfolio_id, state in mine:
            for movement in state.movements:
                if movement.kind is MovementKind.PAYMENT_UNKNOWN:
                    # Сумма не объявлена — пишем один раз, а не каждый день ожидания.
                    key = (portfolio_id, movement.instrument_id)
                    if key in unknown:
                        continue
                    unknown.add(key)
                movements.append((portfolio_id, movement))
        trading = is_trading_day(day, exceptions)
        if not trading and not movements:
            continue
        securities = dict.fromkeys(sorted(instruments, key=str), 0)
        for _, state in mine:
            for holding in state.holdings.values():
                securities[holding.instrument_id] += holding.settle_quantity
        result.append(
            LadderDay(
                day=day,
                trading=trading,
                movements=tuple(movements),
                cash=sum((s.cash for _, s in mine), ZERO),
                securities=securities,
            )
        )
    return result


async def ladder(
    session: AsyncSession,
    refdata: RefdataClient,
    visible: Mapping[UUID, PortfolioRef],
    account_id: UUID,
    today: date,
    *,
    marketdata: MarketdataClient,
) -> Ladder | None:
    """Лестница счёта; None — у пользователя нет портфелей на этом счёте.
    Купоны флоатеров без объявленной суммы — по прогнозу из текущих ставок индексов."""
    portfolios = sorted(
        (p for p in visible.values() if p.account_id == account_id and p.kind == "physical"),
        key=lambda p: p.code,
    )
    if not portfolios:
        return None
    # Счёт принадлежит одному фонду, у physical фонд есть всегда.
    fund_id = cast(UUID, portfolios[0].fund_id)
    repo = Repository(session)
    inputs = await load_inputs(repo, fund_id, today, today)
    waiting = await repo.waiting([fund_id])
    first_day = min(inputs.first_day, today)
    inputs = replace(inputs, first_day=first_day)
    latest = max(
        [today]
        + [t.settle_date for portfolio in inputs.trades.values() for t in portfolio]
        + [r.second_date for portfolio in inputs.repos.values() for r in portfolio]
        + [o.planned for o in inputs.operations if o.planned is not None]
    )
    book, exceptions = await terms_book(refdata, inputs, latest, await _rates(marketdata, today))
    days = project(
        inputs.states,
        first_day,
        today,
        account={p.id for p in portfolios},
        trades=inputs.trades,
        operations=inputs.operations,
        terms_on=book,
        exceptions=exceptions,
        repos=inputs.repos,
    )
    instruments = {i for d in days for i in d.securities} | {
        m.instrument_id for d in days for _, m in d.movements if m.instrument_id is not None
    }
    return Ladder(
        account_id=account_id,
        portfolios=portfolios,
        as_of=inputs.previous[0].day if inputs.previous else None,
        waiting_since=waiting.get(fund_id),
        days=days,
        names=await repo.instruments_named(instruments),
        forecasts=_forecasts(days, book),
    )


async def _rates(marketdata: MarketdataClient, today: date) -> dict[str, Decimal] | None:
    """Ставки индексов на сегодня; сервис цен недоступен — лестница без прогноза."""
    try:
        return await marketdata.rates(today)
    except ServiceUnavailableError:
        return None


def _forecasts(days: Sequence[LadderDay], book: TermsOn) -> list[CouponForecast]:
    """Из чего посчитан каждый прогнозный купон лестницы: по бумаге и дате — один раз."""
    found: dict[tuple[UUID, date], CouponForecast] = {}
    for day in days:
        for _, movement in day.movements:
            if movement.kind is not MovementKind.COUPON_FORECAST:
                continue
            # Прогнозный купон всегда по бумаге и по выплате с прогнозом.
            instrument_id = cast(UUID, movement.instrument_id)
            payment = cast(Payment, book(instrument_id, day.day).paid_on(day.day))
            found[(instrument_id, day.day)] = CouponForecast(
                instrument_id,
                day.day,
                cast(Decimal, payment.coupon),
                cast(Forecast, payment.forecast),
            )
    return list(found.values())
