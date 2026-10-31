from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from am_common.app import Resources
from am_common.clock import MOSCOW
from am_common.db import Database
from am_common.migrations import upgrade
from am_common.testing import TEST_AUDIENCE, TEST_ISSUER, TEST_JWKS_URL, TokenFactory
from marketdata.main import build_app
from marketdata.settings import MarketdataSettings

FIXTURES = Path(__file__).parent / "fixtures" / "iss"
# Условия выпуска, как их отдаёт справочник (/as-of): ответы ISS, разобранные кодом справочника.
TERMS = Path(__file__).parent / "fixtures" / "refdata"
ISS_URL = "http://iss.test/iss"
REFDATA_URL = "http://refdata.test"
KEYCLOAK_URL = "http://keycloak.test/auth"

OFZ = UUID("00000000-0000-0000-0000-000000026238")
LINKER = UUID("00000000-0000-0000-0000-00000010f504")
FUND = UUID("00000000-0000-0000-0000-0000000001d7")

# Вторник 27.10.2026, 11:05 по Москве — «сейчас» в тестах.
NOW = datetime(2026, 10, 27, 11, 5, tzinfo=MOSCOW)


class World:
    """Всё, куда сервис ходит по сети: Keycloak, справочник и MOEX ISS."""

    def __init__(self, tokens: TokenFactory) -> None:
        self.tokens = tokens
        self.iss_down = False
        self.refdata_down = False
        self.requests: list[str] = []
        self.instruments: list[dict[str, Any]] = [
            _instrument(
                OFZ,
                secid="SU26238RMFS4",
                isin="RU000A1038V6",
                kind="bond",
                short_name="ОФЗ 26238",
                board="TQOB",
            ),
            _instrument(
                LINKER,
                secid="RU000A10F504",
                isin="RU000A10F504",
                kind="bond",
                short_name="ВЭБ2Р-58",
                board="TQCB",
            ),
            _instrument(
                FUND,
                secid="LQDT",
                isin="RU000A1014L8",
                kind="fund_share",
                short_name="ВИМ Ликвидность",
                board="TQBR",
            ),
        ]
        # ОФЗ 26238: купонный период 03.06–02.12.2026, купон 35,40 ₽; у линкера сумма неизвестна.
        # Индексацию номинала линкера на RUONIA указывает сопровождение: ISS её не знает.
        self.terms: dict[UUID, dict[str, Any]] = {
            OFZ: terms("SU26238RMFS4"),
            LINKER: terms("RU000A10F504") | {"nominal_indexation": "ruonia"},
            FUND: {"coupons": []},
        }
        self.calendar: dict[int, list[dict[str, Any]]] = {
            2026: [{"day": "2026-11-04", "is_trading": False, "comment": "праздник"}]
        }

    @staticmethod
    def recorded_terms(secid: str) -> dict[str, Any]:
        """Условия выпуска из fixtures/refdata."""
        return terms(secid)

    def add_bond(
        self, instrument_id: UUID, secid: str, short_name: str, params: dict[str, Any]
    ) -> None:
        self.instruments.append(
            _instrument(
                instrument_id,
                secid=secid,
                isin=secid,
                kind="bond",
                short_name=short_name,
                board="TQCB",
            )
        )
        self.terms[instrument_id] = params

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        path = request.url.path
        self.requests.append(path)
        if path.endswith("/protocol/openid-connect/certs"):
            return httpx2.Response(200, json=self.tokens.jwks())
        if path.endswith("/protocol/openid-connect/token"):
            return httpx2.Response(200, json={"access_token": "service-token", "expires_in": 300})
        if url.startswith(REFDATA_URL):
            return self._refdata(request)
        if url.startswith(ISS_URL):
            return self._iss(request)
        raise AssertionError(f"unexpected request {url}")

    def _refdata(self, request: httpx2.Request) -> httpx2.Response:
        if self.refdata_down:
            raise httpx2.ConnectError("refdata is down", request=request)
        assert request.headers["Authorization"] == "Bearer service-token"
        path = request.url.path
        if path == "/instruments":
            return httpx2.Response(200, json=[_summary(i) for i in self.instruments])
        if match := re.fullmatch(r"/instruments/([^/]+)", path):
            item = next(i for i in self.instruments if i["id"] == match[1])
            return httpx2.Response(200, json={"current": {"params": {"board": item["board"]}}})
        if match := re.fullmatch(r"/instruments/([^/]+)/as-of/(\d{4}-\d{2}-\d{2})", path):
            if match[2] < "2021-01-01" or UUID(match[1]) not in self.terms:
                return httpx2.Response(404, json={"detail": {"code": "not_found"}})
            return httpx2.Response(200, json={"params": self.terms[UUID(match[1])]})
        if match := re.fullmatch(r"/calendar/(\d{4})", path):
            return httpx2.Response(200, json=self.calendar.get(int(match[1]), []))
        return httpx2.Response(500)

    def _iss(self, request: httpx2.Request) -> httpx2.Response:
        if self.iss_down:
            raise httpx2.ConnectError("ISS is down", request=request)
        path = request.url.path
        if match := re.fullmatch(
            r"/iss/history/engines/stock/markets/\w+/securities/(\w+)\.json", path
        ):
            name = f"history_{match[1]}_{request.url.params['start']}.json"
        elif match := re.fullmatch(r"/iss/engines/stock/markets/(\w+)/securities\.json", path):
            name = f"intraday_{match[1]}.json"
        else:
            return httpx2.Response(404)
        file = FIXTURES / name
        if not file.exists():
            return httpx2.Response(404)
        return httpx2.Response(200, json=json.loads(file.read_text(encoding="utf-8")))


