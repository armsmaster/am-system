"""Настройки сервиса из переменных окружения с префиксом ``AM_``."""

from __future__ import annotations

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseSettings):
    """Общие для всех сервисов настройки.

    Сервис наследует этот класс и задаёт свои значения по умолчанию
    (имя, схему БД). Префикс, под которым шлюз публикует сервис,
    передаётся серверу: ``uvicorn --root-path /api/<сервис>``.
    """

    model_config = SettingsConfigDict(env_prefix="AM_", extra="ignore", frozen=True)

    service_name: str
    log_level: str = "INFO"

    # Issuer в токенах — публичный адрес realm, как его видит браузер.
    auth_issuer: str
    # Ключи читаем по внутреннему адресу Keycloak, минуя шлюз.
    auth_jwks_url: str
    auth_audience: str = "am-api"

    database_url: SecretStr | None = None
    database_schema: str | None = None

    redis_url: SecretStr | None = None

    @model_validator(mode="after")
    def _schema_required_with_database(self) -> ServiceSettings:
        if self.database_url is not None and not self.database_schema:
            raise ValueError("database_schema is required when database_url is set")
        return self
