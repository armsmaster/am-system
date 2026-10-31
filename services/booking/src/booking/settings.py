from __future__ import annotations

from pydantic import SecretStr

from am_common.settings import ServiceSettings


class BookingSettings(ServiceSettings):
    service_name: str = "booking"
    database_schema: str | None = "booking"

    # Справочник (условия бумаг, календарь) и рыночные данные (НКД и номинал
    # для формы сделки) читаем под служебной учётной записью am-booking.
    refdata_url: str = "http://refdata:8000"
    marketdata_url: str = "http://marketdata:8000"
    keycloak_url: str = "http://keycloak:8080/auth"
    keycloak_realm: str = "am"
    keycloak_client_id: str = "am-booking"
    keycloak_client_secret: SecretStr | None = None
    # Портфели и доступы — у сервиса портфелей, с токеном пользователя:
    # он видит ровно те портфели, что видит пользователь.
    portfolio_url: str = "http://portfolio:8000"

    # Пересчёт: как часто проверять очередь и сколько ждать упавший экземпляр.
    recalc_seconds: float = 2.0
    recalc_stale_seconds: float = 600.0
    # События справочника: новая версия бумаги, правка календаря.
    consumer_group: str = "booking"
    consumer_name: str = "booking-1"

    # Фоновые задачи: пересчёт и события справочника. Тесты запускают их сами.
    background_jobs: bool = True
