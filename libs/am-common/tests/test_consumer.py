"""Потребитель событий — с настоящим Redis."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from am_common.consumer import StreamConsumer
from am_common.events import Actor, Event

pytestmark = pytest.mark.integration

GROUP = "audit"


def make_event(n: int) -> Event:
    return Event(
        service="testsvc",
        type="thing.created",
        actor=Actor(subject="u-1", username="ivanov"),
        entity_type="thing",
        entity_id=str(n),
    )


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def stream() -> str:
    return f"am.events.test-{uuid.uuid4().hex[:8]}"


class Recorder:
    """Обработчик, который запоминает события и может упасть заданное число раз."""

    def __init__(self, failures: int = 0) -> None:
        self.events: list[Event] = []
        self.failures = failures

    async def __call__(self, event: Event) -> None:
        if self.failures:
            self.failures -= 1
            raise RuntimeError("handler failed")
        self.events.append(event)


def consumer(redis: Redis, stream: str, handler: Recorder, **options: int) -> StreamConsumer:
    return StreamConsumer(
        redis,
        streams=[stream],
        group=GROUP,
        consumer="test",
        handler=handler,
        block_ms=options.pop("block_ms", 100),
        **options,
    )


async def publish(redis: Redis, stream: str, event: Event) -> None:
    await redis.xadd(stream, {"event": event.model_dump_json()})


async def pending(redis: Redis, stream: str) -> int:
    info = await redis.xpending(stream, GROUP)
    return int(info["pending"])


async def test_delivers_events_and_acknowledges_them(redis: Redis, stream: str) -> None:
    handler = Recorder()
    events = [make_event(1), make_event(2)]
    subject = consumer(redis, stream, handler)
    await subject.ensure_groups()
    for event in events:
        await publish(redis, stream, event)

    assert await subject.consume_once() == 2
    assert handler.events == events
    assert await pending(redis, stream) == 0


async def test_new_group_reads_the_stream_from_the_beginning(redis: Redis, stream: str) -> None:
    await publish(redis, stream, make_event(1))
    handler = Recorder()
    subject = consumer(redis, stream, handler)

    await subject.ensure_groups()
    await subject.ensure_groups()  # повторное создание группы безопасно
    await subject.consume_once()

    assert [event.entity_id for event in handler.events] == ["1"]


async def test_failed_event_is_retried_later(redis: Redis, stream: str) -> None:
    handler = Recorder(failures=1)
    subject = consumer(redis, stream, handler, claim_idle_ms=0)
    await subject.ensure_groups()
    await publish(redis, stream, make_event(1))

    assert await subject.consume_once() == 0
    assert await pending(redis, stream) == 1

    assert await subject.consume_once() == 1
    assert [event.entity_id for event in handler.events] == ["1"]
    assert await pending(redis, stream) == 0


@pytest.mark.parametrize("fields", [{"event": "not json"}, {"other": "field"}])
async def test_malformed_message_is_dropped_with_an_error(
    redis: Redis, stream: str, fields: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    handler = Recorder()
    subject = consumer(redis, stream, handler)
    await subject.ensure_groups()
    await redis.xadd(stream, dict(fields))  # type: ignore[arg-type]

    assert await subject.consume_once() == 0
    assert handler.events == []
    assert await pending(redis, stream) == 0
    assert "malformed message" in caplog.text


async def test_unexpected_redis_error_is_not_swallowed(redis: Redis, stream: str) -> None:
    await redis.set(stream, "not a stream")
    subject = consumer(redis, stream, Recorder())

    with pytest.raises(ResponseError, match="WRONGTYPE"):
        await subject.ensure_groups()
    await redis.delete(stream)


def test_needs_at_least_one_stream(redis: Redis) -> None:
    with pytest.raises(ValueError, match="at least one stream"):
        StreamConsumer(redis, streams=[], group=GROUP, consumer="test", handler=Recorder())


async def test_run_processes_events_until_cancelled(redis: Redis, stream: str) -> None:
    handler = Recorder()
    task = asyncio.create_task(consumer(redis, stream, handler).run())

    await publish(redis, stream, make_event(1))
    for _ in range(200):
        if handler.events:
            break
        await asyncio.sleep(0.02)
    task.cancel()

    assert [event.entity_id for event in handler.events] == ["1"]


async def test_run_survives_redis_outage(stream: str) -> None:
    dead_redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.1)
    task = asyncio.create_task(consumer(dead_redis, stream, Recorder(), retry_interval=0).run())

    await asyncio.sleep(0.5)

    assert not task.done()
    task.cancel()
    await dead_redis.aclose()
