from __future__ import annotations

from am_common.settings import ServiceSettings


class PortfolioSettings(ServiceSettings):
    service_name: str = "portfolio"
    database_schema: str | None = "portfolio"
