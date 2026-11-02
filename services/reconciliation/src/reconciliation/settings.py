from __future__ import annotations

from am_common.settings import ServiceSettings


class ReconciliationSettings(ServiceSettings):
    service_name: str = "reconciliation"
    database_schema: str | None = "reconciliation"

    # Соседей читаем с токеном пользователя или скрипта: сервис портфелей отдаёт
    # ровно те фонды и счета, что тот видит, сервис сделок проверяет доступ сам.
    portfolio_url: str = "http://portfolio:8000"
    booking_url: str = "http://booking:8000"
    refdata_url: str = "http://refdata:8000"
