"""Денежные ставки: индексы ISS, RUONIA от ЦБ, ставки от скрипта и какая действует на дату."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from am_common.app import Resources
from am_common.clock import MOSCOW
from am_common.testing import TokenFactory
from marketdata.db import rate
from marketdata.refdata import refdata_client
from marketdata.scheduler import (
    RuoniaLoader,
    cbr_client,
    history_worker,
    iss_client,
    load_due_rates,
)
from marketdata.settings import MarketdataSettings

if TYPE_CHECKING:
    from conftest import World

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 27, 11, 5, tzinfo=MOSCOW)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """Ноябрь 2026: понедельник 09.11; 04.11 — праздник, 07–08.11 — выходные."""
    return datetime(2026, 11, day, hour, minute, tzinfo=MOSCOW)


def headers(tokens: TokenFactory, *roles: str) -> dict[str, str]:
    token = tokens.issue(subject="u-script", username="script", roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


def send(client: TestClient, tokens: TokenFactory, *items: dict[str, Any]) -> Any:
    response = client.post(
        "/feed/v1/rates",
        json={"source": "cbr", "rates": list(items)},
        headers=headers(tokens, "price_feed"),
    )
    assert response.is_success, response.text
    return response.json()


def on(client: TestClient, tokens: TokenFactory, day: str | None = None) -> dict[str, Any]:
    params = {} if day is None else {"date": day}
    response = client.get("/rates", params=params, headers=headers(tokens, "trading"))
    assert response.is_success, response.text
    return {item["rate"]: item for item in response.json()}


async def stored(resources: Resources) -> list[tuple[str, str, str]]:
    async with resources.require_database().session() as session:
        rows = await session.execute(select(rate.c.kind, rate.c.as_of, rate.c.value))
    return sorted((r.kind, r.as_of.isoformat(), str(r.value)) for r in rows)


async def test_iss_rates_are_loaded_once(resources: Resources) -> None:
    refdata, iss = refdata_client(resources), iss_client(resources)

    # Ключевая ставка и RUSFAR с 20.10 по 26.10 — по 5 торговых дней.
    assert await load_due_rates(resources, refdata, iss, NOW) == 10
    assert await load_due_rates(resources, refdata, iss, NOW) == 0

    rows = await stored(resources)
    assert ("key_rate", "2026-10-26", "14.0000") in rows
    assert ("rusfar", "2026-10-22", "14.2200") in rows


async def ruonia(resources: Resources) -> dict[str, tuple[str, str]]:
    async with resources.require_database().session() as session:
        rows = await session.execute(
            select(rate.c.as_of, rate.c.value, rate.c.source).where(rate.c.kind == "ruonia")
        )
    return {r.as_of.isoformat(): (str(r.value), r.source) for r in rows}


async def test_ruonia_comes_from_the_bank_of_russia(resources: Resources, world: World) -> None:
    loader, refdata = RuoniaLoader(cbr_client(resources)), refdata_client(resources)

    # 09.11 после 15:00 ЦБ уже опубликовал ставку за пятницу 06.11. Первый раз — с начала учёта.
    assert await loader.load_due(resources, refdata, at(9, 15, 30)) == 7
    assert world.cbr_asked == [("2026-01-01", "2026-11-06")]
    found = await ruonia(resources)
    assert len(found) == 7
    assert found["2026-11-06"] == ("13.7200", "cbr-ws")

    # Ставка за ожидаемый день уже есть — к ЦБ не идём.
    assert await loader.load_due(resources, refdata, at(9, 18)) == 0
    assert len(world.cbr_asked) == 1


async def test_ruonia_waits_for_publication(resources: Resources, world: World) -> None:
    loader, refdata = RuoniaLoader(cbr_client(resources)), refdata_client(resources)
    del world.ruonia["2026-11-06"]

    # До 15:00 ставку за 06.11 не ждём: только за 05.11.
    assert await loader.load_due(resources, refdata, at(9, 10)) == 6
    assert world.cbr_asked == [("2026-01-01", "2026-11-05")]

    # После 15:00 ждём 06.11, а ЦБ задерживает. Последние 7 дней — заново: 29.10–05.11.
    assert await loader.load_due(resources, refdata, at(9, 15, 5)) == 5
    assert world.cbr_asked[-1] == ("2026-10-29", "2026-11-06")
    # Пока ставки нет, спрашиваем не чаще раза в час.
    world.ruonia["2026-11-06"] = "13.7200"
    world.ruonia["2026-11-03"] = "13.9300"  # ЦБ исправил ставку задним числом
    assert await loader.load_due(resources, refdata, at(9, 16, 4)) == 0
    assert len(world.cbr_asked) == 2

    assert await loader.load_due(resources, refdata, at(9, 16, 5)) == 6
    found = await ruonia(resources)
    assert (found["2026-11-06"], found["2026-11-03"]) == (
        ("13.7200", "cbr-ws"),
        ("13.9300", "cbr-ws"),
    )


async def test_ruonia_is_retried_when_the_bank_is_down(
    resources: Resources, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    loader, refdata = RuoniaLoader(cbr_client(resources)), refdata_client(resources)
    world.cbr_down = True
    assert await loader.load_due(resources, refdata, at(9, 15, 30)) == 0
    assert "CBR RUONIA failed, will retry" in caplog.text

    world.cbr_down = False
    assert await loader.load_due(resources, refdata, at(9, 16)) == 0
    assert await loader.load_due(resources, refdata, at(9, 16, 30)) == 7


async def test_failed_iss_rates_are_retried(resources: Resources, world: World) -> None:
    refdata, iss = refdata_client(resources), iss_client(resources)
    world.iss_down = True
    assert await load_due_rates(resources, refdata, iss, NOW) == 0

    world.iss_down = False
    assert await load_due_rates(resources, refdata, iss, NOW) == 10


async def test_history_worker_loads_rates_too(
    settings: MarketdataSettings, resources: Resources
) -> None:
    quick = settings.model_copy(update={"history_check_seconds": 0.05})
    task = asyncio.ensure_future(history_worker(quick, clock=lambda: NOW)(resources))
    # Ждём все десять: под нагрузкой воркер может успеть записать только часть.
    for _ in range(100):
        if len(await stored(resources)) == 10:
            break
        await asyncio.sleep(0.05)
    task.cancel()
    assert len(await stored(resources)) == 10


async def test_script_rate_wins_over_iss_on_the_same_day(
    client: TestClient, tokens: TokenFactory, resources: Resources
) -> None:
    await load_due_rates(resources, refdata_client(resources), iss_client(resources), NOW)
    result = send(
        client,
        tokens,
        {"rate": "ruonia", "date": "2026-10-26", "value": 13.9},
        {"rate": "key_rate", "date": "2026-10-26", "value": 14.25},
        {"rate": "ruonia", "date": "2026-10-23", "value": 13.87654},
    )
    assert (result["accepted"], result["rejected"]) == (3, [])

    found = on(client, tokens)  # сегодня, 27.10: последние известные — за 26.10
    assert {k: (v["date"], v["value"], v["source"]) for k, v in found.items()} == {
        "key_rate": ("2026-10-26", "14.2500", "cbr"),
        "ruonia": ("2026-10-26", "13.9000", "cbr"),
        "rusfar": ("2026-10-26", "13.9600", "iss"),
    }
    assert found["ruonia"]["label"] == "RUONIA"
    # На прошлую дату — ставка той даты; 4 знака после запятой.
    assert on(client, tokens, "2026-10-23")["ruonia"]["value"] == "13.8765"


def test_rates_before_any_data(client: TestClient, tokens: TokenFactory) -> None:
    found = on(client, tokens, "2025-01-01")
    assert {v["value"] for v in found.values()} == {None}


def test_bad_rates_are_rejected_one_by_one(client: TestClient, tokens: TokenFactory) -> None:
    result = send(
        client,
        tokens,
        {"rate": "ruonia", "date": "2026-10-26", "value": 13.9},
        {"rate": "ruonia", "date": "2026-10-26", "value": 13.95},
        {"rate": "ruonia", "date": "2026-10-28", "value": 13.9},
    )
    assert result["accepted"] == 1
    assert [(r["index"], r["code"], r["error"]) for r in result["rejected"]] == [
        (1, "ruonia", "duplicate_in_batch"),
        (2, "ruonia", "future_date"),
    ]
    # Отклонено всё — пакет всё равно записан в ленту.
    assert (
        send(client, tokens, {"rate": "key_rate", "date": "2027-01-01", "value": 14})["accepted"]
        == 0
    )


@pytest.mark.parametrize(
    "item",
    [
        {"rate": "libor", "date": "2026-10-26", "value": 5},
        {"rate": "ruonia", "date": "2026-10-26", "value": -1},
        {"rate": "ruonia", "date": "26.10.2026", "value": 13.9},
    ],
)
def test_rate_out_of_schema_refuses_the_batch(
    client: TestClient, tokens: TokenFactory, item: dict[str, Any]
) -> None:
    response = client.post(
        "/feed/v1/rates",
        json={"source": "cbr", "rates": [item]},
        headers=headers(tokens, "price_feed"),
    )
    assert response.status_code == 422


def test_rates_are_sent_only_by_the_script(client: TestClient, tokens: TokenFactory) -> None:
    response = client.post(
        "/feed/v1/rates",
        json={"source": "cbr", "rates": [{"rate": "ruonia", "date": "2026-10-26", "value": 1}]},
        headers=headers(tokens, "trading"),
    )
    assert response.status_code == 403
