from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import httpx2
from fastapi import FastAPI

from am_common.app import create_app
from am_common.clock import moscow_now
from am_common.logs import configure_logging
from am_common.outbox import relay_worker
from booking.api import install_error_handlers, router
from booking.db import outbox
from booking.recalc import recalc_worker, refdata_consumer
from booking.settings import BookingSettings


def build_app(
    settings: BookingSettings | None = None,
    *,
    http_transport: httpx2.AsyncBaseTransport | None = None,
    clock: Callable[[], datetime] = moscow_now,
) -> FastAPI:
    """``clock`` — время для API: в тестах его подменяют, в работе — Москва сейчас."""
    settings = settings or BookingSettings()
    background = (
        [recalc_worker(settings), refdata_consumer(settings)] if settings.background_jobs else []
    )
    app = create_app(
        settings,
        routers=[router],
        workers=[relay_worker(outbox), *background],
        title="Booking service",
        http_transport=http_transport,
    )
    app.state.clock = clock
    install_error_handlers(app)
    return app


def serve() -> FastAPI:
    """Точка входа процесса: ``uvicorn --factory booking.main:serve``."""
    settings = BookingSettings()
    configure_logging(settings.service_name, settings.log_level)
    return build_app(settings)
