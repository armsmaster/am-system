from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from am_common.app import AppResources
from am_common.auth import Role, require_any_role
from am_common.events import Event
from audit.store import AuditStore, EventFilter

# Журнал видит только администратор.
router = APIRouter(dependencies=[Depends(require_any_role(Role.ADMIN))])


class EventPage(BaseModel):
    items: list[Event]
    # Передать как ``before``, чтобы получить следующую страницу; нет — это всё.
    next_before: UUID | None


@router.get("/events", tags=["events"])
async def events(
    *,
    resources: AppResources,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor: Annotated[str | None, Query(description="subject пользователя")] = None,
    event_type: Annotated[str | None, Query(alias="type")] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> EventPage:
    """События журнала, новые сверху."""
    store = AuditStore(resources.require_database())
    criteria = EventFilter(
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        type=event_type,
        since=since,
        until=until,
    )
    # Берём на одно больше, чтобы понять, есть ли следующая страница.
    found = await store.find(criteria, limit=limit + 1, before=before)
    items = found[:limit]
    return EventPage(items=items, next_before=items[-1].event_id if len(found) > limit else None)
