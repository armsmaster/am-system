from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx2
import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from am_common.app import Resources
from am_common.clock import MOSCOW
from am_common.db import Database
from am_common.migrations import upgrade
from am_common.testing import TEST_AUDIENCE, TEST_ISSUER, TEST_JWKS_URL, TokenFactory
from booking.main import build_app
from booking.settings import BookingSettings

FIXTURES = Path(__file__).parent / "fixtures" / "refdata"
REFDATA_URL = "http://refdata.test"
MARKETDATA_URL = "http://marketdata.test"
PORTFOLIO_URL = "http://portfolio.test"
KEYCLOAK_URL = "http://keycloak.test/auth"

# Фонд «ОПИФ 1»: казначейский портфель на банковском счёте и портфель облигаций
# на брокерском (design/05, сквозной пример). Второй фонд трейдеру не виден.
FUND = UUID("00000000-0000-0000-0000-00000000f001")
TREASURY = UUID("00000000-0000-0000-0000-00000000a001")
BONDS = UUID("00000000-0000-0000-0000-00000000b001")
OTHER_FUND = UUID("00000000-0000-0000-0000-00000000f002")
OTHER_BONDS = UUID("00000000-0000-0000-0000-00000000b002")

OFZ = UUID("00000000-0000-0000-0000-000000026238")
LQDT = UUID("00000000-0000-0000-0000-0000000001d7")
LINKER = UUID("00000000-0000-0000-0000-00000010f504")
MATURED = UUID("00000000-0000-0000-0000-000000000666")
FUTURE = UUID("00000000-0000-0000-0000-000000000777")
# Флоатер на ключевую ставку + 1,30%, как Газпром нефть 003P-08R: купон 14.12 не объявлен.
FLOATER = UUID("00000000-0000-0000-0000-0000000a07b1")

# Четверг 03.12.2026, 12:00 по Москве — «сейчас» в тестах: пример методики уже в прошлом.
NOW = datetime(2026, 12, 3, 12, 0, tzinfo=MOSCOW)


def _portfolio(
    portfolio_id: UUID,
    code: str,
    kind: str,
    *,
    parents: tuple[UUID, ...] = (),
    fund: UUID | None = None,
    is_fund: bool = False,
) -> dict[str, Any]:
    return {
        "id": str(portfolio_id),
        "code": code,
        "name": code,
        "kind": kind,
        "is_fund": is_fund,
        "parent_ids": [str(p) for p in parents],
        "fund_id": None if fund is None else str(fund),
        "account_id": str(UUID(int=portfolio_id.int + 1)) if kind == "physical" else None,
        "treasury_id": str(TREASURY) if portfolio_id == FUND else None,
    }


def _ofz_terms() -> dict[str, Any]:
    params: dict[str, Any] = json.loads(
        (FIXTURES / "SU26238RMFS4.json").read_text(encoding="utf-8")
    )
    return params


