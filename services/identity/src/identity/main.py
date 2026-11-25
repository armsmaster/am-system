"""Точка входа сервиса входа."""

from __future__ import annotations

import httpx2
from fastapi import FastAPI

from am_common.app import create_app
from am_common.logs import configure_logging
from am_common.outbox import relay_worker
from identity.api import install_error_handlers, public, router
from identity.db import outbox
from identity.settings import IdentitySettings


def build_app(
    settings: IdentitySettings | None = None,
    *,
    http_transport: httpx2.AsyncBaseTransport | None = None,
) -> FastAPI:
    app = create_app(
        settings or IdentitySettings(),
        routers=[public, router],
        workers=[relay_worker(outbox)],
        title="Identity service",
        http_transport=http_transport,
    )
    install_error_handlers(app)
    return app


def serve() -> FastAPI:
    """Фабрика для uvicorn: настройки из окружения, логи в JSON."""
    settings = IdentitySettings()
    configure_logging(settings.service_name, settings.log_level)
    return build_app(settings)
