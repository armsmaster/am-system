from __future__ import annotations

from datetime import time

from am_common.settings import ServiceSettings


class RefdataSettings(ServiceSettings):
    service_name: str = "refdata"
    database_schema: str | None = "refdata"

    # Открытая часть MOEX ISS (решение 61).
    iss_url: str = "https://iss.moex.com/iss"
    iss_timeout: float = 20.0
    # Ежедневная синхронизация — по Москве, до начала торгов.
    sync_at: time = time(6, 0)
    sync_check_seconds: float = 300.0
