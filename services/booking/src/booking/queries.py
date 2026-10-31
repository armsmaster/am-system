"""Чтение позиций и результата: снапшоты дня по physical-портфелям и их сумма для compound.

Сегодняшний день оценивается по текущим ценам при чтении (решение 68): снапшот
дня считали по ценам на момент пересчёта, а цены с тех пор могли измениться.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from am_common.clock import MOSCOW
from booking.clients import MarketdataClient, Metrics, PortfolioRef, physical_under
from booking.repository import Repository, Snapshot
from booking.roll import (
    HUNDRED,
    ZERO,
    DayResult,
    Flag,
    Lot,
    Mark,
    Movement,
    State,
    Valuation,
    from_json,
    remark,
)


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
    for ref, state in found:
        states.append(_portfolio_state(ref, state, day, waiting))
        if state is None:
            continue
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
    return PositionsView(
        portfolio, day, states, sorted(lines.values(), key=lambda line: line.secid)
    )


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
    flags: list[Flag]

    @property
    def total(self) -> Decimal:
        return self.revaluation + self.realized + self.coupon + self.fees + self.expenses


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
            flags=sorted({f for r in found for f in r.flags}),
        )
        for day, found in sorted(by_day.items())
    ]
