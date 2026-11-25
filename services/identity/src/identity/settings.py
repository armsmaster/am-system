from __future__ import annotations

from pydantic import SecretStr

from am_common.settings import ServiceSettings


class IdentitySettings(ServiceSettings):
    service_name: str = "identity"
    database_schema: str | None = "identity"

    keycloak_url: str = "http://keycloak:8080/auth"
    keycloak_realm: str = "am"
    # Служебная учётная запись для Admin API: заводить пользователей, роли, пароли.
    keycloak_client_id: str = "am-identity"
    keycloak_client_secret: SecretStr | None = None
    # Публичный клиент, от имени которого входит пользователь: токены те же, что
    # выдавал вход через страницу Keycloak.
    login_client_id: str = "am-web"
    # «Запомнить меня»: срок cookie, если Keycloak не сообщил срок офлайн-сессии.
    remember_max_age: int = 30 * 24 * 3600
