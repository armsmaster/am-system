"""Transactional outbox: надёжная доставка событий из сервиса в шину.

Сервис пишет событие в свою таблицу ``outbox`` в той же транзакции, что
и изменение данных, — значит, событие есть тогда и только тогда, когда
изменение зафиксировано. Ретранслятор публикует неотправленные события
в Redis Streams и помечает их отправленными.

Ретранслятор может упасть между публикацией и пометкой — тогда событие
уйдёт повторно. Поэтому получатели обязаны быть идемпотентными (у каждого
события уникальный ``event_id``).
"""

from __future__ import annotations

import asyncio
import json
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Identity,
    Index,
    MetaData,
    Table,
    Text,
    Uuid,
    func,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from am_common.app import Resources, Worker
from am_common.db import Database
from am_common.events import Event, stream_name

logger = logging.getLogger(__name__)


def outbox_table(metadata: MetaData) -> Table:
    """Таблица outbox в схеме сервиса. Создаётся миграцией сервиса."""
    return Table(
        "outbox",
        metadata,
        Column("id", BigInteger, Identity(), primary_key=True),
        Column("event_id", Uuid, nullable=False, unique=True),
        Column("stream", Text, nullable=False),
        Column("payload", JSONB, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("published_at", DateTime(timezone=True)),
        Index("outbox_unpublished", "id", postgresql_where=text("published_at IS NULL")),
    )


class Outbox:
    def __init__(self, table: Table) -> None:
        self._table = table

    async def add(self, session: AsyncSession, event: Event) -> None:
        """Добавляет событие в текущую транзакцию сессии."""
        await session.execute(
            insert(self._table).values(
                event_id=event.event_id,
                stream=stream_name(event.service),
                payload=event.model_dump(mode="json"),
            )
        )


class OutboxRelay:
    """Публикует неотправленные события из outbox в Redis Streams."""

    def __init__(
        self,
        database: Database,
        table: Table,
        redis: Redis,
        *,
        batch_size: int = 100,
        poll_interval: float = 0.5,
        retry_interval: float = 5.0,
    ) -> None:
        self._database = database
        self._table = table
        self._redis = redis
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._retry_interval = retry_interval

    async def publish_batch(self) -> int:
        """Публикует одну пачку; возвращает число опубликованных событий."""
        table = self._table
        async with self._database.session() as session, session.begin():
            rows = (
                await session.execute(
                    select(table.c.id, table.c.stream, table.c.payload)
                    .where(table.c.published_at.is_(None))
                    .order_by(table.c.id)
                    .limit(self._batch_size)
                    # Реплики сервиса не публикуют одно и то же одновременно.
                    .with_for_update(skip_locked=True)
                )
            ).all()
            if not rows:
                return 0

            async with self._redis.pipeline(transaction=False) as pipe:
                for row in rows:
                    pipe.xadd(row.stream, {"event": json.dumps(row.payload, ensure_ascii=False)})
                await pipe.execute()

            await session.execute(
                update(table)
                .where(table.c.id.in_([row.id for row in rows]))
                .values(published_at=func.now())
            )
        return len(rows)

    async def run(self) -> None:
        """Бесконечный цикл; останавливается отменой задачи."""
        while True:
            try:
                published = await self.publish_batch()
            except (RedisError, SQLAlchemyError, OSError) as exc:
                logger.warning("outbox relay failed, will retry: %s", exc)
                await asyncio.sleep(self._retry_interval)
                continue
            if published < self._batch_size:
                await asyncio.sleep(self._poll_interval)


def relay_worker(table: Table) -> Worker:
    """Фоновая задача сервиса: ретранслятор его outbox."""

    async def outbox_relay(resources: Resources) -> None:
        relay = OutboxRelay(resources.require_database(), table, resources.require_redis())
        await relay.run()

    return outbox_relay
