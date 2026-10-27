from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import httpx2
from fastapi import FastAPI

from am_common.app import create_app
from am_common.clock import moscow_now
from am_common.logs import configure_logging
from am_common.outbox import relay_worker
from marketdata.api import install_error_handlers, install_feed_limit, router
from marketdata.db import outbox
from marketdata.scheduler import history_worker, instruments_worker, intraday_worker
from marketdata.settings import MarketdataSettings


def build_app(
    settings: MarketdataSettings | None = None,
    *,
    http_transport: httpx2.AsyncBaseTransport | None = None,
    clock: Callable[[], datetime] = moscow_now,
) -> FastAPI:
    """``clock`` — время для API: в тестах его подменяют, в работе — Москва сейчас."""
    settings = settings or MarketdataSettings()
    background = (
        [instruments_worker(settings), history_worker(settings), intraday_worker(settings)]
        if settings.background_jobs
        else []
    )
    app = create_app(
        settings,
        routers=[router],
        workers=[relay_worker(outbox), *background],
        title="Market data service",
        http_transport=http_transport,
    )
    app.state.clock = clock
    install_error_handlers(app)
    install_feed_limit(app, settings)
    return app


def serve() -> FastAPI:
    """Точка входа процесса: ``uvicorn --factory marketdata.main:serve``."""
    settings = MarketdataSettings()
    configure_logging(settings.service_name, settings.log_level)
    return build_app(settings)