def _instrument(
    instrument_id: UUID, *, secid: str, isin: str, kind: str, short_name: str, board: str
) -> dict[str, Any]:
    return {
        "id": str(instrument_id),
        "secid": secid,
        "isin": isin,
        "kind": kind,
        "short_name": short_name,
        "board": board,
    }


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "board"}


def terms(secid: str) -> dict[str, Any]:
    params: dict[str, Any] = json.loads((TERMS / f"{secid}.json").read_text(encoding="utf-8"))
    return params


@pytest.fixture(scope="session")
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def world(tokens: TokenFactory) -> World:
    return World(tokens)


@pytest.fixture
def bare_settings(postgres_url: str, redis_url: str, schema: str) -> MarketdataSettings:
    """Настройки сервиса с отдельной пустой схемой."""
    return MarketdataSettings(
        auth_issuer=TEST_ISSUER,
        auth_jwks_url=TEST_JWKS_URL,
        auth_audience=TEST_AUDIENCE,
        database_url=SecretStr(postgres_url),
        database_schema=schema,
        redis_url=SecretStr(redis_url),
        iss_url=ISS_URL,
        refdata_url=REFDATA_URL,
        keycloak_url=KEYCLOAK_URL,
        keycloak_client_secret=SecretStr("marketdata-secret"),
        # Тесты запускают фоновые задачи сами — так данные не появляются «сами собой».
        background_jobs=False,
    )


@pytest.fixture
async def settings(bare_settings: MarketdataSettings) -> MarketdataSettings:
    """Настройки сервиса с отдельной, уже мигрированной схемой."""
    await asyncio.to_thread(upgrade, bare_settings, "marketdata:migrations")
    return bare_settings


@pytest.fixture
def client(settings: MarketdataSettings, world: World) -> Iterator[TestClient]:
    app = build_app(settings, http_transport=world.transport(), clock=lambda: NOW)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def resources(settings: MarketdataSettings, world: World) -> AsyncIterator[Resources]:
    assert settings.database_url is not None
    assert settings.database_schema is not None
    database = Database(settings.database_url.get_secret_value(), settings.database_schema)
    async with httpx2.AsyncClient(transport=world.transport()) as http:
        yield Resources(settings=settings, http=http, database=database, redis=None)
    await database.dispose()
