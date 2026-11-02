from __future__ import annotations

import socket

from pydantic import Field

from am_common.events import stream_name
from am_common.settings import ServiceSettings

# Сервисы, чьи события попадают в журнал. Новый сервис — новая строка.
AUDITED_SERVICES = ("portfolio", "refdata", "marketdata", "booking", "reconciliation")


class AuditSettings(ServiceSettings):
    service_name: str = "audit"
    database_schema: str | None = "audit"

    streams: list[str] = Field(
        default_factory=lambda: [stream_name(service) for service in AUDITED_SERVICES]
    )
    consumer_group: str = "audit"
    # Имя экземпляра в группе потребителей: у каждой реплики своё.
    consumer_name: str = Field(default_factory=socket.gethostname)
