"""Хранилище журнала — с настоящим PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from am_common.db import Database
from am_common.events import Actor, Event
from am_common.migrations import schema_diff
from audit.db import metadata
from audit.settings import AuditSettings
from audit.store import AuditStore, EventFilter

pytestmark = pytest.mark.integration

IVANOV = Actor(subject="u-ivanov", username="ivanov")
PETROV = Actor(subject="u-petrov", username="petrov")
START = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def event(minute: int, *, actor: Actor = IVANOV, entity_id: str = "p-1", **fields: str) -> Event:
    return Event(
        occurred_at=START + timedelta(minutes=minute),
        service="portfolio",
        type=fields.get("type", "portfolio.updated"),
        actor=actor,
        entity_type=fields.get("entity_type", "portfolio"),
        entity_id=entity_id,
        data={"after": {"minute": minute}},
    )


async def test_schema_matches_the_model(settings: AuditSettings) -> None:
    assert settings.database_url is not None
    assert settings.database_schema is not None

    diff = await schema_diff(
        settings.database_url.get_secret_value(), settings.database_schema, metadata
    )

    assert diff == []


async def test_saved_event_reads_back_unchanged(database: Database) -> None:
    store = AuditStore(database)
    original = event(1).model_copy(update={"comment": "исправление"})

    await store.save(original)

    assert await store.find(EventFilter(), limit=10) == [original]


async def test_repeated_delivery_is_stored_once(database: Database) -> None:
    store = AuditStore(database)
    original = event(1)

    await store.save(original)
    await store.save(original)

    assert len(await store.find(EventFilter(), limit=10)) == 1


async def test_newest_first_with_pages(database: Database) -> None:
    store = AuditStore(database)
    events = [event(minute) for minute in range(5)]
    for item in events:
        await store.save(item)

    first = await store.find(EventFilter(), limit=2)
    second = await store.find(EventFilter(), limit=2, before=first[-1].event_id)
    unknown = await store.find(EventFilter(), limit=2, before=event(99).event_id)

    assert first == [events[4], events[3]]
    assert second == [events[2], events[1]]
    assert unknown == []


async def test_filters(database: Database) -> None:
    store = AuditStore(database)
    by_petrov = event(1, actor=PETROV)
    other_entity = event(2, entity_id="p-2")
    account = event(3, entity_type="account", type="account.created")
    plain = event(4)
    for item in (by_petrov, other_entity, account, plain):
        await store.save(item)

    async def find(**criteria: object) -> list[Event]:
        return await store.find(EventFilter(**criteria), limit=10)  # type: ignore[arg-type]

    assert await find(actor="u-petrov") == [by_petrov]
    assert await find(entity_type="portfolio", entity_id="p-2") == [other_entity]
    assert await find(type="account.created") == [account]
    assert await find(since=START + timedelta(minutes=3)) == [plain, account]
    assert await find(until=START + timedelta(minutes=2)) == [by_petrov]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_event SET comment = 'подчистка'",
        "DELETE FROM audit_event",
        "TRUNCATE audit_event",
    ],
)
async def test_journal_cannot_be_changed(database: Database, statement: str) -> None:
    await AuditStore(database).save(event(1))

    with pytest.raises(DBAPIError, match="append-only"):
        async with database.session() as session, session.begin():
            await session.execute(text(statement))

    assert len(await AuditStore(database).find(EventFilter(), limit=10)) == 1
