from __future__ import annotations

import httpx2
from fastapi import FastAPI

from am_common.app import Resources, Worker, create_app
from am_common.consumer import StreamConsumer
from am_common.logs import configure_logging
from audit.api import router
from audit.settings import AuditSettings
from audit.store import AuditStore


def audit_consumer(settings: AuditSettings) -> Worker:
    """Фоновая задача: события из потоков сервисов → журнал."""

    async def audit_consumer(resources: Resources) -> None:
        store = AuditStore(resources.require_database())
        consumer = StreamConsumer(
            resources.require_redis(),
            streams=settings.streams,
            group=settings.consumer_group,
            consumer=settings.consumer_name,
            handler=store.save,
        )
        await consumer.run()

    return audit_consumer


def build_app(
    settings: AuditSettings | None = None,
    *,
    http_transport: httpx2.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or AuditSettings()
    return create_app(
        settings,
        routers=[router],
        workers=[audit_consumer(settings)],
        title="Audit service",
        http_transport=http_transport,
    )


def serve() -> FastAPI:
    """Точка входа процесса: ``uvicorn --factory audit.main:serve``."""
    settings = AuditSettings()
    configure_logging(settings.service_name, settings.log_level)
    return build_app(settings)
