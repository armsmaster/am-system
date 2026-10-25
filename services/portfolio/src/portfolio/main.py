from __future__ import annotations

import httpx2
from fastapi import FastAPI

from am_common.app import create_app
from am_common.logs import configure_logging
from portfolio.api import router
from portfolio.settings import PortfolioSettings


def build_app(
    settings: PortfolioSettings | None = None,
    *,
    http_transport: httpx2.AsyncBaseTransport | None = None,
) -> FastAPI:
    return create_app(
        settings or PortfolioSettings(),
        routers=[router],
        title="Portfolio service",
        http_transport=http_transport,
    )


def serve() -> FastAPI:
    """Точка входа процесса: ``uvicorn --factory portfolio.main:serve``."""
    settings = PortfolioSettings()
    configure_logging(settings.service_name, settings.log_level)
    return build_app(settings)
