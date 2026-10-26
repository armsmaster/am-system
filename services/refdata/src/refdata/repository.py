"""Чтение и запись справочника в БД."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Row, delete, extract, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from refdata.db import instrument, instrument_version, sync_run, trading_day
from refdata.domain import InstrumentKind, Params, Version


class NotFoundError(Exception):
    def __init__(self, what: str, key: object) -> None:
        super().__init__(f"{what} {key} not found")
        self.what = what
        self.key = key


@dataclass(frozen=True, slots=True)
class InstrumentRecord:
    id: UUID
    secid: str
    isin: str | None
    kind: InstrumentKind
    name: str
    short_name: str
    added_by: str
    added_at: datetime
    iss_snapshot: Params | None
    last_synced_at: datetime | None
    last_sync_error: str | None


@dataclass(frozen=True, slots=True)
class CalendarDay:
    day: date
    is_trading: bool
    comment: str | None


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- инструменты ------------------------------------------------------------

    async def find_by_code(self, code: str) -> InstrumentRecord | None:
        wanted = code.strip().upper()
        row = (
            await self._session.execute(
                select(instrument).where(
                    or_(
                        func.upper(instrument.c.secid) == wanted,
                        func.upper(instrument.c.isin) == wanted,
                    )
                )
            )
        ).one_or_none()
        return None if row is None else _record(row)

    async def get(self, instrument_id: UUID, *, lock: bool = False) -> InstrumentRecord:
        query = select(instrument).where(instrument.c.id == instrument_id)
        if lock:
            # Версии бумаги нумеруются по порядку — правки одной бумаги идут по очереди.
            query = query.with_for_update()
        row = (await self._session.execute(query)).one_or_none()
        if row is None:
            raise NotFoundError("instrument", instrument_id)
        return _record(row)

    async def search(self, query: str | None, limit: int = 200) -> list[InstrumentRecord]:
        statement = select(instrument).order_by(instrument.c.secid).limit(limit)
        if query:
            pattern = f"%{query.strip()}%"
            statement = statement.where(
                or_(
                    instrument.c.secid.ilike(pattern),
                    instrument.c.isin.ilike(pattern),
                    instrument.c.name.ilike(pattern),
                    instrument.c.short_name.ilike(pattern),
                )
            )
        return [_record(row) for row in await self._session.execute(statement)]

    async def all_ids(self) -> list[UUID]:
        rows = await self._session.execute(select(instrument.c.id).order_by(instrument.c.secid))
        return [row.id for row in rows]

    async def insert(self, record: InstrumentRecord) -> bool:
        """Добавляет бумагу; False — такая уже есть (её успел добавить кто-то другой)."""
        result = await self._session.execute(
            insert(instrument)
            .values(
                id=record.id,
                secid=record.secid,
                isin=record.isin,
                kind=record.kind.value,
                name=record.name,
                short_name=record.short_name,
                added_by=record.added_by,
                iss_snapshot=_json(record.iss_snapshot),
                last_synced_at=record.last_synced_at,
            )
            .on_conflict_do_nothing(index_elements=[instrument.c.secid])
            .returning(instrument.c.id)
        )
        return result.one_or_none() is not None

    async def set_names(self, instrument_id: UUID, params: Params) -> None:
        await self._session.execute(
            update(instrument)
            .where(instrument.c.id == instrument_id)
            .values(name=params.name, short_name=params.short_name)
        )

    async def set_sync_state(
        self,
        instrument_id: UUID,
        *,
        synced_at: datetime | None,
        snapshot: Params | None,
        error: str | None,
    ) -> None:
        values: dict[str, Any] = {"last_sync_error": error}
        if snapshot is not None:
            values.update(iss_snapshot=_json(snapshot), last_synced_at=synced_at)
        await self._session.execute(
            update(instrument).where(instrument.c.id == instrument_id).values(**values)
        )

    # --- версии -----------------------------------------------------------------

    async def versions(self, instrument_id: UUID) -> list[Version]:
        rows = await self._session.execute(
            select(instrument_version)
            .where(instrument_version.c.instrument_id == instrument_id)
            .order_by(instrument_version.c.number)
        )
        return [_version(row) for row in rows]

    async def latest_version(self, instrument_id: UUID) -> Version:
        # У бумаги всегда есть хотя бы первая версия — она создаётся вместе с бумагой.
        return (await self.versions(instrument_id))[-1]

    async def add_version(self, instrument_id: UUID, version: Version) -> None:
        await self._session.execute(
            insert(instrument_version).values(
                instrument_id=instrument_id,
                number=version.number,
                valid_from=version.valid_from,
                source=version.source,
                author=version.author,
                comment=version.comment,
                created_at=version.created_at,
                pinned=sorted(version.pinned),
                params=_json(version.params),
            )
        )

    # --- календарь --------------------------------------------------------------

    async def calendar_exceptions(self, year: int | None = None) -> list[CalendarDay]:
        statement = select(trading_day).order_by(trading_day.c.day)
        if year is not None:
            statement = statement.where(extract("year", trading_day.c.day) == year)
        return [
            CalendarDay(row.day, row.is_trading, row.comment)
            for row in await self._session.execute(statement)
        ]

    async def calendar_day(self, day: date) -> CalendarDay | None:
        row = (
            await self._session.execute(select(trading_day).where(trading_day.c.day == day))
        ).one_or_none()
        return None if row is None else CalendarDay(row.day, row.is_trading, row.comment)

    async def set_calendar_day(self, entry: CalendarDay, updated_by: str) -> None:
        values = {
            "is_trading": entry.is_trading,
            "comment": entry.comment,
            "updated_by": updated_by,
        }
        await self._session.execute(
            insert(trading_day)
            .values(day=entry.day, **values)
            .on_conflict_do_update(
                index_elements=[trading_day.c.day], set_={**values, "updated_at": func.now()}
            )
        )

    async def clear_calendar_day(self, day: date) -> None:
        await self._session.execute(delete(trading_day).where(trading_day.c.day == day))

    # --- ежедневная синхронизация -----------------------------------------------

    async def claim_sync_run(self, run_date: date, now: datetime, retry_after: timedelta) -> bool:
        """Забирает сегодняшнюю синхронизацию.

        Через ``retry_after`` после начала запуск забирается заново, если он
        не закончился (экземпляр упал посередине) или закончился с ошибками
        (ISS не ответил по части бумаг).
        """
        statement = (
            insert(sync_run)
            .values(run_date=run_date, started_at=now)
            .on_conflict_do_update(
                index_elements=[sync_run.c.run_date],
                set_={"started_at": now, "finished_at": None},
                where=(sync_run.c.finished_at.is_(None) | (sync_run.c.failed > 0))
                & (sync_run.c.started_at < now - retry_after),
            )
            .returning(sync_run.c.run_date)
        )
        return (await self._session.execute(statement)).one_or_none() is not None

    async def finish_sync_run(
        self, run_date: date, now: datetime, checked: int, changed: int, failed: int
    ) -> None:
        await self._session.execute(
            update(sync_run)
            .where(sync_run.c.run_date == run_date)
            .values(finished_at=now, checked=checked, changed=changed, failed=failed)
        )


def _json(params: Params | None) -> dict[str, Any] | None:
    return None if params is None else params.model_dump(mode="json")


def _record(row: Row[Any]) -> InstrumentRecord:
    return InstrumentRecord(
        id=row.id,
        secid=row.secid,
        isin=row.isin,
        kind=InstrumentKind(row.kind),
        name=row.name,
        short_name=row.short_name,
        added_by=row.added_by,
        added_at=row.added_at,
        iss_snapshot=None if row.iss_snapshot is None else Params.model_validate(row.iss_snapshot),
        last_synced_at=row.last_synced_at,
        last_sync_error=row.last_sync_error,
    )


def _version(row: Row[Any]) -> Version:
    return Version(
        number=row.number,
        valid_from=row.valid_from,
        source=row.source,
        author=row.author,
        comment=row.comment,
        created_at=row.created_at,
        pinned=frozenset(row.pinned),
        params=Params.model_validate(row.params),
    )