class World:
    """Всё, куда сервис ходит по сети: Keycloak, справочник, рыночные данные, портфели."""

    def __init__(self, tokens: TokenFactory) -> None:
        self.tokens = tokens
        self.down: set[str] = set()
        self.portfolios = [
            _portfolio(FUND, "OPIF1", "compound", is_fund=True),
            _portfolio(TREASURY, "OPIF1-TREASURY", "physical", parents=(FUND,), fund=FUND),
            _portfolio(BONDS, "OPIF1-BONDS", "physical", parents=(FUND,), fund=FUND),
            _portfolio(OTHER_FUND, "OPIF2", "compound", is_fund=True),
            _portfolio(
                OTHER_BONDS, "OPIF2-BONDS", "physical", parents=(OTHER_FUND,), fund=OTHER_FUND
            ),
        ]
        # Кому что видно: администратор и сопровождение — всё, трейдер — только ОПИФ 1.
        self.hidden = {"trader": {OTHER_FUND, OTHER_BONDS}}
        ofz = _ofz_terms()
        self.instruments: dict[UUID, dict[str, Any]] = {
            OFZ: _instrument(OFZ, "SU26238RMFS4", "ОФЗ 26238", "bond", [(None, ofz)])
            | {"isin": "RU000A1038V6"},
            LQDT: _instrument(
                LQDT, "LQDT", "ВИМ Ликвидность", "fund_share", [(None, {"coupons": []})]
            ),
            LINKER: _instrument(
                LINKER,
                "RU000A10F504",
                "ВЭБ2Р-58",
                "bond",
                [(None, {**ofz, "nominal_indexation": "ruonia"})],
            ),
            MATURED: _instrument(
                MATURED,
                "SU26999RMFS0",
                "ОФЗ погашена",
                "bond",
                [(None, {**ofz, "maturity_date": "2026-01-01"})],
            ),
            FUTURE: _instrument(
                FUTURE, "SU27000RMFS0", "ОФЗ выпуск 2027", "bond", [(date(2027, 1, 1), ofz)]
            ),
            FLOATER: _instrument(
                FLOATER,
                "RU000A107HG1",
                "Газпнф3P8R",
                "bond",
                [
                    (
                        None,
                        {
                            "initial_face_value": "1000",
                            "maturity_date": "2028-12-01",
                            "nominal_indexation": "none",
                            "floater": {
                                "rate": "key_rate",
                                "margin_bp": "130",
                                "capitalized": False,
                            },
                            "coupons": [
                                {
                                    "date": "2026-12-14",
                                    "start_date": "2026-11-14",
                                    "record_date": "2026-12-11",
                                    "amount": None,
                                }
                            ],
                            "amortizations": [],
                        },
                    )
                ],
            ),
        }
        # НКД и номинал на дату расчётов — как их отдаёт сервис рыночных данных.
        self.accrued: dict[tuple[UUID, str], str] = {
            (OFZ, "2026-11-30"): "35.01",
            (OFZ, "2026-12-01"): "35.21",
            (OFZ, "2026-12-02"): "0",
            (OFZ, "2026-12-04"): "0.39",
        }
        self.face: dict[tuple[UUID, str], str] = {(OFZ, "2026-11-30"): "1000"}
        # Для оценки (design/06): цены на конец дня, НКД на T+1 сверх ``accrued``, DV01.
        self.prices: dict[tuple[UUID, str], str] = {}
        self.stale: set[tuple[UUID, str]] = set()
        self.valuation_accrued: dict[tuple[UUID, str], str] = {
            (OFZ, "2026-11-27"): "34.43",
            (OFZ, "2026-12-03"): "0.19",
        }
        self.metrics: dict[UUID, dict[str, Any]] = {}
        # Ставки индексов, как их отдаёт сервис цен; None — сервис недоступен.
        self.rates: list[dict[str, Any]] | None = [
            {"rate": "key_rate", "value": "14.0000"},
            {"rate": "ruonia", "value": None},
            {"rate": "rusfar", "value": "14.1700"},
        ]
        self.calendar: dict[int, list[dict[str, Any]]] = {
            2026: [{"day": "2026-11-04", "is_trading": False, "comment": "праздник"}]
        }
        self.requests: list[str] = []

    def add_instrument(
        self, instrument_id: UUID, secid: str, params: dict[str, Any], kind: str = "bond"
    ) -> None:
        self.instruments[instrument_id] = _instrument(
            instrument_id, secid, secid, kind, [(None, params)]
        )

    @staticmethod
    def ofz_terms() -> dict[str, Any]:
        return _ofz_terms()

    def add_version(self, instrument_id: UUID, valid_from: date, params: dict[str, Any]) -> None:
        found = self.instruments[instrument_id]
        number = len(found["versions"]) + 1
        version = {"number": number, "valid_from": valid_from.isoformat(), "params": params}
        found["versions"].append(version)
        found["current"] = version

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        url, path = str(request.url), request.url.path
        self.requests.append(path)
        if path.endswith("/protocol/openid-connect/certs"):
            return httpx2.Response(200, json=self.tokens.jwks())
        if path.endswith("/protocol/openid-connect/token"):
            return httpx2.Response(200, json={"access_token": "service-token", "expires_in": 300})
        for name, base in (
            ("refdata", REFDATA_URL),
            ("marketdata", MARKETDATA_URL),
            ("portfolio", PORTFOLIO_URL),
        ):
            if url.startswith(base):
                if name in self.down:
                    raise httpx2.ConnectError(f"{name} is down", request=request)
                handler = getattr(self, f"_{name}")
                response: httpx2.Response = handler(request, path)
                return response
        raise AssertionError(f"unexpected request {url}")

    def _refdata(self, request: httpx2.Request, path: str) -> httpx2.Response:
        assert request.headers["Authorization"] == "Bearer service-token"
        if path == "/instruments":
            summaries = [
                {"id": i["id"], "secid": i["secid"], "isin": i.get("isin"), "kind": i["kind"]}
                for i in self.instruments.values()
            ]
            return httpx2.Response(200, json=summaries)
        if match := re.fullmatch(r"/instruments/([^/]+)", path):
            found = self.instruments.get(UUID(match[1]))
            if found is None:
                return httpx2.Response(404, json={"detail": {"code": "not_found"}})
            return httpx2.Response(200, json=found)
        if match := re.fullmatch(r"/instruments/([^/]+)/versions/(\d+)", path):
            versions = self.instruments[UUID(match[1])]["versions"]
            return httpx2.Response(200, json=versions[int(match[2]) - 1])
        if match := re.fullmatch(r"/calendar/(\d{4})", path):
            return httpx2.Response(200, json=self.calendar.get(int(match[1]), []))
        return httpx2.Response(500)

    def _marketdata(self, request: httpx2.Request, path: str) -> httpx2.Response:
        assert request.headers["Authorization"] == "Bearer service-token"
        if path == "/rates":
            return self._rates()
        if match := re.fullmatch(r"/instruments/([^/]+)/marks", path):
            return self._marks(UUID(match[1]), request)
        if match := re.fullmatch(r"/instruments/([^/]+)/analytics", path):
            found = self.metrics.get(UUID(match[1]))
            if found is None:
                return httpx2.Response(404, json={"detail": {"code": "not_found"}})
            return httpx2.Response(200, json=found)
        match = re.fullmatch(r"/instruments/([^/]+)/(accrued-interest|face-value)", path)
        assert match is not None
        day = request.url.params["date"]
        source = self.accrued if match[2] == "accrued-interest" else self.face
        value = source.get((UUID(match[1]), day))
        if value is None:
            return httpx2.Response(404, json={"detail": {"code": "not_found"}})
        return httpx2.Response(200, json={"date": day, "value": value, "method": "iss"})

    def _rates(self) -> httpx2.Response:
        if self.rates is None:
            return httpx2.Response(503, json={"detail": {"code": "unavailable"}})
        return httpx2.Response(200, json=self.rates)

    def _marks(self, instrument_id: UUID, request: httpx2.Request) -> httpx2.Response:
        """Отметки как в сервисе цен: цена — последняя известная к концу дня,
        расчёты T+1 — следующий будний день, кроме праздника 04.11."""
        if instrument_id not in self.instruments:
            return httpx2.Response(404, json={"detail": {"code": "not_found"}})
        bond = self.instruments[instrument_id]["kind"] == "bond"
        day = date.fromisoformat(request.url.params["from"])
        till = date.fromisoformat(request.url.params["till"])
        found = []
        while day <= till:
            known = [d for (i, d) in self.prices if i == instrument_id and d <= day.isoformat()]
            price = None
            if known:
                last = max(known)
                price = {
                    "value": self.prices[(instrument_id, last)],
                    "type": "vwap",
                    "source": "quik",
                    "time": f"{last}T18:00:00+03:00",
                    "stale": (instrument_id, last) in self.stale,
                    "reason": "по правилу",
                }
            settle = day + timedelta(days=1)
            while settle.weekday() >= 5 or settle == date(2026, 11, 4):
                settle += timedelta(days=1)
            key = (instrument_id, settle.isoformat())
            accrued = self.valuation_accrued.get(key, self.accrued.get(key))
            found.append(
                {
                    "date": day.isoformat(),
                    "price": price,
                    "settle_date": settle.isoformat(),
                    "accrued": accrued if bond else None,
                    "face_value": "1000" if bond and instrument_id != LINKER else None,
                }
            )
            day += timedelta(days=1)
        return httpx2.Response(200, json=found)

    def _portfolio(self, request: httpx2.Request, path: str) -> httpx2.Response:
        assert path == "/portfolios"
        token = request.headers["Authorization"].removeprefix("Bearer ")
        username = jwt.decode(token, options={"verify_signature": False})["preferred_username"]
        hidden = {str(h) for h in self.hidden.get(username, set())}
        return httpx2.Response(200, json=[p for p in self.portfolios if p["id"] not in hidden])


