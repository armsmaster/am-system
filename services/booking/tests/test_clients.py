"""Клиенты соседних сервисов: версии бумаги, календарь с кэшем, ошибки, граф портфелей."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx2
import pytest
from pydantic import SecretStr

from am_common.app import Resources
from am_common.service_token import ServiceToken
from am_common.testing import TEST_ISSUER, TEST_JWKS_URL
from booking.clients import (
    CACHE_SECONDS,
    MarketdataClient,
    PortfolioRef,
    RefdataClient,
    ServiceUnavailableError,
    physical_under,
    service_token,
)
from booking.settings import BookingSettings

BOND = UUID(int=1)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make(handler: Any, *, token: bool = True, clock: Clock | None = None) -> RefdataClient:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    service = (
        ServiceToken(
            http, token_url="http://keycloak.test/token", client_id="am-booking", client_secret="s"
        )
        if token
        else None
    )
    return RefdataClient(http, "http://refdata.test/", service, clock=clock or Clock())


def tokens_and(handler: Any) -> Any:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/token":
            return httpx2.Response(200, json={"access_token": "t", "expires_in": 300})
        result: httpx2.Response = handler(request)
        return result

    return handle


async def test_history_reads_every_version() -> None:
    calls: list[str] = []
    first = {"number": 1, "valid_from": None, "params": {"maturity_date": "2041-05-15"}}
    second = {"number": 2, "valid_from": "2026-11-20", "params": {"maturity_date": "2041-06-15"}}

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/versions/1"):
            return httpx2.Response(200, json=first)
        return httpx2.Response(
            200,
            json={
                "kind": "bond",
                "secid": "SU26238RMFS4",
                "short_name": "ОФЗ 26238",
                "current": second,
                "versions": [{"number": 1}, {"number": 2}],
            },
        )

    history = await make(tokens_and(handler)).history(BOND)

    assert history is not None
    assert [v[0] for v in history.versions] == [None, date(2026, 11, 20)]
    # Текущую версию второй раз не запрашиваем.
    assert calls == [f"/instruments/{BOND}", f"/instruments/{BOND}/versions/1"]


async def test_calendar_is_cached_for_a_while() -> None:
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        return httpx2.Response(200, json=[{"day": "2026-11-04", "is_trading": False}])

    clock = Clock()
    refdata = make(tokens_and(handler), clock=clock)

    assert await refdata.calendar(2026) == {date(2026, 11, 4): False}
    await refdata.calendar(2026)
    assert len(calls) == 1
    clock.now = CACHE_SECONDS
    await refdata.calendar_around(date(2026, 12, 31))
    assert sorted(calls) == ["/calendar/2025", "/calendar/2026", "/calendar/2026", "/calendar/2027"]


@pytest.mark.parametrize(
    ("handler", "token"),
    [
        (lambda r: httpx2.Response(500), True),  # справочник ответил ошибкой
        (lambda r: httpx2.Response(200, json={}), False),  # нет служебной учётной записи
    ],
)
async def test_refdata_unavailable(handler: Any, token: bool) -> None:
    with pytest.raises(ServiceUnavailableError, match="refdata"):
        await make(tokens_and(handler), token=token).history(BOND)


async def test_service_token_failure() -> None:
    with pytest.raises(ServiceUnavailableError):
        await make(lambda r: httpx2.Response(500)).calendar(2026)


async def test_marketdata_value_or_nothing() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("face-value"):
            return httpx2.Response(404)
        return httpx2.Response(200, json={"value": "35.01"})

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(tokens_and(handler)))
    token = ServiceToken(
        http, token_url="http://keycloak.test/token", client_id="c", client_secret="s"
    )
    marketdata = MarketdataClient(http, "http://marketdata.test", token)

    assert await marketdata.on_date(BOND, "accrued-interest", date(2026, 11, 30)) == Decimal(
        "35.01"
    )
    assert await marketdata.on_date(BOND, "face-value", date(2026, 11, 30)) is None


def marketdata_with(handler: Any) -> MarketdataClient:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(tokens_and(handler)))
    token = ServiceToken(
        http, token_url="http://keycloak.test/token", client_id="c", client_secret="s"
    )
    return MarketdataClient(http, "http://marketdata.test", token)


async def test_marks_for_valuation() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert dict(request.url.params) == {"from": "2026-11-27", "till": "2026-11-28"}
        if request.url.path.startswith(f"/instruments/{UUID(int=9)}"):
            return httpx2.Response(404)
        return httpx2.Response(
            200,
            json=[
                {
                    "date": "2026-11-27",
                    "price": {"value": "52.550000", "stale": False},
                    "settle_date": "2026-11-30",
                    "accrued": "35.01",
                    "face_value": "1000",
                },
                {
                    "date": "2026-11-28",
                    "price": None,
                    "settle_date": "2026-11-30",
                    "accrued": None,
                    "face_value": None,
                },
            ],
        )

    marketdata = marketdata_with(handler)
    found = await marketdata.marks(BOND, date(2026, 11, 27), date(2026, 11, 28))

    friday, saturday = found[date(2026, 11, 27)], found[date(2026, 11, 28)]
    assert (friday.price, friday.settle, friday.accrued, friday.face) == (
        Decimal("52.55"),
        date(2026, 11, 30),
        Decimal("35.01"),
        Decimal(1000),
    )
    assert (saturday.price, saturday.stale, saturday.accrued, saturday.face) == (
        None,
        False,
        None,
        None,
    )
    assert await marketdata.marks(UUID(int=9), date(2026, 11, 27), date(2026, 11, 28)) == {}


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (
            {
                "spread": None,
                "yields": [{"dv01": None}, {"dv01": "0.326", "modified_duration": "5.89"}],
            },
            ("yield", Decimal("0.326"), Decimal("5.89"), None),
        ),
        (
            {"spread": {"spread_dv01": "0.04", "spread_duration": "0.41"}, "yields": []},
            ("spread", Decimal("0.04"), Decimal("0.41"), None),
        ),
        (
            {
                "spread": {
                    "spread_dv01": "0.01",
                    "spread_duration": "0.1",
                    "reset_duration": "0.03",
                },
                "yields": [],
            },
            ("spread", Decimal("0.01"), Decimal("0.1"), Decimal("0.03")),
        ),
        ({"spread": None, "yields": [{"dv01": None}]}, None),
        (None, None),
    ],
)
async def test_metrics(answer: Any, expected: Any) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404) if answer is None else httpx2.Response(200, json=answer)

    found = await marketdata_with(handler).metrics(BOND, datetime(2026, 11, 30, tzinfo=UTC))
    assert (
        None if found is None else (found.basis, found.dv01, found.duration, found.reset_duration)
    ) == expected


def ref(number: int, kind: str, *parents: int) -> PortfolioRef:
    return PortfolioRef(
        id=UUID(int=number),
        code=f"P{number}",
        name=f"P{number}",
        kind=kind,
        is_fund=False,
        parent_ids=tuple(UUID(int=p) for p in parents),
        fund_id=None,
        account_id=None,
        treasury_id=None,
    )


def test_physical_portfolios_are_counted_once() -> None:
    # Ромб: physical 4 входит в compound 2 и 3, оба — в 1; physical 5 под невидимым 9.
    visible = {
        r.id: r
        for r in [
            ref(1, "compound"),
            ref(2, "compound", 1),
            ref(3, "compound", 1),
            ref(4, "physical", 2, 3),
            ref(5, "physical", 9),
        ]
    }

    assert [r.code for r in physical_under(UUID(int=1), visible)] == ["P4"]
    assert physical_under(UUID(int=9), visible) == []


def test_service_token_needs_a_secret() -> None:
    settings = BookingSettings(auth_issuer=TEST_ISSUER, auth_jwks_url=TEST_JWKS_URL)
    http = httpx2.AsyncClient()
    resources = Resources(settings=settings, http=http, database=None, redis=None)
    assert service_token(resources) is None

    configured = settings.model_copy(update={"keycloak_client_secret": SecretStr("s")})
    assert service_token(Resources(settings=configured, http=http, database=None, redis=None))
