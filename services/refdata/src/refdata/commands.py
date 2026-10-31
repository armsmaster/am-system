"""Изменения справочника: загрузка бумаги, правка, синхронизация с ISS, календарь.

Запрос к ISS — до транзакции: сеть бывает медленной, а держать открытой
транзакцию во время сетевого запроса нельзя. Изменение и событие для журнала
записываются одной транзакцией (outbox).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from am_common.clock import moscow_now
from am_common.db import Database
from am_common.events import Actor, Event
from am_common.outbox import Outbox
from refdata.db import outbox
from refdata.domain import (
    FIELD_LABELS,
    RuleViolationError,
    Version,
    apply_edit,
    changed_fields,
    check_new_version_date,
    issue_terms,
    journal_view,
    merge_from_iss,
)
from refdata.iss import IssClient, IssError
from refdata.repository import CalendarDay, InstrumentRecord, Repository

SERVICE = "refdata"
SYNC_AUTHOR = "moex-iss"


@dataclass(frozen=True, slots=True)
class Loaded:
    instrument_id: UUID
    created: bool  # False — бумага уже была в справочнике


class Commands:
    def __init__(
        self,
        session: AsyncSession,
        actor: Actor,
        *,
        clock: Callable[[], datetime] = moscow_now,
    ) -> None:
        self._session = session
        self._actor = actor
        self._clock = clock
        self._repo = Repository(session)
        self._outbox = Outbox(outbox)

    # --- бумаги -----------------------------------------------------------------

    async def load(self, code: str, iss: IssClient) -> Loaded:
        """Загружает бумагу из ISS по коду биржи или ISIN."""
        existing = await self._repo.find_by_code(code)
        await self._session.commit()
        if existing is not None:
            return Loaded(existing.id, created=False)

        security = await iss.find(code)
        now = self._clock()
        params = issue_terms(security.params, now.date())
        record = InstrumentRecord(
            id=uuid4(),
            secid=security.secid,
            isin=security.isin,
            kind=security.kind,
            name=params.name,
            short_name=params.short_name,
            added_by=self._actor.username,
            added_at=now,
            iss_snapshot=params,
            last_synced_at=now,
            last_sync_error=None,
        )
        async with self._session.begin():
            if not await self._repo.insert(record):
                # Пока шёл запрос к ISS, ту же бумагу загрузил кто-то другой.
                found = await self._repo.find_by_code(security.secid)
                return Loaded(found.id if found else record.id, created=False)
            await self._repo.add_version(
                record.id,
                Version(
                    number=1,
                    valid_from=None,
                    source="iss",
                    author=self._actor.username,
                    comment="Загружена из MOEX ISS",
                    created_at=now,
                    params=params,
                ),
            )
            await self._emit(
                "instrument.loaded",
                record,
                {"after": journal_view(params)},
            )
        return Loaded(record.id, created=True)

    async def edit(
        self,
        instrument_id: UUID,
        *,
        valid_from: date,
        comment: str,
        changes: Mapping[str, Any],
        unpin: Sequence[str],
    ) -> Version:
        """Ручная правка сопровождением: новая версия с закреплёнными полями."""
        async with self._session.begin():
            record = await self._repo.get(instrument_id, lock=True)
            latest = await self._repo.latest_version(instrument_id)
            check_new_version_date(latest, valid_from)
            params, pinned = apply_edit(latest, changes, unpin, record.iss_snapshot)
            if params == latest.params and pinned == latest.pinned:
                raise RuleViolationError("no_changes", "Изменений нет — новая версия не нужна.")
            version = Version(
                number=latest.number + 1,
                valid_from=valid_from,
                source="manual",
                author=self._actor.username,
                comment=comment,
                created_at=self._clock(),
                pinned=pinned,
                params=params,
            )
            await self._save_version(record, latest, version)
        return version

    async def sync(self, instrument_id: UUID, iss: IssClient) -> bool:
        """Сверяет бумагу с ISS. True — появилась новая версия."""
        async with self._session.begin():
            record = await self._repo.get(instrument_id)
        try:
            security = await iss.find(record.secid)
        except IssError as exc:
            async with self._session.begin():
                await self._repo.set_sync_state(
                    instrument_id, synced_at=None, snapshot=None, error=str(exc)
                )
            raise

        now = self._clock()
        from_iss = issue_terms(security.params, now.date())
        async with self._session.begin():
            record = await self._repo.get(instrument_id, lock=True)
            latest = await self._repo.latest_version(instrument_id)
            merged = merge_from_iss(latest, from_iss)
            changed = changed_fields(latest.params, merged)
            if changed:
                # Строчная — только первая буква: аббревиатуры вроде ISS остаются как есть.
                labels = ", ".join(_lower_first(FIELD_LABELS[name]) for name in changed)
                # ISS не сообщает дату публикации — новая версия действует с дня,
                # когда изменение заметили (решение 13, опоздание не больше суток).
                today = now.date()
                valid_from = max(today, latest.valid_from) if latest.valid_from else today
                version = Version(
                    number=latest.number + 1,
                    valid_from=valid_from,
                    source="iss",
                    author=SYNC_AUTHOR,
                    comment=f"Синхронизация с MOEX ISS: изменено — {labels}",
                    created_at=now,
                    pinned=latest.pinned,
                    params=merged,
                )
                await self._save_version(record, latest, version)
            await self._repo.set_sync_state(
                instrument_id, synced_at=now, snapshot=from_iss, error=None
            )
        return bool(changed)

    async def _save_version(
        self, record: InstrumentRecord, latest: Version, version: Version
    ) -> None:
        await self._repo.add_version(record.id, version)
        await self._repo.set_names(record.id, version.params)
        changed = changed_fields(latest.params, version.params)
        await self._emit(
            "instrument.version_added",
            record,
            {
                "version": version.number,
                "valid_from": None
                if version.valid_from is None
                else version.valid_from.isoformat(),
                "source": version.source,
                "before": journal_view(latest.params, changed),
                "after": journal_view(version.params, changed),
                "pinned": sorted(version.pinned),
            },
            comment=version.comment,
        )

    # --- календарь --------------------------------------------------------------

    async def set_calendar_day(self, day: date, is_trading: bool, comment: str | None) -> None:
        async with self._session.begin():
            before = await self._repo.calendar_day(day)
            entry = CalendarDay(day, is_trading, comment)
            await self._repo.set_calendar_day(entry, self._actor.username)
            await self._emit_calendar("calendar.day_set", day, before, entry)

    async def clear_calendar_day(self, day: date) -> None:
        async with self._session.begin():
            before = await self._repo.calendar_day(day)
            if before is None:
                raise RuleViolationError(
                    "no_exception", "Для этой даты нет исключения в календаре."
                )
            await self._repo.clear_calendar_day(day)
            await self._emit_calendar("calendar.day_cleared", day, before, None)

    # --- журнал -----------------------------------------------------------------

    async def _emit(
        self,
        event_type: str,
        record: InstrumentRecord,
        data: dict[str, Any],
        comment: str | None = None,
    ) -> None:
        await self._outbox.add(
            self._session,
            Event(
                service=SERVICE,
                type=event_type,
                actor=self._actor,
                entity_type="instrument",
                entity_id=str(record.id),
                data={"instrument": {"secid": record.secid, "isin": record.isin}, **data},
                comment=comment,
            ),
        )

    async def _emit_calendar(
        self, event_type: str, day: date, before: CalendarDay | None, after: CalendarDay | None
    ) -> None:
        def state(entry: CalendarDay | None) -> dict[str, Any] | None:
            if entry is None:
                return None
            return {"is_trading": entry.is_trading, "comment": entry.comment}

        await self._outbox.add(
            self._session,
            Event(
                service=SERVICE,
                type=event_type,
                actor=self._actor,
                entity_type="calendar",
                entity_id=day.isoformat(),
                data={"day": day.isoformat(), "before": state(before), "after": state(after)},
            ),
        )


@dataclass(frozen=True, slots=True)
class SyncStats:
    checked: int
    changed: int
    failed: int


async def sync_all(
    database: Database,
    iss: IssClient,
    actor: Actor,
    *,
    clock: Callable[[], datetime] = moscow_now,
) -> SyncStats:
    """Сверяет с ISS все бумаги справочника. Ошибка одной бумаги не мешает остальным."""
    async with database.session() as session:
        ids = await Repository(session).all_ids()
    changed = failed = 0
    for instrument_id in ids:
        async with database.session() as session:
            try:
                changed += await Commands(session, actor, clock=clock).sync(instrument_id, iss)
            except IssError:
                failed += 1
    return SyncStats(checked=len(ids), changed=changed, failed=failed)


def _lower_first(label: str) -> str:
    return label[:1].lower() + label[1:]
