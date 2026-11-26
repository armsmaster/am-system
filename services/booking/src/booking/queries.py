"""Чтение позиций и результата: снапшоты дня по physical-портфелям и их сумма для compound.

Сегодняшний день оценивается по текущим ценам при чтении (решение 68): снапшот
дня считали по ценам на момент пересчёта, а цены с тех пор могли измениться.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from am_common.calendar import add_trading_days, is_trading_day
from am_common.clock import MOSCOW
from am_quant.kalman import NAV_SETTINGS, Settings, kalman_trend
from booking.clients import (
    MarketdataClient,
    Metrics,
    PortfolioRef,
    RefdataClient,
    ServiceUnavailableError,
    physical_under,
)
from booking.repository import Repository, Snapshot
from booking.roll import (
    HUNDRED,
    ZERO,
    DayResult,
    Flag,
    Lot,
    Mark,
    Movement,
    RepoPosition,
    State,
    Valuation,
    from_json,
    remark,
)

# Периоды результата по бумагам (решение 164): неделя и 30 дней — скользящие, месяц,
# квартал и год — с начала.
PERIODS = ("week", "days30", "month", "quarter", "year")


def period_starts(day: date) -> dict[str, date]:
    """Первый день каждого периода, который заканчивается ``day``."""
    return {
        "week": day - timedelta(days=6),
        "days30": day - timedelta(days=29),
        "month": day.replace(day=1),
        "quarter": date(day.year, 3 * ((day.month - 1) // 3) + 1, 1),
        "year": date(day.year, 1, 1),
    }


@dataclass(slots=True)
class PeriodTotal:
    """Результат бумаги за период и реализованная часть, ₽."""

    result: Decimal = ZERO
    realized: Decimal = ZERO


def _no_periods() -> dict[str, PeriodTotal]:
    return {name: PeriodTotal() for name in PERIODS}


@dataclass(frozen=True, slots=True)
class PortfolioState:
    portfolio: PortfolioRef
    as_of: date | None  # день снапшота; None — расчёта ещё нет
    waiting_since: date | None  # стоит в очереди пересчёта с этой даты
    cash: Decimal
    movements: tuple[Movement, ...]  # движения дня снапшота
    units: Decimal = ZERO
    claims: Decimal = ZERO
    obligations: Decimal = ZERO
    expenses: Decimal = ZERO
    result: DayResult | None = None  # оценка и результат дня; None — ещё не оценён


@dataclass(frozen=True, slots=True)
class RepoLine:
    """Открытая сделка РЕПО на дату: позиция ролла и условия сделки."""

    portfolio: PortfolioRef
    position: RepoPosition
    trade: Any  # последняя версия сделки: бумага, ставка


@dataclass(slots=True)
class PositionLine:
    instrument_id: UUID
    kind: str
    secid: str
    short_name: str
    trade_quantity: int = 0
    settle_quantity: int = 0
    lots: list[tuple[str, Lot]] = field(default_factory=list)  # код портфеля, лот
    valuations: list[Valuation] = field(default_factory=list)  # по портфелям
    metrics: Metrics | None = None
    periods: dict[str, PeriodTotal] = field(default_factory=_no_periods)

    @property
    def cost(self) -> Decimal:
        return sum((lot.cost for _, lot in self.lots), ZERO)

    @property
    def accrued_paid(self) -> Decimal:
        return sum((lot.accrued_paid for _, lot in self.lots), ZERO)

    @property
    def average_price(self) -> Decimal | None:
        """Справочная средняя цена открытых лотов: облигация — % от номинала, пай — ₽."""
        base = sum(
            (
                lot.quantity * (lot.face / HUNDRED if lot.face is not None else 1)
                for _, lot in self.lots
            ),
            ZERO,
        )
        return None if not base else self.cost / base

    @property
    def price(self) -> Decimal | None:
        return next((v.price for v in self.valuations if v.price is not None), None)

    def total(self, name: str) -> Decimal:
        """Сумма поля оценки по портфелям: clean, accrued_value, revaluation…"""
        return sum((getattr(v, name) for v in self.valuations), ZERO)

    @property
    def flags(self) -> list[Flag]:
        return sorted({f for v in self.valuations for f in v.flags})

    @property
    def dv01(self) -> Decimal | None:
        return None if self.metrics is None else self.trade_quantity * self.metrics.dv01


@dataclass(frozen=True, slots=True)
class PositionsView:
    portfolio: PortfolioRef
    day: date
    portfolios: list[PortfolioState]
    lines: list[PositionLine]
    repos: list[RepoLine] = field(default_factory=list)
    # Бумаги, которых в позиции уже нет, — их результат за периоды одной суммой.
    closed: dict[str, PeriodTotal] = field(default_factory=_no_periods)

    @property
    def cash(self) -> Decimal:
        return sum((p.cash for p in self.portfolios), ZERO)

    @property
    def units(self) -> Decimal:
        return sum((p.units for p in self.portfolios), ZERO)

    @property
    def claims(self) -> Decimal:
        return sum((p.claims for p in self.portfolios), ZERO)

    @property
    def obligations(self) -> Decimal:
        return sum((p.obligations for p in self.portfolios), ZERO)

    @property
    def expenses(self) -> Decimal:
        return sum((p.expenses for p in self.portfolios), ZERO)

    @property
    def results(self) -> list[DayResult]:
        """Оценка портфелей на этот день; портфель без расчёта на день не входит."""
        return [p.result for p in self.portfolios if p.result is not None and p.as_of == self.day]

    def result(self, name: str) -> Decimal:
        return sum((getattr(r, name) for r in self.results), ZERO)

    @property
    def flags(self) -> list[Flag]:
        return sorted({f for r in self.results for f in r.flags})

    def dv01(self, basis: str | None = None) -> Decimal:
        """DV01 портфеля: по доходности — облигации с фиксированным купоном;
        без основания — по спреду, все облигации (у фиксированного купона это то же)."""
        return sum(
            (
                line.dv01
                for line in self.lines
                if line.dv01 is not None
                and line.metrics is not None
                and (basis is None or line.metrics.basis == basis)
            ),
            ZERO,
        )


async def positions(
    session: AsyncSession,
    visible: Mapping[UUID, PortfolioRef],
    portfolio_id: UUID,
    day: date,
    *,
    now: datetime,
    marketdata: MarketdataClient | None = None,
) -> PositionsView | None:
    """Позиции портфеля на конец дня; compound — сумма по уникальным physical-потомкам.

    С ``marketdata`` — сегодня по текущим ценам и DV01 с дюрацией по бумагам.
    """
    portfolio = visible.get(portfolio_id)
    if portfolio is None:
        return None
    physical = physical_under(portfolio_id, visible)
    repo = Repository(session)
    waiting = await repo.waiting(p.fund_id for p in physical)
    found: list[tuple[PortfolioRef, State | None]] = []
    for ref in physical:
        snapshot = await repo.snapshot_on(ref.id, day)
        found.append((ref, None if snapshot is None else from_json(snapshot.state)))
    today = now.astimezone(MOSCOW).date()
    if marketdata is not None and day == today:
        found = await _remarked(found, marketdata, today)

    states: list[PortfolioState] = []
    lines: dict[UUID, PositionLine] = {}
    open_repos: list[tuple[PortfolioRef, RepoPosition]] = []
    for ref, state in found:
        states.append(_portfolio_state(ref, state, day, waiting))
        if state is None:
            continue
        if state.day == day:
            open_repos += [(ref, r) for r in state.repos]
        for holding in state.holdings.values():
            line = lines.setdefault(
                holding.instrument_id,
                PositionLine(holding.instrument_id, holding.kind.value, "", ""),
            )
            line.trade_quantity += holding.trade_quantity
            line.settle_quantity += holding.settle_quantity
            line.lots += [(ref.code, lot) for lot in holding.lots]
        if state.day != day:
            continue
        for valuation in state.valuations.values():
            lines.setdefault(
                valuation.instrument_id,
                PositionLine(valuation.instrument_id, valuation.kind.value, "", ""),
            ).valuations.append(valuation)
    names = await repo.instruments_named(lines)
    at = now if day == today else datetime.combine(day, time.max, tzinfo=MOSCOW)
    for instrument_id, line in lines.items():
        # Позиция есть только по бумагам из сделок и переводов — имя есть всегда.
        line.secid, line.short_name = names[instrument_id].secid, names[instrument_id].short_name
        if marketdata is not None and line.kind == "bond" and line.trade_quantity:
            line.metrics = await marketdata.metrics(instrument_id, at)
    deals = await repo.trades_by_id(r.id for _, r in open_repos)
    today_valuations = [v for line in lines.values() for v in line.valuations]
    totals = await period_results(repo, [p.id for p in physical], day, today_valuations)
    closed = _spread_periods(lines, totals)
    return PositionsView(
        portfolio,
        day,
        states,
        sorted(lines.values(), key=lambda line: line.secid),
        [RepoLine(ref, r, deals[r.id]) for ref, r in open_repos],
        closed,
    )


def _spread_periods(
    lines: Mapping[UUID, PositionLine], totals: Mapping[UUID, dict[str, PeriodTotal]]
) -> dict[str, PeriodTotal]:
    """Результат за периоды — по строкам позиций; бумаг, которых в позиции нет, — одной
    суммой: её возвращаем."""
    closed = _no_periods()
    for instrument_id, periods in totals.items():
        if instrument_id in lines:
            lines[instrument_id].periods = periods
            continue
        for name, total in periods.items():
            closed[name].result += total.result
            closed[name].realized += total.realized
    return closed


async def period_results(
    repo: Repository, portfolio_ids: list[UUID], day: date, today: Iterable[Valuation]
) -> dict[UUID, dict[str, PeriodTotal]]:
    """Результат и реализованный результат каждой бумаги за периоды до ``day`` включительно.

    Прошлые дни — из действующих снапшотов, сам ``day`` — из оценки, которую уже
    показывают позиции: сегодня она по текущим ценам.
    """
    starts = period_starts(day)
    totals: dict[UUID, dict[str, PeriodTotal]] = defaultdict(_no_periods)

    def add(instrument_id: UUID, on: date, result: Decimal, realized: Decimal) -> None:
        for name, start in starts.items():
            if on >= start:
                totals[instrument_id][name].result += result
                totals[instrument_id][name].realized += realized

    first, yesterday = min(starts.values()), day - timedelta(days=1)
    past = await repo.snapshots_between(portfolio_ids, first, yesterday)
    for snapshot in past:
        for v in snapshot.state.get("valuations", []):
            parts = [Decimal(v[name]) for name in ("revaluation", "realized", "coupon", "fees")]
            add(UUID(v["instrument_id"]), snapshot.day, sum(parts, ZERO), Decimal(v["realized"]))
    for valuation in today:
        add(valuation.instrument_id, day, valuation.result, valuation.realized)
    return dict(totals)


def previous_trading_day(day: date, exceptions: Mapping[date, bool]) -> date:
    """Ближайший торговый день раньше ``day``."""
    current = day - timedelta(days=1)
    while not is_trading_day(current, exceptions):
        current -= timedelta(days=1)
    return current


def _portfolio_state(
    ref: PortfolioRef, state: State | None, day: date, waiting: Mapping[UUID, date]
) -> PortfolioState:
    return PortfolioState(
        portfolio=ref,
        as_of=None if state is None else state.day,
        waiting_since=None if ref.fund_id is None else waiting.get(ref.fund_id),
        cash=ZERO if state is None else state.cash,
        # Движения — только если снапшот именно на этот день.
        movements=() if state is None or state.day != day else state.movements,
        units=ZERO if state is None else state.units,
        claims=ZERO if state is None else state.claims,
        obligations=ZERO if state is None else state.obligations,
        expenses=ZERO if state is None else state.expenses,
        result=None if state is None else state.result,
    )


async def _remarked(
    found: list[tuple[PortfolioRef, State | None]], marketdata: MarketdataClient, today: date
) -> list[tuple[PortfolioRef, State | None]]:
    """Сегодняшние снапшоты — по текущим ценам: одна отметка на бумагу."""
    held = sorted(
        {
            i
            for _, state in found
            if state is not None and state.day == today
            for i, v in state.valuations.items()
            if v.quantity
        },
        key=str,
    )
    marks: dict[UUID, Mark] = {}
    for instrument_id in held:
        today_marks = await marketdata.marks(instrument_id, today, today)
        marks[instrument_id] = today_marks.get(today, Mark(price=None))

    def marks_on(instrument_id: UUID, _day: date) -> Mark:
        return marks.get(instrument_id, Mark(price=None))

    return [
        (ref, remark(state, marks_on) if state is not None and state.day == today else state)
        for ref, state in found
    ]


# --- результат за период ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResultDay:
    day: date
    value: Decimal
    flows: Decimal
    revaluation: Decimal
    realized: Decimal
    coupon: Decimal
    fees: Decimal
    expenses: Decimal
    charges: Decimal
    correction: Decimal
    flags: list[Flag]
    repo: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return (
            self.revaluation
            + self.realized
            + self.coupon
            + self.fees
            + self.expenses
            + self.charges
            + self.correction
            + self.repo
        )


@dataclass(slots=True)
class ResultLine:
    """Результат по бумаге за период."""

    instrument_id: UUID
    secid: str = ""
    short_name: str = ""
    revaluation: Decimal = ZERO
    realized: Decimal = ZERO
    coupon: Decimal = ZERO
    fees: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return self.revaluation + self.realized + self.coupon + self.fees


@dataclass(frozen=True, slots=True)
class ResultView:
    portfolio: PortfolioRef
    first: date
    last: date
    portfolios: list[PortfolioRef]
    waiting_since: date | None
    days: list[ResultDay]
    lines: list[ResultLine]


async def results(
    session: AsyncSession,
    visible: Mapping[UUID, PortfolioRef],
    portfolio_id: UUID,
    period: tuple[date, date],
    *,
    now: datetime,
    marketdata: MarketdataClient | None = None,
) -> ResultView | None:
    """Результат по дням и по бумагам за период; compound — сумма по physical-потомкам."""
    portfolio = visible.get(portfolio_id)
    if portfolio is None:
        return None
    first, last = period
    physical = physical_under(portfolio_id, visible)
    repo = Repository(session)
    states = await _states(repo, physical, (first, last), now, marketdata)
    lines: dict[UUID, ResultLine] = {}
    for _, state in states:
        for v in state.valuations.values():
            line = lines.setdefault(v.instrument_id, ResultLine(v.instrument_id))
            line.revaluation += v.revaluation
            line.realized += v.realized
            line.coupon += v.coupon
            line.fees += v.fees
    names = await repo.instruments_named(lines)
    for instrument_id, line in lines.items():
        line.secid, line.short_name = names[instrument_id].secid, names[instrument_id].short_name
    waiting = await repo.waiting(p.fund_id for p in physical)
    return ResultView(
        portfolio=portfolio,
        first=first,
        last=last,
        portfolios=physical,
        waiting_since=min(waiting.values(), default=None),
        days=_days(states),
        lines=sorted(lines.values(), key=lambda line: line.secid),
    )


async def _states(
    repo: Repository,
    physical: list[PortfolioRef],
    period: tuple[date, date],
    now: datetime,
    marketdata: MarketdataClient | None,
) -> list[tuple[Snapshot, State]]:
    """Снапшоты портфелей за период; сегодняшние — по текущим ценам."""
    snapshots = await repo.snapshots_between([p.id for p in physical], *period)
    today = now.astimezone(MOSCOW).date()
    states = [(s, from_json(s.state)) for s in snapshots]
    if marketdata is not None and any(s.day == today for s, _ in states):
        refs = {p.id: p for p in physical}
        remarked = await _remarked(
            [(refs[s.portfolio_id], state) for s, state in states], marketdata, today
        )
        states = [
            (s, cast(State, state)) for (s, _), (_, state) in zip(states, remarked, strict=True)
        ]
    return states


# --- отчёт о финансовом результате (решение 167) -------------------------------------

# Части результата в порядке отчёта: бумаги, РЕПО, расходы фонда.
RESULT_PARTS = (
    "revaluation",
    "realized",
    "coupon",
    "fees",
    "repo",
    "expenses",
    "charges",
    "correction",
)
SUMMARY_PERIODS = ("day", "week", "days30", "quarter", "year", "all")


def summary_periods(
    day: date, first_day: date, custom: tuple[date, date] | None
) -> list[tuple[str, date, date]]:
    """Периоды отчёта, которые заканчиваются ``day``, и свой период, если задан."""
    found = [
        ("day", day, day),
        ("week", day - timedelta(days=6), day),
        ("days30", day - timedelta(days=29), day),
        ("quarter", date(day.year, 3 * ((day.month - 1) // 3) + 1, 1), day),
        ("year", date(day.year, 1, 1), day),
        ("all", min(first_day, day), day),
    ]
    return found if custom is None else [*found, ("custom", *custom)]


@dataclass(slots=True)
class PeriodResult:
    """Результат за период по частям, стоимость, приток и база доходности."""

    key: str
    first: date
    last: date
    parts: dict[str, Decimal] = field(default_factory=lambda: dict.fromkeys(RESULT_PARTS, ZERO))
    flows: Decimal = ZERO
    start_value: Decimal = ZERO
    end_value: Decimal = ZERO
    # Средневзвешенные вложения (Модифицированный Дитц): стоимость на начало и приток,
    # каждый — на долю периода, которую он работал.
    invested: Decimal = ZERO
    flags: set[Flag] = field(default_factory=set)
    lines: dict[UUID, Decimal] = field(default_factory=dict)

    @property
    def total(self) -> Decimal:
        return sum(self.parts.values(), ZERO)

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1

    @property
    def rate(self) -> Decimal | None:
        """Доля от вложений; вложений нет — None."""
        return self.total / self.invested if self.invested > 0 else None


@dataclass(frozen=True, slots=True)
class SummaryLine:
    instrument_id: UUID
    secid: str
    short_name: str
    held: bool  # в позиции на дату отчёта; нет — продана или погашена


@dataclass(frozen=True, slots=True)
class ResultSummary:
    portfolio: PortfolioRef
    day: date
    first_day: date
    portfolios: list[PortfolioRef]
    waiting_since: date | None
    periods: list[PeriodResult]
    lines: list[SummaryLine]
    # Начислено и не оплачено на дату отчёта (решение 168).
    expenses_owed: Decimal = ZERO
    fees_owed: Decimal = ZERO


async def result_summary(
    session: AsyncSession,
    visible: Mapping[UUID, PortfolioRef],
    portfolio_id: UUID,
    day: date,
    *,
    custom: tuple[date, date] | None = None,
    now: datetime,
    marketdata: MarketdataClient | None = None,
) -> ResultSummary | None:
    """Финансовый результат за день, неделю, 30 дней, с начала квартала и года, за всё
    время и за свой период — по частям и по бумагам; compound — сумма по physical."""
    portfolio = visible.get(portfolio_id)
    if portfolio is None:
        return None
    physical = physical_under(portfolio_id, visible)
    repo = Repository(session)
    starts = [await repo.first_day(f) for f in sorted({p.fund_id for p in physical if p.fund_id})]
    first_day = min((s for s in starts if s is not None), default=day)
    periods = [
        PeriodResult(key, first, last)
        for key, first, last in summary_periods(day, first_day, custom)
    ]
    low = min(p.first for p in periods) - timedelta(days=1)
    states = await _states(repo, physical, (low, max(p.last for p in periods)), now, marketdata)
    daily = {d.day: d for d in _days(states)}
    by_instrument: dict[date, dict[UUID, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for snapshot, state in states:
        for v in state.valuations.values():
            by_instrument[snapshot.day][v.instrument_id] += (
                v.revaluation + v.realized + v.coupon + v.fees
            )
    for period in periods:
        _fill(period, daily, by_instrument)
    on_day = [state for snapshot, state in states if snapshot.day == day]
    held = {
        holding.instrument_id
        for state in on_day
        for holding in state.holdings.values()
        if holding.trade_quantity
    }
    instruments = {i for p in periods for i in p.lines}
    names = await repo.instruments_named(instruments)
    waiting = await repo.waiting(p.fund_id for p in physical)
    return ResultSummary(
        portfolio=portfolio,
        day=day,
        first_day=first_day,
        portfolios=physical,
        waiting_since=min(waiting.values(), default=None),
        periods=periods,
        expenses_owed=sum((s.expenses for s in on_day), ZERO),
        fees_owed=sum((s.fees_owed for s in on_day), ZERO),
        lines=sorted(
            (SummaryLine(i, names[i].secid, names[i].short_name, i in held) for i in instruments),
            key=lambda line: line.secid,
        ),
    )


def _fill(
    period: PeriodResult,
    daily: Mapping[date, ResultDay],
    by_instrument: Mapping[date, Mapping[UUID, Decimal]],
) -> None:
    """Сумма дней периода; стоимость на начало — на конец дня перед ним."""
    inside = [d for d in daily.values() if period.first <= d.day <= period.last]
    for found in inside:
        for name in RESULT_PARTS:
            period.parts[name] += getattr(found, name)
        period.flows += found.flows
        period.flags |= set(found.flags)
        # Приток работает с дня поступления до конца периода включительно.
        weight = Decimal((period.last - found.day).days + 1) / period.days
        period.invested += found.flows * weight
        for instrument_id, result in by_instrument.get(found.day, {}).items():
            period.lines[instrument_id] = period.lines.get(instrument_id, ZERO) + result
    before = daily.get(period.first - timedelta(days=1))
    period.start_value = ZERO if before is None else before.value
    period.invested += period.start_value
    period.end_value = inside[-1].value if inside else period.start_value


def _days(states: Iterable[tuple[Snapshot, State]]) -> list[ResultDay]:
    by_day: dict[date, list[DayResult]] = {}
    for snapshot, state in states:
        # Снапшот без оценки — посчитан до части 6.1 или без сервиса цен.
        if state.result is not None:
            by_day.setdefault(snapshot.day, []).append(state.result)
    return [
        ResultDay(
            day=day,
            value=sum((r.value for r in found), ZERO),
            flows=sum((r.flows for r in found), ZERO),
            revaluation=sum((r.revaluation for r in found), ZERO),
            realized=sum((r.realized for r in found), ZERO),
            coupon=sum((r.coupon for r in found), ZERO),
            fees=sum((r.fees for r in found), ZERO),
            expenses=sum((r.expenses for r in found), ZERO),
            charges=sum((r.charges for r in found), ZERO),
            correction=sum((r.correction for r in found), ZERO),
            repo=sum((r.repo for r in found), ZERO),
            flags=sorted({f for r in found for f in r.flags}),
        )
        for day, found in sorted(by_day.items())
    ]


# --- СЧА фонда ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NavPoint:
    """День графика СЧА (решение 168): стоимость пая, сглаженная, сравнение."""

    day: date
    nav: Decimal
    units: Decimal
    unit_price: Decimal | None
    smoothed: Decimal | None  # уровень фильтра Калмана
    benchmark: Decimal | None  # цена LQDT для оценки


@dataclass(frozen=True, slots=True)
class NavForecast:
    day: date
    value: Decimal
    std: Decimal | None  # стандартное отклонение прогноза; мало истории — None
    trend: Decimal  # прирост стоимости пая за день начисления, до комиссий от СЧА
    # Комиссии от СЧА на пай за календарный день — по ставкам последнего дня.
    fees_per_day: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class NavHistory:
    fund: PortfolioRef
    first_day: date | None
    today: date
    waiting_since: date | None
    benchmark: str | None  # код бумаги сравнения; нет в справочнике — None
    points: list[NavPoint]
    forecast: NavForecast | None
    # Два последних дня со стоимостью пая по всей истории — для карточек «сейчас»:
    # они не зависят от периода графика (свой период мог закончиться вчера).
    latest: list[NavPoint] = field(default_factory=list)


async def nav_history(
    session: AsyncSession,
    visible: Mapping[UUID, PortfolioRef],
    fund_id: UUID,
    period: tuple[date | None, date | None],
    *,
    now: datetime,
    marketdata: MarketdataClient,
    refdata: RefdataClient,
    benchmark: str,
    settings: Settings = NAV_SETTINGS,
) -> NavHistory | None:
    """Стоимость пая и СЧА за период (по умолчанию — вся история по сегодня), уровень
    и прогноз фильтра Калмана — по всей истории, цена бумаги сравнения за те же дни.
    ``settings`` — настройки фильтра: свои у пользователя (решение 174)."""
    fund = visible.get(fund_id)
    if fund is None or not fund.is_fund:
        return None
    repo = Repository(session)
    first_day = await repo.first_day(fund_id)
    today = now.astimezone(MOSCOW).date()
    view = (
        None
        if first_day is None
        else await nav(
            session, visible, fund_id, (first_day, today), now=now, marketdata=marketdata
        )
    )
    days = [] if view is None else view.days
    try:
        # Праздники — из календаря торгов; справочник недоступен — только выходные.
        exceptions = await refdata.calendar_around(first_day or today, today + timedelta(days=14))
    except ServiceUnavailableError:
        exceptions = {}
    smoothed, forecast = smooth_unit_price(days, exceptions, settings)
    since, till = period
    shown = [
        d for d in days if (since is None or d.day >= since) and (till is None or d.day <= till)
    ]
    prices, found_benchmark = await _benchmark(refdata, marketdata, benchmark, shown)
    waiting = await repo.waiting([fund.id])
    return NavHistory(
        fund=fund,
        first_day=first_day,
        today=today,
        waiting_since=waiting.get(fund.id),
        benchmark=found_benchmark,
        points=[
            NavPoint(d.day, d.nav, d.units, d.unit_price, smoothed.get(d.day), prices.get(d.day))
            for d in shown
        ],
        forecast=forecast,
        latest=[
            NavPoint(d.day, d.nav, d.units, d.unit_price, smoothed.get(d.day), None)
            for d in [d for d in days if d.unit_price is not None][-2:]
        ],
    )


def smooth_unit_price(
    days: Sequence[NavDay], exceptions: Mapping[date, bool], settings: Settings = NAV_SETTINGS
) -> tuple[dict[date, Decimal], NavForecast | None]:
    """Сглаженная стоимость пая и прогноз на следующий торговый день (решение 169).

    Стоимость пая растёт по дате расчётов T+1: в пятницу — за три дня, в выходные — нет.
    Поэтому фильтр идёт только по торговым дням: время — торговые сессии, начисление —
    дни от первой даты расчётов. Комиссии от СЧА начисляются за каждый календарный
    день и известны точно: их накопленную сумму на пай прибавляем к стоимости пая до
    фильтра и вычитаем после. Так же — с разовыми списаниями по операциям фонда:
    расходами и выплатой вознаграждения сверх начисленного (решение 172). Иначе фильтр
    принял бы скачок за падение доходности и неделями отставал бы от стоимости пая.
    В выходные и праздники сглаженная — уровень последнего торгового дня за вычетом
    комиссий за прошедшие с него дни.
    """

    def settle(day: date) -> date:
        return add_trading_days(day + timedelta(days=1), 0, exceptions)

    cumulative: dict[date, Decimal] = {}
    total = ZERO
    for found in days:
        if found.units:
            total += (sum(found.fees_today.values(), ZERO) + found.charges_today) / found.units
        cumulative[found.day] = total
    observed = [d for d in days if d.unit_price is not None and is_trading_day(d.day, exceptions)]
    if not observed:
        return {}, None
    first, last = observed[0].day, days[-1].day
    target = settle(last)  # следующий торговый день после последнего дня с расчётом
    sessions: dict[date, int] = {}
    count, current = 0, first
    while current <= target:
        if is_trading_day(current, exceptions):
            count += 1
            sessions[current] = count
        current += timedelta(days=1)
    filtered = kalman_trend(
        [
            (Decimal(sessions[d.day]), cast(Decimal, d.unit_price) + cumulative[d.day])
            for d in observed
        ],
        settings,
        [Decimal((settle(d.day) - settle(first)).days) for d in observed],
    )
    levels = {d.day: p.level for d, p in zip(observed, filtered.points, strict=True)}
    smoothed: dict[date, Decimal] = {}
    level: Decimal | None = None
    for found in days:
        level = levels.get(found.day, level)
        if level is not None:
            smoothed[found.day] = level - cumulative[found.day]
    latest = observed[-1].day
    ahead = filtered.forecast(
        Decimal(sessions[target] - sessions[latest]),
        Decimal((settle(target) - settle(latest)).days),
    )
    # Комиссии до прогнозного дня — по ставке последнего дня, за каждый календарный день.
    final = days[-1]
    per_day = sum(final.fees_today.values(), ZERO) / final.units if final.units else ZERO
    value = ahead.value - cumulative[last] - per_day * (target - last).days
    return smoothed, NavForecast(target, value, ahead.std, filtered.points[-1].trend, per_day)


async def _benchmark(
    refdata: RefdataClient, marketdata: MarketdataClient, secid: str, days: Sequence[NavDay]
) -> tuple[dict[date, Decimal], str | None]:
    """Цены бумаги сравнения за дни графика; нет бумаги или сервиса — пусто."""
    if not days:
        return {}, None
    try:
        instrument_id = (await refdata.codes()).get(secid.upper())
        if instrument_id is None:
            return {}, None
        marks = await marketdata.marks(instrument_id, days[0].day, days[-1].day)
    except ServiceUnavailableError:
        return {}, None
    return {day: m.price for day, m in marks.items() if m.price is not None}, secid


@dataclass(frozen=True, slots=True)
class NavDay:
    """СЧА фонда на конец дня и её состав (design/06)."""

    day: date
    nav: Decimal
    units: Decimal
    cash: Decimal
    clean: Decimal  # бумаги по чистой цене
    accrued: Decimal  # НКД в оценке и купоны к получению
    principal_due: Decimal  # погашение номинала к получению (решение 149)
    receivable: Decimal
    payable: Decimal
    claims: Decimal
    obligations: Decimal
    expenses: Decimal  # начисленные и не выплаченные расходы
    fees_owed: Decimal  # начисленные и не выплаченные комиссии от СЧА
    fees_today: dict[str, Decimal]  # начислено за день по видам
    flags: list[Flag]
    repo_placed: Decimal = ZERO  # размещено в обратное РЕПО, с процентами
    repo_borrowed: Decimal = ZERO  # привлечено в прямое РЕПО, с процентами
    # Разовые списания дня, известные по операциям фонда: начисленные расходы и разница
    # при выплате вознаграждения сверх начисленного (решение 172). Меньше начисленного —
    # со знаком минус.
    charges_today: Decimal = ZERO

    @property
    def unit_price(self) -> Decimal | None:
        """Стоимость пая до копейки (решение 104)."""
        return None if not self.units else _kopecks(self.nav / self.units)


@dataclass(frozen=True, slots=True)
class NavView:
    fund: PortfolioRef
    first: date
    last: date
    waiting_since: date | None
    days: list[NavDay]


async def nav(
    session: AsyncSession,
    visible: Mapping[UUID, PortfolioRef],
    fund_id: UUID,
    period: tuple[date, date],
    *,
    now: datetime,
    marketdata: MarketdataClient | None = None,
) -> NavView | None:
    """СЧА фонда по дням: сумма оценённых портфелей фонда; сегодня — по текущим ценам."""
    fund = visible.get(fund_id)
    if fund is None or not fund.is_fund:
        return None
    first, last = period
    physical = physical_under(fund_id, visible)
    repo = Repository(session)
    snapshots = await repo.snapshots_between([p.id for p in physical], first, last)
    today = now.astimezone(MOSCOW).date()
    states = [(s, from_json(s.state)) for s in snapshots]
    if marketdata is not None and any(s.day == today for s, _ in states):
        refs = {p.id: p for p in physical}
        remarked = await _remarked(
            [(refs[s.portfolio_id], state) for s, state in states], marketdata, today
        )
        states = [
            (s, cast(State, state)) for (s, _), (_, state) in zip(states, remarked, strict=True)
        ]
    by_day: dict[date, list[State]] = {}
    for snapshot, state in states:
        if state.result is not None:
            by_day.setdefault(snapshot.day, []).append(state)
    waiting = await repo.waiting([fund.id])
    return NavView(
        fund=fund,
        first=first,
        last=last,
        waiting_since=waiting.get(fund.id),
        days=[_nav_day(day, found) for day, found in sorted(by_day.items())],
    )


def _nav_day(day: date, states: list[State]) -> NavDay:
    results = [cast(DayResult, s.result) for s in states]
    values = [v for s in states for v in s.valuations.values()]
    fees_today: dict[str, Decimal] = {}
    for state in states:
        for kind, amount in state.fees_today.items():
            fees_today[kind] = fees_today.get(kind, ZERO) + amount
    return NavDay(
        day=day,
        nav=sum((r.value for r in results), ZERO),
        units=sum((s.units for s in states), ZERO),
        cash=sum((s.cash for s in states), ZERO),
        clean=sum((v.clean for v in values), ZERO),
        accrued=sum((v.accrued_value + v.coupon_due for v in values), ZERO),
        principal_due=sum((v.principal_due for v in values), ZERO),
        receivable=sum((r.receivable for r in results), ZERO),
        payable=sum((r.payable for r in results), ZERO),
        claims=sum((s.claims for s in states), ZERO),
        obligations=sum((s.obligations for s in states), ZERO),
        expenses=sum((s.expenses for s in states), ZERO),
        fees_owed=sum((s.fees_owed for s in states), ZERO),
        fees_today=fees_today,
        flags=sorted({f for r in results for f in r.flags}),
        repo_placed=sum((r.value for s in states for r in s.repos if not r.direct), ZERO),
        repo_borrowed=sum((-r.value for s in states for r in s.repos if r.direct), ZERO),
        charges_today=-sum((r.expenses + r.correction for r in results), ZERO),
    )


def _kopecks(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
