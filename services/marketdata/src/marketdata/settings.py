from __future__ import annotations

from datetime import date, time

from pydantic import SecretStr

from am_common.settings import ServiceSettings


class MarketdataSettings(ServiceSettings):
    service_name: str = "marketdata"
    database_schema: str | None = "marketdata"

    # Открытая часть MOEX ISS (решение 61): итоги дня и данные дня с задержкой 15 минут.
    iss_url: str = "https://iss.moex.com/iss"
    iss_timeout: float = 20.0
    # Историю ISS раньше этой даты не грузим: учёт в системе начинается с неё.
    history_from: date = date(2026, 1, 1)
    # Итоги вчерашнего дня ISS публикует ночью — забираем утром, по Москве.
    history_at: time = time(6, 30)
    history_check_seconds: float = 300.0
    # Данные дня — раз в 5 минут, пока идут торги (решение 72).
    intraday_seconds: float = 300.0
    intraday_from: time = time(6, 50)
    intraday_till: time = time(23, 55)

    # Справочник: список бумаг, купоны, календарь. Читаем под служебной учётной записью.
    refdata_url: str = "http://refdata:8000"
    refdata_sync_seconds: float = 60.0
    keycloak_url: str = "http://keycloak:8080/auth"
    keycloak_realm: str = "am"
    keycloak_client_id: str = "am-marketdata"
    keycloak_client_secret: SecretStr | None = None

    # Пакет от скрипта — не больше 5 МБ (спецификация docs/api/price-feed).
    feed_max_bytes: int = 5 * 1024 * 1024

    # Фоновые задачи: список бумаг, история и данные дня ISS. Тесты запускают их сами.
    background_jobs: bool = True
