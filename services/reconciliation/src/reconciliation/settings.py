from __future__ import annotations

from pydantic import SecretStr

from am_common.settings import ServiceSettings


class ReconciliationSettings(ServiceSettings):
    service_name: str = "reconciliation"
    database_schema: str | None = "reconciliation"

    # Соседей читаем с токеном пользователя или скрипта: сервис портфелей отдаёт
    # ровно те фонды и счета, что тот видит, сервис сделок проверяет доступ сам.
    portfolio_url: str = "http://portfolio:8000"
    booking_url: str = "http://booking:8000"
    refdata_url: str = "http://refdata:8000"

    # День закрывает сервис сверки под своей служебной учётной записью am-reconciliation
    # (роль close_day): сам пользователь закрыть день в booking не может (design/08).
    keycloak_url: str = "http://keycloak:8080/auth"
    keycloak_realm: str = "am"
    keycloak_client_id: str = "am-reconciliation"
    keycloak_client_secret: SecretStr | None = None
