"""День счёта для сверки (design/08): сделки дня, движения денег, остатки на начало и конец.

Сервис сверки сравнивает это с выпиской брокера или банка. Счёт — сумма своих
physical-портфелей (решение 33). Перевод между портфелями одного счёта брокер
не видит: оба его движения попадают в день счёта, и сверка сложит их в ноль.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from am_common.calendar import is_trading_day
from booking.clients import PortfolioRef, RefdataClient
from booking.repository import Repository
from booking.roll import ZERO, Movement, State, from_json

# За один запрос — не больше года: сверке хватает дней с последнего закрытого.
MAX_ACCOUNT_DAYS = 370


@dataclass(frozen=True, slots=True)
class AccountDay:
    day: date
    trading: bool
    calculated: bool  # ролл фонда дошёл до этого дня
    opening: Decimal  # деньги на конец предыдущего дня
    closing: Decimal
    trades: list[Any]  # неотменённые сделки с датой заключения — этот день
    movements: list[tuple[PortfolioRef, Movement]]  # движения денег дня


@dataclass(frozen=True, slots=True)
class AccountDays:
    account_id: UUID
    fund_id: UUID
    portfolios: list[PortfolioRef]
    waiting_since: date | None
    days: list[AccountDay]
    deals: Mapping[UUID, Any]  # сделки, к которым относятся движения: номер, бумага
    names: Mapping[UUID, Any]  # бумаги движений: код и название


async def account_days(
    session: AsyncSession,
    refdata: RefdataClient,
    visible: Mapping[UUID, PortfolioRef],
    account_id: UUID,
    *,
    first: date,
    last: date,
) -> AccountDays | None:
    """Дни счёта с ``first`` по ``last``; None — у пользователя нет портфелей на счёте."""
    portfolios = sorted(
        (p for p in visible.values() if p.account_id == account_id and p.kind == "physical"),
        key=lambda p: p.code,
    )
    if not portfolios:
        return None
    # Счёт принадлежит одному фонду, у physical фонд есть всегда.
    fund_id = cast(UUID, portfolios[0].fund_id)
    ids = [p.id for p in portfolios]
    repo = Repository(session)
    waiting = await repo.waiting([fund_id])
    reached = (await repo.last_days()).get(fund_id)
    exceptions = await refdata.calendar_around(first, last)
    # Деньги портфеля на конец дня — по последнему снапшоту; до начала истории — ноль.
    cash: dict[UUID, Decimal] = {}
    for portfolio_id in ids:
        before = await repo.snapshot_on(portfolio_id, first - timedelta(days=1))
        cash[portfolio_id] = ZERO if before is None else from_json(before.state).cash
    states: dict[date, list[tuple[UUID, State]]] = {}
    for snapshot in await repo.snapshots_between(ids, first, last):
        states.setdefault(snapshot.day, []).append(
            (snapshot.portfolio_id, from_json(snapshot.state))
        )
    trades = [
        t
        for t in await repo.current(portfolio_ids=ids, include_cancelled=False)
        if first <= t.trade_date <= last
    ]
    by_id = {p.id: p for p in portfolios}
    days: list[AccountDay] = []
    day = first
    while day <= last:
        opening = sum(cash.values(), ZERO)
        movements: list[tuple[PortfolioRef, Movement]] = []
        for portfolio_id, state in states.get(day, []):
            cash[portfolio_id] = state.cash
            movements += [(by_id[portfolio_id], m) for m in state.movements if m.cash]
        days.append(
            AccountDay(
                day=day,
                trading=is_trading_day(day, exceptions),
                calculated=reached is not None and day <= reached,
                opening=opening,
                closing=sum(cash.values(), ZERO),
                trades=[t for t in trades if t.trade_date == day],
                movements=movements,
            )
        )
        day += timedelta(days=1)
    deals = await repo.trades_by_id(
        {m.trade_id for d in days for _, m in d.movements if m.trade_id is not None}
    )
    names = await repo.instruments_named(
        {m.instrument_id for d in days for _, m in d.movements if m.instrument_id is not None}
    )
    return AccountDays(
        account_id=account_id,
        fund_id=fund_id,
        portfolios=portfolios,
        waiting_since=waiting.get(fund_id),
        days=days,
        deals=deals,
        names=names,
    )
