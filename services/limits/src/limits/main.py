from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import httpx2
from fastapi import FastAPI

from am_common.app import create_app
from am_common.clock import moscow_now
from am_common.logs import configure_logging
from am_common.outbox import relay_worker
from limits.api import install_error_handlers, router
from limits.db import outbox
from limits.settings import LimitsSettings


def build_app(
    settings: LimitsSettings | None = None,
    *,
    http_transport: httpx2.AsyncBaseTransport | None = None,
    clock: Callable[[], datetime] = moscow_now,
) -> FastAPI:
    """``clock`` — время для API: в тестах его подменяют, в работе — Москва сейчас."""
    settings = settings or LimitsSettings()
    app = create_app(
        settings,
        routers=[router],
        workers=[relay_worker(outbox)],
        title="Limits service",
        http_transport=http_transport,
    )
    app.state.clock = clock
    install_error_handlers(app)
    return app


def serve() -> FastAPI:
    """Точка входа процесса: ``uvicorn --factory limits.main:serve``."""
    settings = LimitsSettings()
    configure_logging(settings.service_name, settings.log_level)
    return build_app(settings)
