from __future__ import annotations

from pydantic import SecretStr

from am_common.settings import ServiceSettings


class PortfolioSettings(ServiceSettings):
    service_name: str = "portfolio"
    database_schema: str | None = "portfolio"

    # Справочник пользователей: Admin API Keycloak по внутреннему адресу.
    keycloak_url: str = "http://keycloak:8080/auth"
    keycloak_realm: str = "am"
    keycloak_client_id: str = "am-portfolio"
    # Нет секрета — нет справочника: выдача доступов отвечает 503.
    keycloak_client_secret: SecretStr | None = None