def _instrument(
    instrument_id: UUID,
    secid: str,
    short_name: str,
    kind: str,
    versions: list[tuple[date | None, dict[str, Any]]],
) -> dict[str, Any]:
    items = [
        {
            "number": number,
            "valid_from": None if valid_from is None else valid_from.isoformat(),
            "params": params,
        }
        for number, (valid_from, params) in enumerate(versions, start=1)
    ]
    return {
        "id": str(instrument_id),
        "secid": secid,
        "short_name": short_name,
        "kind": kind,
        "current": items[-1],
        "versions": items,
    }


@pytest.fixture(scope="session")
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def world(tokens: TokenFactory) -> World:
    return World(tokens)


@pytest.fixture
def bare_settings(postgres_url: str, redis_url: str, schema: str) -> BookingSettings:
    return BookingSettings(
        auth_issuer=TEST_ISSUER,
        auth_jwks_url=TEST_JWKS_URL,
        auth_audience=TEST_AUDIENCE,
        database_url=SecretStr(postgres_url),
        database_schema=schema,
        redis_url=SecretStr(redis_url),
        refdata_url=REFDATA_URL,
        marketdata_url=MARKETDATA_URL,
        portfolio_url=PORTFOLIO_URL,
        keycloak_url=KEYCLOAK_URL,
        keycloak_client_secret=SecretStr("booking-secret"),
        background_jobs=False,
    )


@pytest.fixture
async def settings(bare_settings: BookingSettings) -> BookingSettings:
    await asyncio.to_thread(upgrade, bare_settings, "booking:migrations")
    return bare_settings


@pytest.fixture
def client(settings: BookingSettings, world: World) -> Iterator[TestClient]:
    app = build_app(settings, http_transport=world.transport(), clock=lambda: NOW)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def resources(settings: BookingSettings, world: World) -> AsyncIterator[Resources]:
    assert settings.database_url is not None
    assert settings.database_schema is not None
    database = Database(settings.database_url.get_secret_value(), settings.database_schema)
    async with httpx2.AsyncClient(transport=world.transport()) as http:
        yield Resources(settings=settings, http=http, database=database, redis=None)
    await database.dispose()
