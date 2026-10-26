"""Миграции схемы сервиса (Alembic).

Миграции лежат в пакете сервиса (``<сервис>/migrations``) и применяются
при старте процесса командой ``python -m <сервис>.migrate``. Таблица
версий Alembic живёт в схеме сервиса.

Реплики сервиса могут стартовать одновременно, поэтому миграции идут
под advisory-блокировкой Postgres: вторая реплика дождётся первой
и увидит, что применять уже нечего.
"""

from __future__ import annotations

import asyncio
from typing import Any

from alembic import command, context
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, MetaData, text
from sqlalchemy.ext.asyncio import create_async_engine

from am_common.settings import ServiceSettings


def upgrade(settings: ServiceSettings, script_location: str, revision: str = "head") -> None:
    """Применяет миграции до ``revision``."""
    config = Config()
    config.set_main_option("script_location", script_location)
    config.attributes["settings"] = settings
    command.upgrade(config, revision)


def run_env(target_metadata: MetaData) -> None:
    """Тело ``env.py`` миграций любого сервиса."""
    settings: ServiceSettings = context.config.attributes["settings"]
    asyncio.run(_run(settings, target_metadata))


async def _run(settings: ServiceSettings, target_metadata: MetaData) -> None:
    if settings.database_url is None or settings.database_schema is None:
        raise RuntimeError("database_url and database_schema are required for migrations")
    schema = settings.database_schema
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:schema))"), {"schema": schema}
            )
            await connection.run_sync(_migrate, target_metadata)
            await connection.commit()
    finally:
        await engine.dispose()


def _migrate(connection: Connection, target_metadata: MetaData) -> None:
    # Транзакция уже открыта (её начала блокировка) — Alembic работает
    # внутри неё, и все шаги миграции фиксируются вместе.
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def schema_diff(database_url: str, schema: str, metadata: MetaData) -> list[Any]:
    """Расхождения между моделью в коде и схемой в базе (пусто — совпадают).

    Используется в тестах сервисов: после миграций модель и база должны
    совпадать, иначе миграцию забыли написать или написали не так.
    """
    engine = create_async_engine(
        database_url, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync: compare_metadata(MigrationContext.configure(sync), metadata)
            )
    finally:
        await engine.dispose()
