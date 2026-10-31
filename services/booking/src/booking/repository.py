"""Чтение и запись сделок, операций фонда, очереди пересчёта и снапшотов."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Row, Table, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.ext.asyncio import AsyncSession

from booking.db import (
    day_snapshot,
    fee_rate,
    operation,
    operation_version,
    recalc_queue,
    roll_run,
    trade,
    trade_version,
)

CANCELLED = "cancelled"


class NotFoundError(Exception):
    def __init__(self, what: str, key: object) -> None:
        super().__init__(f"{what} {key} not found")
        self.what = what
        self.key = key


@dataclass(frozen=True, slots=True)
class Claim:
    fund_id: UUID
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

    async def add_trade(self, values: dict[str, Any]) -> bool:
        """False — сделку с тем же ключом импорта уже записал другой пакет."""
        result = await self._session.execute(
            insert(trade)
            .values(**values)
            .on_conflict_do_nothing(constraint="trade_source_key")
            .returning(trade.c.id)
        )
        return result.scalar_one_or_none() is not None

    async def trade_by_key(self, source: str, source_key: str) -> UUID | None:
        """Сделка по ключу импорта: источник и номер сделки в нём."""
        found: UUID | None = await self._session.scalar(
            select(trade.c.id).where(trade.c.source == source, trade.c.source_key == source_key)
        )
        return found

    async def add_version(self, values: dict[str, Any]) -> None:
        await self._session.execute(insert(trade_version).values(**values))

    async def versions(self, trade_id: UUID, *, lock: bool = False) -> list[Row[Any]]:
        """Все версии сделки, от первой к последней."""
        return await self._versions(trade, trade_version, "trade_id", trade_id, lock=lock)

    async def current(
        self,
        *,
        portfolio_ids: Iterable[UUID],
        trade_date: date | None = None,
        include_cancelled: bool = True,
    ) -> list[Row[Any]]:
        """Действующие (последние) версии сделок портфелей."""
        ids = set(portfolio_ids)
        rows = await self._latest(trade_version, "trade_id", trade_version.c.portfolio_id.in_(ids))
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

    async def fund_trades(self, fund_id: UUID) -> list[Row[Any]]:
        """Действующие неотменённые сделки фонда — вход ролла."""
        rows = await self._latest(trade_version, "trade_id", trade_version.c.fund_id == fund_id)
        return [r for r in rows if r.fund_id == fund_id and r.status != CANCELLED]

    async def instruments_named(self, instrument_ids: Iterable[UUID]) -> dict[UUID, Any]:
        """Код и название бумаги — из последней введённой сделки или перевода с ней."""
        ids = list(instrument_ids)
        found: dict[UUID, Any] = {}
        for versions in (trade_version, operation_version):
            result = await self._session.execute(
                select(
                    versions.c.instrument_id,
                    versions.c.instrument_kind,
                    versions.c.secid,
                    versions.c.short_name,
                    versions.c.created_at,
                )
                .where(versions.c.instrument_id.in_(ids))
                .ext(distinct_on(versions.c.instrument_id))
                .order_by(versions.c.instrument_id, versions.c.created_at.desc())
            )
            for row in result:
                known = found.get(row.instrument_id)
                if known is None or known.created_at < row.created_at:
                    found[row.instrument_id] = row
        return found

    # --- операции фонда -------------------------------------------------------

    async def add_operation(self, values: dict[str, Any]) -> None:
        await self._session.execute(insert(operation).values(**values))

    async def add_operation_version(self, values: dict[str, Any]) -> None:
        await self._session.execute(insert(operation_version).values(**values))

    async def operation_versions(self, operation_id: UUID, *, lock: bool = False) -> list[Row[Any]]:
        return await self._versions(
            operation, operation_version, "operation_id", operation_id, lock=lock
        )

    async def operations(
        self, *, portfolio_ids: Iterable[UUID], op_date: date | None = None
    ) -> list[Row[Any]]:
        """Действующие версии операций, где портфель — отправитель или получатель."""
        ids = set(portfolio_ids)
        rows = await self._latest(
            operation_version,
            "operation_id",
            or_(
                operation_version.c.portfolio_id.in_(ids),
                operation_version.c.receiver_id.in_(ids),
            ),
        )
        return sorted(
            (
                r
                for r in rows
                if (r.portfolio_id in ids or r.receiver_id in ids)
                and (op_date is None or r.op_date == op_date)
            ),
            key=lambda r: (r.op_date, r.created_at),
        )

    async def fund_operations(self, fund_id: UUID) -> list[Row[Any]]:
        """Действующие неотменённые операции фонда — вход ролла."""
        rows = await self._latest(
            operation_version, "operation_id", operation_version.c.fund_id == fund_id
        )
        return [r for r in rows if r.status != CANCELLED]

    # --- ставки комиссий от СЧА -------------------------------------------------

    async def add_fee_rate(self, values: dict[str, Any]) -> None:
        await self._session.execute(insert(fee_rate).values(**values))

    async def fee_rates(self, fund_id: UUID) -> list[Row[Any]]:
        """Ставки фонда в порядке ввода: последняя по дате начала главнее."""
        result = await self._session.execute(
            select(fee_rate)
            .where(fee_rate.c.fund_id == fund_id)
            .order_by(fee_rate.c.valid_from, fee_rate.c.id)
        )
        return list(result)

    # --- что затрагивают изменения справочника ----------------------------------

    async def funds_holding(self, instrument_id: UUID) -> list[UUID]:
        result = await self._session.execute(
            select(trade_version.c.fund_id)
            .where(trade_version.c.instrument_id == instrument_id)
            .union(
                select(operation_version.c.fund_id).where(
                    operation_version.c.instrument_id == instrument_id
                )
            )
        )
        return [row.fund_id for row in result]

    async def funds_with_activity(self) -> list[UUID]:
        result = await self._session.execute(
            select(trade_version.c.fund_id).union(select(operation_version.c.fund_id))
        )
        return [row.fund_id for row in result]

    # --- очередь пересчёта ----------------------------------------------------

    async def mark_dirty(self, fund_id: UUID, since: date, now: datetime) -> None:
        """Фонд надо пересчитать с ``since``: раньше отмеченная дата не теряется."""
        statement = insert(recalc_queue).values(fund_id=fund_id, since=since, marked_at=now)
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[recalc_queue.c.fund_id],
                set_={
                    "since": func.least(recalc_queue.c.since, statement.excluded.since),
                    "marked_at": statement.excluded.marked_at,
                },
            )
        )

    async def claim(self, now: datetime, stale: timedelta) -> Claim | None:
        """Забирает фонд из очереди: его пересчитает только этот экземпляр."""
        waiting = (
            select(recalc_queue.c.fund_id)
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
            .where(recalc_queue.c.fund_id == waiting)
            .values(claimed_at=now)
            .returning(recalc_queue.c.fund_id, recalc_queue.c.since, recalc_queue.c.marked_at)
        )
        row = result.one_or_none()
        return None if row is None else Claim(row.fund_id, row.since, row.marked_at)

    async def finish(self, claim: Claim) -> None:
        """Снимает фонд с очереди, если за время пересчёта его не отметили снова."""
        await self._session.execute(
            recalc_queue.delete().where(
                and_(
                    recalc_queue.c.fund_id == claim.fund_id,
                    recalc_queue.c.marked_at == claim.marked_at,
                )
            )
        )
        await self.release(claim.fund_id)

    async def release(self, fund_id: UUID) -> None:
        await self._session.execute(
            update(recalc_queue).where(recalc_queue.c.fund_id == fund_id).values(claimed_at=None)
        )

    async def waiting(self, fund_ids: Iterable[UUID | None]) -> dict[UUID, date]:
        """С какой даты фонды ждут пересчёта."""
        result = await self._session.execute(
            select(recalc_queue.c.fund_id, recalc_queue.c.since).where(
                recalc_queue.c.fund_id.in_([f for f in fund_ids if f is not None])
            )
        )
        return {row.fund_id: row.since for row in result}

    # --- снапшоты -------------------------------------------------------------

    async def fund_before(self, fund_id: UUID, day: date) -> list[Snapshot]:
        """Действующие снапшоты портфелей фонда на последний день раньше ``day``."""
        last = (
            select(func.max(day_snapshot.c.day))
            .where(
                day_snapshot.c.fund_id == fund_id,
                day_snapshot.c.replaced_at.is_(None),
                day_snapshot.c.day < day,
            )
            .scalar_subquery()
        )
        result = await self._session.execute(
            select(day_snapshot).where(
                day_snapshot.c.fund_id == fund_id,
                day_snapshot.c.replaced_at.is_(None),
                day_snapshot.c.day == last,
            )
        )
        return [Snapshot(r.portfolio_id, r.day, r.state, r.run_id) for r in result]

    async def snapshot_on(self, portfolio_id: UUID, day: date) -> Snapshot | None:
        """Действующий снапшот портфеля на ``day`` или последний до него."""
        result = await self._session.execute(
            select(day_snapshot)
            .where(
                day_snapshot.c.portfolio_id == portfolio_id,
                day_snapshot.c.replaced_at.is_(None),
                day_snapshot.c.day <= day,
            )
            .order_by(day_snapshot.c.day.desc())
            .limit(1)
        )
        row = result.one_or_none()
        return None if row is None else Snapshot(row.portfolio_id, row.day, row.state, row.run_id)

    async def snapshots_between(
        self, portfolio_ids: Iterable[UUID], first: date, last: date
    ) -> list[Snapshot]:
        """Действующие снапшоты портфелей за период — по дням."""
        result = await self._session.execute(
            select(day_snapshot)
            .where(
                day_snapshot.c.portfolio_id.in_(list(portfolio_ids)),
                day_snapshot.c.replaced_at.is_(None),
                day_snapshot.c.day >= first,
                day_snapshot.c.day <= last,
            )
            .order_by(day_snapshot.c.day, day_snapshot.c.portfolio_id)
        )
        return [Snapshot(r.portfolio_id, r.day, r.state, r.run_id) for r in result]

    async def last_days(self) -> dict[UUID, date]:
        """Последний рассчитанный день по каждому фонду."""
        result = await self._session.execute(
            select(day_snapshot.c.fund_id, func.max(day_snapshot.c.day).label("day"))
            .where(day_snapshot.c.replaced_at.is_(None))
            .group_by(day_snapshot.c.fund_id)
        )
        return {row.fund_id: row.day for row in result}

    async def save_run(
        self,
        run: dict[str, Any],
        replace_from: date,
        states: Sequence[tuple[UUID, date, dict[str, Any]]],
        now: datetime,
    ) -> None:
        """Замещает снапшоты фонда с ``replace_from`` новыми; старые остаются в истории."""
        await self._session.execute(
            update(day_snapshot)
            .where(
                day_snapshot.c.fund_id == run["fund_id"],
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
                            "fund_id": run["fund_id"],
                            "portfolio_id": portfolio_id,
                            "day": day,
                            "run_id": run["id"],
                            "state": state,
                        }
                        for portfolio_id, day, state in states
                    ]
                )
            )

    # --- общее ----------------------------------------------------------------

    async def _versions(
        self, head: Table, versions: Table, key: str, entity_id: UUID, *, lock: bool
    ) -> list[Row[Any]]:
        if lock:
            await self._session.execute(
                select(head.c.id).where(head.c.id == entity_id).with_for_update()
            )
        result = await self._session.execute(
            select(versions).where(versions.c[key] == entity_id).order_by(versions.c.number)
        )
        rows = list(result)
        if not rows:
            raise NotFoundError("trade" if head is trade else "operation", entity_id)
        return rows

    async def _latest(self, versions: Table, key: str, condition: Any) -> list[Row[Any]]:
        """Последние версии сущностей, у которых хоть одна версия подходит под условие:
        портфель или фонд могли смениться в последней версии — фильтр после выбора."""
        result = await self._session.execute(
            select(versions)
            .where(versions.c[key].in_(select(versions.c[key]).where(condition)))
            .ext(distinct_on(versions.c[key]))
            .order_by(versions.c[key], versions.c.number.desc())
        )
        return list(result)
