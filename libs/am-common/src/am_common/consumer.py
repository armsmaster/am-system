"""Потребитель событий из Redis Streams (группа потребителей).

Каждое сообщение передаётся обработчику и подтверждается (XACK) только
после успешной обработки. Если обработчик упал или процесс умер,
сообщение остаётся неподтверждённым и через ``claim_idle_ms`` забирается
повторно — этим или другим экземпляром сервиса.

Обработчик обязан быть идемпотентным: одно событие может прийти дважды.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

from am_common.events import Event

logger = logging.getLogger(__name__)

Handler = Callable[[Event], Awaitable[None]]
# Сообщения потока: [(id, {поле: значение}), ...].
type Messages = list[tuple[str, dict[str, Any]]]


class StreamConsumer:
    def __init__(
        self,
        redis: Redis,
        *,
        streams: Sequence[str],
        group: str,
        consumer: str,
        handler: Handler,
        batch_size: int = 100,
        # Меньше таймаута сокета клиента Redis (5 с по умолчанию в redis-py 8): иначе
        # ожидание новых сообщений обрывается ошибкой чтения и паузой перед повтором.
        block_ms: int = 2_000,
        claim_idle_ms: int = 30_000,
        retry_interval: float = 5.0,
    ) -> None:
        if not streams:
            raise ValueError("at least one stream is required")
        self._redis = redis
        self._streams = list(streams)
        self._group = group
        self._consumer = consumer
        self._handler = handler
        self._batch_size = batch_size
        self._block_ms = block_ms
        self._claim_idle_ms = claim_idle_ms
        self._retry_interval = retry_interval

    async def ensure_groups(self) -> None:
        """Создаёт группу потребителей; новая группа читает поток с начала."""
        for stream in self._streams:
            try:
                await self._redis.xgroup_create(stream, self._group, id="0", mkstream=True)
            except ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

    async def consume_once(self) -> int:
        """Одна итерация: зависшие сообщения, затем новые. Возвращает число обработанных."""
        handled = 0
        for stream in self._streams:
            # Ответ XAUTOCLAIM: [следующий id, сообщения, id удалённых сообщений].
            claimed = await self._redis.xautoclaim(
                stream,
                self._group,
                self._consumer,
                min_idle_time=self._claim_idle_ms,
                start_id="0-0",
                count=self._batch_size,
            )
            handled += await self._handle(stream, claimed[1])

        response = cast(
            "list[tuple[str, Messages]] | None",
            await self._redis.xreadgroup(
                self._group,
                self._consumer,
                dict.fromkeys(self._streams, ">"),
                count=self._batch_size,
                block=self._block_ms,
            ),
        )
        for stream, messages in response or []:
            handled += await self._handle(stream, messages)
        return handled

    async def run(self) -> None:
        """Бесконечный цикл; останавливается отменой задачи."""
        while True:
            try:
                await self.ensure_groups()
                while True:
                    await self.consume_once()
            except (RedisError, OSError) as exc:
                logger.warning("stream consumer failed, will retry: %s", exc)
                await asyncio.sleep(self._retry_interval)

    async def _handle(self, stream: str, messages: Messages) -> int:
        handled = 0
        for message_id, fields in messages:
            try:
                event = Event.model_validate_json(fields["event"])
            except (KeyError, ValidationError):
                # Такое сообщение нельзя обработать никогда: фиксируем в логе
                # и подтверждаем, чтобы оно не блокировало повторы.
                logger.error("malformed message %s in %s: %r", message_id, stream, fields)
                await self._redis.xack(stream, self._group, message_id)
                continue
            try:
                await self._handler(event)
            except Exception:
                logger.exception("failed to handle event %s from %s", event.event_id, stream)
                continue
            await self._redis.xack(stream, self._group, message_id)
            handled += 1
        return handled
