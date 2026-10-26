from __future__ import annotations

import httpx2
from fastapi import FastAPI

from am_common.app import create_app
from am_common.logs import configure_logging
from am_common.outbox import relay_worker
from portfolio.api import install_error_handlers, router
from portfolio.db import outbox
from portfolio.settings import PortfolioSettings


def build_app(
    settings: PortfolioSettings | None = None,
    *,
    http_transport: httpx2.AsyncBaseTransport | None = None,
) -> FastAPI:
    app = create_app(
        settings or PortfolioSettings(),
        routers=[router],
        workers=[relay_worker(outbox)],
        title="Portfolio service",
        http_transport=http_transport,
    )
    install_error_handlers(app)
    return app


def serve() -> FastAPI:
    """Точка входа процесса: ``uvicorn --factory portfolio.main:serve``."""
    settings = PortfolioSettings()
    configure_logging(settings.service_name, settings.log_level)
    return build_app(settings)
