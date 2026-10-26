"""Миграции данных: то, что уже лежит в базе, переживает смену модели."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import insert

from am_common.db import Database
from am_common.migrations import upgrade
from refdata.db import instrument, instrument_version
from refdata.repository import Repository
from refdata.settings import RefdataSettings

pytestmark = pytest.mark.integration


async def test_saved_versions_lose_the_face_value(bare_settings: RefdataSettings) -> None:
    assert bare_settings.database_url is not None
    assert bare_settings.database_schema is not None
    await asyncio.to_thread(upgrade, bare_settings, "refdata:migrations", "0001")
    database = Database(
        bare_settings.database_url.get_secret_value(), bare_settings.database_schema
    )
    # Так версию линкера записывал справочник до решения 63: номинал на день загрузки.
    params = {
        "name": "ВЭБ.РФ ПБО-002Р-58",
        "short_name": "ВЭБ2Р-58",
        "board": "TQCB",
        "currency": "RUB",
        "face_value": "1065.1",
        "initial_face_value": "1000",
        "nominal_indexation": "unknown",
    }
    instrument_id = uuid4()
    async with database.session() as session, session.begin():
        await session.execute(
            insert(instrument).values(
                id=instrument_id,
                secid="RU000A10F504",
                kind="bond",
                name=params["name"],
                short_name=params["short_name"],
                added_by="admin",
                iss_snapshot=params,
            )
        )
        await session.execute(
            insert(instrument_version).values(
                instrument_id=instrument_id,
                number=1,
                source="iss",
                author="admin",
                pinned=[],
                params=params,
            )
        )

    await asyncio.to_thread(upgrade, bare_settings, "refdata:migrations")

    async with database.session() as session:
        record = await Repository(session).get(instrument_id)
        [version] = await Repository(session).versions(instrument_id)
    await database.dispose()
    # Модель запрещает лишние поля: прочиталось — значит, номинала в данных нет.
    assert version.params == record.iss_snapshot
    assert version.params.initial_face_value == 1000
