"""Outbox и ретранслятор — с настоящими PostgreSQL и Redis."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import cast

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import MetaData, Table, func, select

from am_common.db import Database
from am_common.events import Actor, Event, stream_name
from am_common.outbox import Outbox, OutboxRelay, outbox_table

pytestmark = pytest.mark.integration

ACTOR = Actor(subject="u-1", username="ivanov")


def make_event(n: int) -> Event:
    return Event(
        service="testsvc",
        type="thing.created",
        actor=ACTOR,
        entity_type="thing",
        entity_id=str(n),
        data={"after": {"name": f"Вещь {n}"}},
    )


@pytest.fixture
async def database(postgres_url: str, schema: str) -> AsyncIterator[Database]:
    db = Database(postgres_url, schema)
    yield db
    await db.dispose()


@pytest.fixture
async def table(database: Database) -> Table:
    metadata = MetaData()
    table = outbox_table(metadata)
    async with database.engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    return table


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url, decode_responses=True)
    await client.delete(stream_name("testsvc"))
    yield client
    await client.aclose()


async def add_events(database: Database, table: Table, *events: Event) -> None:
    outbox = Outbox(table)
    async with database.session() as session, session.begin():
        for event in events:
            await outbox.add(session, event)


async def read(redis: Redis) -> list[dict[str, str]]:
    """Поля всех сообщений потока сервиса, по порядку."""
    entries = cast("list[tuple[str, dict[str, str]]]", await redis.xrange(stream_name("testsvc")))
    return [fields for _id, fields in entries]


async def unpublished(database: Database, table: Table) -> int:
    async with database.session() as session:
        query = select(func.count()).where(table.c.published_at.is_(None))
        return (await session.execute(query)).scalar_one()


async def test_event_is_written_only_with_the_transaction(database: Database, table: Table) -> None:
    async with database.session() as session:
        await Outbox(table).add(session, make_event(1))
        await session.rollback()

    assert await unpublished(database, table) == 0

    await add_events(database, table, make_event(2))

    assert await unpublished(database, table) == 1


async def test_relay_publishes_events_in_order_and_marks_them(
    database: Database, table: Table, redis: Redis
) -> None:
    events = [make_event(n) for n in range(3)]
    await add_events(database, table, *events)
    relay = OutboxRelay(database, table, redis)

    assert await relay.publish_batch() == 3
    assert await relay.publish_batch() == 0

    published = [Event.model_validate_json(fields["event"]) for fields in await read(redis)]
    assert published == events
    assert await unpublished(database, table) == 0


async def test_relay_publishes_in_batches(database: Database, table: Table, redis: Redis) -> None:
    await add_events(database, table, *(make_event(n) for n in range(3)))
    relay = OutboxRelay(database, table, redis, batch_size=2)

    assert await relay.publish_batch() == 2
    assert await relay.publish_batch() == 1


async def test_events_stay_in_outbox_while_redis_is_down(database: Database, table: Table) -> None:
    await add_events(database, table, make_event(1))
    dead_redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.1)
    relay = OutboxRelay(database, table, dead_redis)

    with pytest.raises(RedisError):
        await relay.publish_batch()

    assert await unpublished(database, table) == 1
    await dead_redis.aclose()


async def test_run_keeps_publishing(database: Database, table: Table, redis: Redis) -> None:
    # Пачка из одного события: полная пачка — сразу следующая, неполная — пауза.
    relay = OutboxRelay(database, table, redis, batch_size=1, poll_interval=0.01)
    task = asyncio.create_task(relay.run())

    await add_events(database, table, make_event(1), make_event(2))
    for _ in range(200):
        if await redis.xlen(stream_name("testsvc")) == 2:
            break
        await asyncio.sleep(0.02)
    task.cancel()

    assert [json.loads(fields["event"])["entity_id"] for fields in await read(redis)] == ["1", "2"]


async def test_run_survives_redis_outage(database: Database, table: Table) -> None:
    await add_events(database, table, make_event(1))
    dead_redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.1)
    relay = OutboxRelay(database, table, dead_redis, retry_interval=0.01)
    task = asyncio.create_task(relay.run())

    await asyncio.sleep(0.5)

    assert not task.done()
    task.cancel()
    await dead_redis.aclose()
