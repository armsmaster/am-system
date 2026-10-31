"""Чтение позиций: снапшоты дня по physical-портфелям и их сумма для compound."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from booking.clients import PortfolioRef, physical_under
from booking.repository import Repository
from booking.roll import HUNDRED, ZERO, Lot, Movement, from_json


@dataclass(frozen=True, slots=True)
class PortfolioState:
    portfolio: PortfolioRef
    as_of: date | None  # день снапшота; None — расчёта ещё нет
    waiting_since: date | None  # стоит в очереди пересчёта с этой даты
    cash: Decimal
    movements: tuple[Movement, ...]  # движения дня снапшота


@dataclass(slots=True)
class PositionLine:
    instrument_id: UUID
    kind: str
    secid: str
    short_name: str
    trade_quantity: int = 0
    settle_quantity: int = 0
    lots: list[tuple[str, Lot]] = field(default_factory=list)  # код портфеля, лот

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


@dataclass(frozen=True, slots=True)
class PositionsView:
    portfolio: PortfolioRef
    day: date
    portfolios: list[PortfolioState]
    lines: list[PositionLine]

    @property
    def cash(self) -> Decimal:
        return sum((p.cash for p in self.portfolios), ZERO)


async def positions(
    session: AsyncSession, visible: Mapping[UUID, PortfolioRef], portfolio_id: UUID, day: date
) -> PositionsView | None:
    """Позиции портфеля на конец дня; compound — сумма по уникальным physical-потомкам."""
    portfolio = visible.get(portfolio_id)
    if portfolio is None:
        return None
    physical = physical_under(portfolio_id, visible)
    repo = Repository(session)
    waiting = await repo.waiting(p.id for p in physical)
    states: list[PortfolioState] = []
    lines: dict[UUID, PositionLine] = {}
    for ref in physical:
        snapshot = await repo.snapshot_on(ref.id, day)
        state = None if snapshot is None else from_json(snapshot.state)
        states.append(
            PortfolioState(
                portfolio=ref,
                as_of=None if snapshot is None else snapshot.day,
                waiting_since=waiting.get(ref.id),
                cash=ZERO if state is None else state.cash,
                # Движения — только если снапшот именно на этот день.
                movements=() if state is None or state.day != day else state.movements,
            )
        )
        for holding in [] if state is None else state.holdings.values():
            line = lines.setdefault(
                holding.instrument_id,
                PositionLine(holding.instrument_id, holding.kind.value, "", ""),
            )
            line.trade_quantity += holding.trade_quantity
            line.settle_quantity += holding.settle_quantity
            line.lots += [(ref.code, lot) for lot in holding.lots]
    names = await repo.instruments_named(lines)
    for instrument_id, line in lines.items():
        # Позиция есть только по бумагам из сделок — имя есть всегда.
        line.secid, line.short_name = names[instrument_id].secid, names[instrument_id].short_name
    return PositionsView(
        portfolio, day, states, sorted(lines.values(), key=lambda line: line.secid)
    )
