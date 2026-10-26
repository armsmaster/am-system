"""Применение миграций — с настоящим PostgreSQL."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import Column, Integer, MetaData, Table, Text, text
from sqlalchemy.ext.asyncio import create_async_engine

from am_common.migrations import schema_diff, upgrade
from am_common.settings import ServiceSettings

type SettingsFactory = Callable[..., ServiceSettings]

pytestmark = pytest.mark.integration

SCRIPTS = str(Path(__file__).parent / "sample_migrations")


async def tables(postgres_url: str, schema: str) -> set[str]:
    engine = create_async_engine(postgres_url)
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = :schema"), {"schema": schema}
        )
        names = {row[0] for row in rows}
    await engine.dispose()
    return names


async def test_migrations_create_tables_in_service_schema(
    postgres_url: str, schema: str, make_settings: SettingsFactory
) -> None:
    settings = make_settings(database_url=SecretStr(postgres_url), database_schema=schema)

    # Alembic запускает свой цикл событий — уводим его из цикла теста.
    await asyncio.to_thread(upgrade, settings, SCRIPTS)
    await asyncio.to_thread(upgrade, settings, SCRIPTS)  # повторный запуск ничего не делает

    assert await tables(postgres_url, schema) == {"sample", "alembic_version"}

    matching = MetaData()
    Table("sample", matching, Column("id", Integer, primary_key=True))
    assert await schema_diff(postgres_url, schema, matching) == []

    drifted = MetaData()
    Table("sample", drifted, Column("id", Integer, primary_key=True), Column("name", Text))
    assert [change[0] for change in await schema_diff(postgres_url, schema, drifted)] == [
        "add_column"
    ]


def test_migrations_need_a_database(make_settings: SettingsFactory) -> None:
    with pytest.raises(RuntimeError, match="database_url and database_schema"):
        upgrade(make_settings(), SCRIPTS)
