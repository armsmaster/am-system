"""Граница закрытого периода фонда (design/08, решения 39, 48, 97, 126–128).

Закрывает день сервис сверки, когда все счета фонда сверены; открывает заново
только администратор с комментарием. Дни закрываются строго подряд: закрытие
торгового дня закрывает и выходные перед ним. Повторное открытие дня открывает
и все закрытые дни после него.

Граница — здесь, а не в сервисе сверки: проверка «день закрыт?» идёт в той же
транзакции, что и правка сделки (раздел 3 архитектуры). Закрытие берёт
блокировку фонда монопольно, правки — совместно, поэтому правка и закрытие
не проскочат друг мимо друга.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from am_common.calendar import is_trading_day
from am_common.clock import MOSCOW
from am_common.events import Actor, Event
from am_common.outbox import Outbox
from booking.clients import RefdataClient
from booking.db import outbox
from booking.repository import Repository
from booking.trades import SERVICE, RuleViolationError

CLOSE = "close"
REOPEN = "reopen"
ONE_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class ClosingState:
    fund_id: UUID
    closed_through: date | None  # закрыто по этот день включительно
    next_day: date | None  # следующий день к закрытию; None — истории фонда ещё нет
    history: list[Row[Any]]


class ClosingDesk:
    def __init__(
        self,
        session: AsyncSession,
        refdata: RefdataClient,
        clock: Callable[[], datetime],
    ) -> None:
        self._session = session
        self._refdata = refdata
        self._clock = clock
        self._repo = Repository(session)
        self._outbox = Outbox(outbox)

    async def state(self, fund_id: UUID) -> ClosingState:
        async with self._session.begin():
            history = await self._repo.closings(fund_id)
            first = await self._repo.first_day(fund_id)
        closed = history[-1].closed_through if history else None
        exceptions = await self._calendar(closed, first)
        return ClosingState(fund_id, closed, _next_day(closed, first, exceptions), history)

    async def close(self, fund_id: UUID, day: date, actor: Actor, fund_code: str) -> ClosingState:
        """Закрыть следующий день фонда: сверку проверил сервис сверки."""
        exceptions = await self._refdata.calendar_around(day)
        today = self._clock().astimezone(MOSCOW).date()
        async with self._session.begin():
            await self._repo.lock_fund(fund_id, shared=False)
            history = await self._repo.closings(fund_id)
            closed = history[-1].closed_through if history else None
            first = await self._repo.first_day(fund_id)
            if first is None:
                raise RuleViolationError(
                    "no_history", "У фонда ещё нет истории — закрывать нечего."
                )
            expected = _next_day(closed, first, exceptions)
            if day != expected:
                raise RuleViolationError(
                    "close_out_of_order",
                    f"Дни закрываются подряд: следующий к закрытию — {expected:%d.%m.%Y}.",
                )
            if day >= today:
                raise RuleViolationError(
                    "day_not_over", "День ещё не закончился: закрывают прошедший день."
                )
            waiting = (await self._repo.waiting([fund_id])).get(fund_id)
            if waiting is not None and waiting <= day:
                raise RuleViolationError(
                    "recalculating",
                    f"Фонд пересчитывается с {waiting:%d.%m.%Y}: сначала пересчёт, потом сверка "
                    "и закрытие.",
                )
            reached = (await self._repo.last_days()).get(fund_id)
            if reached is None or reached < day:
                raise RuleViolationError(
                    "not_calculated", f"Расчёта фонда за {day:%d.%m.%Y} ещё нет."
                )
            # Выходные перед днём закрываются вместе с ним (решение 126).
            start = first if closed is None or closed < first else closed + ONE_DAY
            await self._record(
                fund_id,
                CLOSE,
                days=(start, day, day),
                actor=actor,
                comment=None,
                fund_code=fund_code,
                event_type="day.closed",
            )
        return await self.state(fund_id)

    async def reopen(
        self, fund_id: UUID, day: date, comment: str, actor: Actor, fund_code: str
    ) -> ClosingState:
        """Открыть день заново — вместе со всеми закрытыми днями после него (решение 127)."""
        async with self._session.begin():
            await self._repo.lock_fund(fund_id, shared=False)
            history = await self._repo.closings(fund_id)
            closed = history[-1].closed_through if history else None
            if closed is None or day > closed:
                raise RuleViolationError(
                    "day_not_closed",
                    f"{day:%d.%m.%Y} не закрыт"
                    + ("." if closed is None else f": закрыто по {closed:%d.%m.%Y}."),
                )
            await self._record(
                fund_id,
                REOPEN,
                days=(day, closed, day - ONE_DAY),
                actor=actor,
                comment=comment,
                fund_code=fund_code,
                event_type="day.reopened",
            )
        return await self.state(fund_id)

    async def _record(
        self,
        fund_id: UUID,
        action: str,
        *,
        days: tuple[date, date, date],  # первый и последний день действия, граница после него
        actor: Actor,
        comment: str | None,
        fund_code: str,
        event_type: str,
    ) -> None:
        first, last, closed_through = days
        values: dict[str, Any] = {
            "fund_id": fund_id,
            "action": action,
            "first_day": first,
            "last_day": last,
            "closed_through": closed_through,
            "author": actor.username,
            "comment": comment,
            "created_at": self._clock(),
        }
        await self._repo.add_closing(values)
        await self._outbox.add(
            self._session,
            Event(
                service=SERVICE,
                type=event_type,
                actor=actor,
                entity_type="fund",
                entity_id=str(fund_id),
                data={
                    "fund": {"code": fund_code},
                    "before": None,
                    "after": {
                        "first_day": first.isoformat(),
                        "last_day": last.isoformat(),
                        "closed_through": closed_through.isoformat(),
                    },
                },
                comment=comment,
            ),
        )

    async def _calendar(self, closed: date | None, first: date | None) -> Mapping[date, bool]:
        if first is None:
            return {}
        return await self._refdata.calendar_around(max(first, closed or first))


def _next_day(
    closed: date | None, first: date | None, exceptions: Mapping[date, bool]
) -> date | None:
    """Следующий к закрытию: первый торговый день после закрытых, но не раньше истории фонда."""
    if first is None:
        return None
    day = first if closed is None or closed < first else closed + ONE_DAY
    while not is_trading_day(day, exceptions):
        day += ONE_DAY
    return day
