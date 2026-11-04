from __future__ import annotations

from am_common.settings import ServiceSettings


class LimitsSettings(ServiceSettings):
    service_name: str = "limits"
    database_schema: str | None = "limits"

    # Соседей читаем с токеном пользователя: сервис портфелей отдаёт ровно те фонды,
    # что тот видит, сервис сделок проверяет доступ сам.
    portfolio_url: str = "http://portfolio:8000"
    booking_url: str = "http://booking:8000"
    refdata_url: str = "http://refdata:8000"
