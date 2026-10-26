"""Хранилище журнала: запись событий и поиск по ним."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, Row, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from am_common.db import Database
from am_common.events import Actor, Event
from audit.db import audit_event


@dataclass(frozen=True, slots=True)
class EventFilter:
    entity_type: str | None = None
    entity_id: str | None = None
    actor: str | None = None
    type: str | None = None
    since: datetime | None = None
    until: datetime | None = None


class AuditStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def save(self, event: Event) -> None:
        """Сохраняет событие. Повтор того же события ничего не меняет."""
        async with self._database.session() as session, session.begin():
            await session.execute(
                insert(audit_event)
                .values(
                    event_id=event.event_id,
                    occurred_at=event.occurred_at,
                    service=event.service,
                    type=event.type,
                    actor_subject=event.actor.subject,
                    actor_username=event.actor.username,
                    entity_type=event.entity_type,
                    entity_id=event.entity_id,
                    data=event.data,
                    comment=event.comment,
                )
                .on_conflict_do_nothing(index_elements=[audit_event.c.event_id])
            )

    async def find(
        self, criteria: EventFilter, *, limit: int, before: UUID | None = None
    ) -> list[Event]:
        """События по условиям, новые сверху. ``before`` — продолжить после этого события."""
        c = audit_event.c
        conditions: list[ColumnElement[bool]] = []
        if criteria.entity_type is not None:
            conditions.append(c.entity_type == criteria.entity_type)
        if criteria.entity_id is not None:
            conditions.append(c.entity_id == criteria.entity_id)
        if criteria.actor is not None:
            conditions.append(c.actor_subject == criteria.actor)
        if criteria.type is not None:
            conditions.append(c.type == criteria.type)
        if criteria.since is not None:
            conditions.append(c.occurred_at >= criteria.since)
        if criteria.until is not None:
            conditions.append(c.occurred_at < criteria.until)

        async with self._database.session() as session:
            if before is not None:
                anchor = (
                    await session.execute(
                        select(c.occurred_at, c.event_id).where(c.event_id == before)
                    )
                ).one_or_none()
                if anchor is None:
                    return []
                conditions.append(
                    tuple_(c.occurred_at, c.event_id) < tuple_(anchor.occurred_at, anchor.event_id)
                )

            rows = await session.execute(
                select(audit_event)
                .where(*conditions)
                .order_by(c.occurred_at.desc(), c.event_id.desc())
                .limit(limit)
            )
            return [_to_event(row) for row in rows]


def _to_event(row: Row[tuple[object, ...]]) -> Event:
    return Event(
        event_id=row.event_id,
        occurred_at=row.occurred_at,
        service=row.service,
        type=row.type,
        actor=Actor(subject=row.actor_subject, username=row.actor_username),
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        data=row.data,
        comment=row.comment,
    )
