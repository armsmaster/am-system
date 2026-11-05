from __future__ import annotations

from pydantic import SecretStr

from am_common.settings import ServiceSettings


class LimitsSettings(ServiceSettings):
    service_name: str = "limits"
    database_schema: str | None = "limits"

    # Соседей читаем с токеном пользователя: сервис портфелей отдаёт ровно те фонды,
    # что тот видит, сервис сделок проверяет доступ сам.
    portfolio_url: str = "http://portfolio:8000"
    booking_url: str = "http://booking:8000"
    refdata_url: str = "http://refdata:8000"

    # Трейдер ведёт портфель фонда, а доступа к самому фонду нет: позиции, СЧА и сделки
    # фонда для проверки сделки до ввода сервис читает под учётной записью am-limits
    # (роль fund_reader, решение 147).
    keycloak_url: str = "http://keycloak:8080/auth"
    keycloak_realm: str = "am"
    keycloak_client_id: str = "am-limits"
    keycloak_client_secret: SecretStr | None = None
