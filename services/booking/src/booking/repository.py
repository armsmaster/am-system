"""Чтение и запись сделок, очереди пересчёта и снапшотов."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Row, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.ext.asyncio import AsyncSession

from booking.db import day_snapshot, recalc_queue, roll_run, trade, trade_version

CANCELLED = "cancelled"


class NotFoundError(Exception):
    def __init__(self, what: str, key: object) -> None:
        super().__init__(f"{what} {key} not found")
        self.what = what
        self.key = key


@dataclass(frozen=True, slots=True)
class Claim:
    portfolio_id: UUID
    since: date
    marked_at: datetime


@dataclass(frozen=True, slots=True)
class Snapshot:
    portfolio_id: UUID
    day: date
    state: dict[str, Any]
    run_id: UUID


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- сделки ---------------------------------------------------------------

    async def add_trade(self, values: dict[str, Any]) -> None:
        await self._session.execute(insert(trade).values(**values))

    async def add_version(self, values: dict[str, Any]) -> None:
        await self._session.execute(insert(trade_version).values(**values))

    async def versions(self, trade_id: UUID, *, lock: bool = False) -> list[Row[Any]]:
        """Все версии сделки, от первой к последней."""
        if lock:
            await self._session.execute(
                select(trade.c.id).where(trade.c.id == trade_id).with_for_update()
            )
        result = await self._session.execute(
            select(trade_version)
            .where(trade_version.c.trade_id == trade_id)
            .order_by(trade_version.c.number)
        )
        rows = list(result)
        if not rows:
            raise NotFoundError("trade", trade_id)
        return rows

    async def current(
        self,
        *,
        portfolio_ids: Iterable[UUID],
        trade_date: date | None = None,
        include_cancelled: bool = True,
    ) -> list[Row[Any]]:
        """Действующие (последние) версии сделок портфелей."""
        ids = set(portfolio_ids)
        # Портфель мог смениться в последней версии — фильтруем после выбора последней.
        latest = (
            select(trade_version)
            .where(
                trade_version.c.trade_id.in_(
                    select(trade_version.c.trade_id).where(trade_version.c.portfolio_id.in_(ids))
                )
            )
            .ext(distinct_on(trade_version.c.trade_id))
            .order_by(trade_version.c.trade_id, trade_version.c.number.desc())
        )
        rows = list(await self._session.execute(latest))
        return sorted(
            (
                r
                for r in rows
                if r.portfolio_id in ids
                and (trade_date is None or r.trade_date == trade_date)
                and (include_cancelled or r.status != CANCELLED)
            ),
            key=lambda r: (r.trade_at, str(r.trade_id)),
        )

    async def portfolios_holding(self, instrument_id: UUID) -> list[UUID]:
        result = await self._session.execute(
            select(trade_version.c.portfolio_id)
            .where(trade_version.c.instrument_id == instrument_id)
            .distinct()
        )
        return [row.portfolio_id for row in result]

    async def portfolios_with_trades(self) -> list[UUID]:
        result = await self._session.execute(select(trade_version.c.portfolio_id).distinct())
        return [row.portfolio_id for row in result]

    async def instruments_named(self, instrument_ids: Iterable[UUID]) -> dict[UUID, Any]:
        """Код и название бумаги — из последней введённой версии с этой бумагой."""
        result = await self._session.execute(
            select(
                trade_version.c.instrument_id,
                trade_version.c.instrument_kind,
                trade_version.c.secid,
                trade_version.c.short_name,
            )
            .where(trade_version.c.instrument_id.in_(list(instrument_ids)))
            .ext(distinct_on(trade_version.c.instrument_id))
            .order_by(trade_version.c.instrument_id, trade_version.c.created_at.desc())
        )
        return {row.instrument_id: row for row in result}

    # --- очередь пересчёта ----------------------------------------------------

    async def mark_dirty(self, portfolio_id: UUID, since: date, now: datetime) -> None:
        """Портфель надо пересчитать с ``since``: раньше отмеченная дата не теряется."""
        statement = insert(recalc_queue).values(
            portfolio_id=portfolio_id, since=since, marked_at=now
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[recalc_queue.c.portfolio_id],
                set_={
                    "since": func.least(recalc_queue.c.since, statement.excluded.since),
                    "marked_at": statement.excluded.marked_at,
                },
            )
        )

    async def claim(self, now: datetime, stale: timedelta) -> Claim | None:
        """Забирает портфель из очереди: его пересчитает только этот экземпляр."""
        waiting = (
            select(recalc_queue.c.portfolio_id)
            .where(
                or_(recalc_queue.c.claimed_at.is_(None), recalc_queue.c.claimed_at < now - stale)
            )
            .order_by(recalc_queue.c.marked_at)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        result = await self._session.execute(
            update(recalc_queue)
            .where(recalc_queue.c.portfolio_id == waiting)
            .values(claimed_at=now)
            .returning(recalc_queue.c.portfolio_id, recalc_queue.c.since, recalc_queue.c.marked_at)
        )
        row = result.one_or_none()
        return None if row is None else Claim(row.portfolio_id, row.since, row.marked_at)

    async def finish(self, claim: Claim) -> None:
        """Снимает портфель с очереди, если за время пересчёта его не отметили снова."""
        await self._session.execute(
            recalc_queue.delete().where(
                and_(
                    recalc_queue.c.portfolio_id == claim.portfolio_id,
                    recalc_queue.c.marked_at == claim.marked_at,
                )
            )
        )
        await self.release(claim.portfolio_id)

    async def release(self, portfolio_id: UUID) -> None:
        await self._session.execute(
            update(recalc_queue)
            .where(recalc_queue.c.portfolio_id == portfolio_id)
            .values(claimed_at=None)
        )

    async def waiting(self, portfolio_ids: Iterable[UUID]) -> dict[UUID, date]:
        """С какой даты портфели ждут пересчёта."""
        result = await self._session.execute(
            select(recalc_queue.c.portfolio_id, recalc_queue.c.since).where(
                recalc_queue.c.portfolio_id.in_(list(portfolio_ids))
            )
        )
        return {row.portfolio_id: row.since for row in result}

    # --- снапшоты -------------------------------------------------------------

    async def snapshot_before(self, portfolio_id: UUID, day: date) -> Snapshot | None:
        """Последний действующий снапшот раньше ``day``."""
        return await self._snapshot(portfolio_id, day_snapshot.c.day < day)

    async def snapshot_on(self, portfolio_id: UUID, day: date) -> Snapshot | None:
        """Действующий снапшот на ``day`` или последний до него."""
        return await self._snapshot(portfolio_id, day_snapshot.c.day <= day)

    async def _snapshot(self, portfolio_id: UUID, condition: Any) -> Snapshot | None:
        result = await self._session.execute(
            select(day_snapshot)
            .where(
                day_snapshot.c.portfolio_id == portfolio_id,
                day_snapshot.c.replaced_at.is_(None),
                condition,
            )
            .order_by(day_snapshot.c.day.desc())
            .limit(1)
        )
        row = result.one_or_none()
        return None if row is None else Snapshot(row.portfolio_id, row.day, row.state, row.run_id)

    async def last_days(self) -> dict[UUID, date]:
        """Последний рассчитанный день по каждому портфелю."""
        result = await self._session.execute(
            select(day_snapshot.c.portfolio_id, func.max(day_snapshot.c.day).label("day"))
            .where(day_snapshot.c.replaced_at.is_(None))
            .group_by(day_snapshot.c.portfolio_id)
        )
        return {row.portfolio_id: row.day for row in result}

    async def save_run(
        self,
        run: dict[str, Any],
        replace_from: date,
        states: Sequence[tuple[date, dict[str, Any]]],
        now: datetime,
    ) -> None:
        """Замещает снапшоты с ``replace_from`` новыми; старые остаются в истории."""
        await self._session.execute(
            update(day_snapshot)
            .where(
                day_snapshot.c.portfolio_id == run["portfolio_id"],
                day_snapshot.c.day >= replace_from,
                day_snapshot.c.replaced_at.is_(None),
            )
            .values(replaced_at=now)
        )
        await self._session.execute(insert(roll_run).values(**run))
        if states:
            await self._session.execute(
                insert(day_snapshot).values(
                    [
                        {
                            "portfolio_id": run["portfolio_id"],
                            "day": day,
                            "run_id": run["id"],
                            "state": state,
                        }
                        for day, state in states
                    ]
                )
            )
