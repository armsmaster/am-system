from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import httpx2
from fastapi import FastAPI

from am_common.app import create_app
from am_common.clock import moscow_now
from am_common.logs import configure_logging
from am_common.outbox import relay_worker
from refdata.api import install_error_handlers, router
from refdata.db import outbox
from refdata.scheduler import daily_sync_worker
from refdata.settings import RefdataSettings


def build_app(
    settings: RefdataSettings | None = None,
    *,
    http_transport: httpx2.AsyncBaseTransport | None = None,
    clock: Callable[[], datetime] = moscow_now,
) -> FastAPI:
    """``clock`` — время для рейтингов и классификаторов: в тестах его подменяют."""
    settings = settings or RefdataSettings()
    app = create_app(
        settings,
        routers=[router],
        workers=[relay_worker(outbox), daily_sync_worker(settings)],
        title="Reference data service",
        http_transport=http_transport,
    )
    app.state.clock = clock
    install_error_handlers(app)
    return app


def serve() -> FastAPI:
    """Точка входа процесса: ``uvicorn --factory refdata.main:serve``."""
    settings = RefdataSettings()
    configure_logging(settings.service_name, settings.log_level)
    return build_app(settings)
