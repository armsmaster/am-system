"""Расчёт по облигации через API: эталоны design/04 от условий справочника до ответа.

Числа — те же, что в эталонных тестах am-quant (libs/am-quant/tests/test_golden.py):
здесь проверяем, что сервис берёт правильные дату расчётов, НКД, номинал,
ставки и цену. Спреды в ответе — до 0,01 бп.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from am_common.app import Resources
from am_common.clock import MOSCOW
from am_common.testing import TokenFactory
from marketdata.commands import refresh_instruments
from marketdata.db import accrued_interest
from marketdata.main import build_app
from marketdata.refdata import refdata_client
from marketdata.repository import SETTLEMENT, Repository
from marketdata.scheduler import iss_client, load_due_rates, poll_if_due
from marketdata.settings import MarketdataSettings

if TYPE_CHECKING:
    from conftest import World

pytestmark = pytest.mark.integration

OFZ = UUID("00000000-0000-0000-0000-000000026238")
LINKER = UUID("00000000-0000-0000-0000-00000010f504")
FUND = UUID("00000000-0000-0000-0000-0000000001d7")
RZD = UUID("00000000-0000-0000-0000-0000000a0e06")  # РЖД БО 001Р-06R, оферта 14.10.2027
GPN = UUID("00000000-0000-0000-0000-0000000a0e08")  # Газпром нефть БО 003P-08R, КС + 1,30
NOW = datetime(2026, 10, 27, 11, 5, tzinfo=MOSCOW)
# Цена закрытия 28.10.2026 — дата расчётов 29.10, как в эталонах.
AT = datetime(2026, 10, 28, 15, 0, tzinfo=MOSCOW)
SETTLE = date(2026, 10, 29)
GPN_FLOATER = {"rate": "key_rate", "margin_bp": "130", "capitalized": False}


class Api:
    def __init__(self, client: TestClient, tokens: TokenFactory) -> None:
        self._client = client
        self._tokens = tokens

    def _headers(self, *roles: str) -> dict[str, str]:
        token = self._tokens.issue(subject="u-trader", username="trader", roles=list(roles))
        return {"Authorization": f"Bearer {token}"}

    def analytics(self, instrument_id: UUID, **params: Any) -> Any:
        response = self._client.get(
            f"/instruments/{instrument_id}/analytics",
            params=params,
            headers=self._headers("trading"),
        )
        assert response.is_success, response.text
        return response.json()

    def problem(self, instrument_id: UUID, **params: Any) -> tuple[int, str]:
        response = self._client.get(
            f"/instruments/{instrument_id}/analytics",
            params=params,
            headers=self._headers("trading"),
        )
        return response.status_code, response.json()["detail"]["code"]

    def feed(self, kind: str, items: list[dict[str, Any]]) -> None:
        response = self._client.post(
            f"/feed/v1/{kind}",
            json={"source": "cbr", kind: items},
            headers=self._headers("price_feed"),
        )
        assert response.is_success, response.text
        assert response.json()["rejected"] == []


@pytest.fixture
def api(settings: MarketdataSettings, world: World, tokens: TokenFactory) -> Iterator[Api]:
    """Сервис в 15:00 28.10.2026."""
    app = build_app(settings, http_transport=world.transport(), clock=lambda: AT)
    with TestClient(app) as client:
        yield Api(client, tokens)


@pytest.fixture
def api_now(client: TestClient, tokens: TokenFactory) -> Api:
    """Сервис во вторник 27.10.2026, 11:05 — данные дня ISS из фикстур."""
    return Api(client, tokens)


async def bonds(resources: Resources, world: World, **floater: Any) -> None:
    """Добавляет в справочник РЖД 001Р-06R и Газпром нефть 003P-08R, НКД на 29.10 — как от ISS."""
    world.add_bond(RZD, "RU000A0ZZ4P9", "РЖД 1Р-06R", world.recorded_terms("RU000A0ZZ4P9"))
    world.add_bond(
        GPN, "RU000A107HG1", "Газпнф3P8R", world.recorded_terms("RU000A107HG1") | floater
    )
    await refresh_instruments(resources.require_database(), refdata_client(resources))
    async with resources.require_database().session() as session, session.begin():
        await Repository(session).upsert_points(
            accrued_interest,
            [
                {"instrument_id": i, "as_of": SETTLE, "source": SETTLEMENT, "value": Decimal(v)}
                for i, v in ((RZD, "8.62"), (GPN, "6.71"))
            ],
        )


async def rates(resources: Resources) -> None:
    """Ключевая ставка из ISS (последняя — 14,00% за 26.10)."""
    await load_due_rates(resources, refdata_client(resources), iss_client(resources), AT)


def ruonia(api: Api, day: str = "2026-10-28") -> None:
    api.feed("rates", [{"rate": "ruonia", "date": day, "value": 13.9}])


def metrics(item: dict[str, Any]) -> list[str]:
    keys = ["yield_percent", "macaulay_years", "macaulay_days", "modified_duration", "dv01"]
    return [item[k] for k in [*keys, "convexity"]]


# --- облигации с фиксированным купоном -------------------------------------------


async def test_ofz_by_entered_price(api: Api, resources: Resources) -> None:
    await refresh_instruments(resources.require_database(), refdata_client(resources))

    found = api.analytics(OFZ, price="52.493")

    assert (found["trading_day"], found["settlement_date"], found["type"]) == (
        "2026-10-28",
        "2026-10-29",
        "fixed",
    )
    # НКД на 29.10: 35,40 / 182 × 148 дней = 28,79 ₽.
    assert found["price"] | {"valuation": None} == {
        "basis": "price",
        "clean": "52.493000",
        "face_value": "1000.000000",
        "accrued": "28.790000",
        "dirty": "553.720000",
        "valuation": None,
    }
    [maturity] = found["yields"]
    assert maturity["horizon"] == {"kind": "maturity", "date": "2041-05-15", "price": "100"}
    assert metrics(maturity) == [
        "15.935227", "6.824692", "2491.01", "5.886642", "0.325955", "60.252300"
    ]  # fmt: skip


async def test_ofz_by_valuation_price(api: Api, resources: Resources) -> None:
    await refresh_instruments(resources.require_database(), refdata_client(resources))
    api.feed(
        "quotes", [{"code": "SU26238RMFS4", "time": "2026-10-28T14:55:00+03:00", "vwap": 52.493}]
    )

    found = api.analytics(OFZ)

    assert found["price"]["basis"] == "valuation"
    assert found["price"]["valuation"]["type"] == "vwap"
    assert found["yields"][0]["yield_percent"] == "15.935227"


async def test_ofz_what_if_yield(api: Api, resources: Resources) -> None:
    await refresh_instruments(resources.require_database(), refdata_client(resources))

    found = api.analytics(OFZ, **{"yield": "15.935227"})

    assert found["price"]["basis"] == "yield"
    assert abs(Decimal(found["price"]["clean"]) - Decimal("52.493")) < Decimal("0.00001")
    assert found["yields"][0]["yield_percent"] == "15.935227"


async def test_offer_bond_to_maturity_is_not_computed(
    api: Api, resources: Resources, world: World
) -> None:
    await bonds(resources, world)

    offer, maturity = api.analytics(RZD, price="103.14")["yields"]

    assert offer["horizon"] == {"kind": "offer", "date": "2027-10-14", "price": "100"}
    assert metrics(offer) == ["15.232750", "0.916100", "334.38", "0.795000", "0.082682", "1.336653"]
    assert maturity["yield_percent"] is None
    assert maturity["problem"]["code"] == "coupon_unknown"


# --- флоатеры и линкеры ------------------------------------------------------------


async def test_key_rate_floater(api: Api, resources: Resources, world: World) -> None:
    await bonds(resources, world, floater=GPN_FLOATER)
    await rates(resources)
    ruonia(api)

    found = api.analytics(GPN, price="100.07")
    spread = found["spread"]

    assert (found["type"], found["yields"], found["price"]["dirty"]) == (
        "floater",
        [],
        "1007.410000",
    )
    assert spread["horizon"] == {"kind": "maturity", "date": "2026-12-12", "price": "100"}
    assert (spread["rate"], spread["margin_bp"], spread["capitalized"]) == (
        "key_rate",
        "130.00",
        False,
    )
    assert spread["funding"] | {"label": ""} == {
        "rate": "ruonia",
        "label": "",
        "date": "2026-10-28",
        "value": "13.9000",
        "source": "cbr",
    }
    assert (spread["index"]["date"], spread["index"]["value"], spread["index"]["source"]) == (
        "2026-10-26",
        "14.0000",
        "iss",
    )
    assert spread["funding_percent"] == "14.909369"
    assert spread["breakdown"] == {
        "basis_bp": "11.49",
        "capitalization_bp": "-8.88",
        "margin_bp": "148.59",
        "other_bp": "-2.20",
        "price_bp": "-67.49",
    }
    assert (spread["spread_bp"], spread["spread_duration"], spread["spread_dv01"]) == (
        "81.51",
        "0.103286",
        "0.010405",
    )
    assert spread["yield_percent"] == "15.724468"
    # Для лимитов — дюрация до купона 12.11: 14 дней от расчётов 29.10, прогноз КС 14% + 1,3%.
    assert spread["reset_duration"] == "0.032946"


async def test_floater_what_if_spread(api: Api, resources: Resources, world: World) -> None:
    await bonds(resources, world, floater=GPN_FLOATER)
    await rates(resources)
    ruonia(api)

    found = api.analytics(GPN, spread="81.5098")

    assert found["price"]["basis"] == "spread"
    assert abs(Decimal(found["price"]["clean"]) - Decimal("100.07")) < Decimal("0.0001")


async def test_veb_linker(api_now: Api, resources: Resources) -> None:
    # Данные дня ISS 27.10: на дату расчётов 28.10 номинал 1065,92 ₽, НКД 8,97 ₽.
    await refresh_instruments(resources.require_database(), refdata_client(resources))
    await poll_if_due(resources, refdata_client(resources), iss_client(resources), NOW)
    ruonia(api_now, "2026-10-26")

    found = api_now.analytics(LINKER, price="99.75")
    spread = found["spread"]

    assert found["type"] == "linker"
    assert (found["price"]["face_value"], found["price"]["accrued"]) == ("1065.920000", "8.970000")
    assert (spread["rate"], spread["margin_bp"], spread["index"]) == (None, None, None)
    assert spread["breakdown"] == {
        "basis_bp": "0.00",
        "capitalization_bp": "0.00",
        "margin_bp": "213.54",
        "other_bp": "-0.03",
        "price_bp": "11.34",
    }
    assert (spread["spread_bp"], spread["spread_duration"], spread["spread_dv01"]) == (
        "224.85",
        "2.188054",
        "0.234609",
    )
    assert spread["reset_duration"] is None
    # «Что если»: цена по спреду — обратная задача.
    back = api_now.analytics(LINKER, spread="224.8461")["price"]["clean"]
    assert abs(Decimal(back) - Decimal("99.75")) < Decimal("0.0001")


# --- чего не хватает для расчёта ------------------------------------------------------


async def test_what_cannot_be_computed(api: Api, resources: Resources, world: World) -> None:
    await bonds(resources, world)
    await rates(resources)

    # Не облигация; задано больше одного «что если»; нет цены для оценки.
    assert api.problem(FUND) == (422, "not_a_bond")
    assert api.problem(OFZ, price="52", **{"yield": "15"}) == (422, "one_input")
    assert api.problem(OFZ) == (422, "no_price")
    assert api.problem(OFZ, spread="100") == (422, "spread_for_fixed")
    # Флоатер без параметров в справочнике.
    assert api.problem(GPN, price="100") == (422, "floater_terms_missing")
    # Бумага погашена; версии на дату нет.
    assert api.problem(OFZ, price="100", at="2041-06-03T12:00:00+03:00") == (422, "matured")
    del world.terms[RZD]
    assert api.problem(RZD, price="100") == (422, "no_terms")


async def test_floater_and_linker_inputs(api: Api, resources: Resources, world: World) -> None:
    await bonds(resources, world, floater=GPN_FLOATER)
    await rates(resources)

    # RUONIA ещё не загружена — ни с сайта ЦБ, ни от скрипта: спред не посчитать.
    assert api.problem(GPN, price="100") == (422, "rate_missing")
    ruonia(api)
    assert api.problem(GPN, **{"yield": "15"}) == (422, "yield_for_floater")
    # Линкер без номинала на дату расчётов (данные дня ISS не загружены).
    assert api.problem(LINKER, price="99.75") == (422, "face_value_missing")


async def test_floater_without_accrued_interest(
    api: Api, resources: Resources, world: World
) -> None:
    # Сумма текущего купона неизвестна, точек ISS нет — НКД не досчитать.
    params = world.recorded_terms("RU000A107HG1") | {"floater": GPN_FLOATER}
    world.add_bond(GPN, "RU000A107HG1", "Газпнф3P8R", params)
    await refresh_instruments(resources.require_database(), refdata_client(resources))

    assert api.problem(GPN, price="100") == (422, "accrued_missing")
