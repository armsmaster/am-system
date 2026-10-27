"""Клиент справочника: служебный токен, кэш купонов и календаря, ошибки."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx2
import pytest

from am_common.service_token import ServiceToken
from marketdata.accrual import CouponPeriod
from marketdata.refdata import CACHE_SECONDS, RefdataClient, RefdataUnavailableError

BOND = UUID("00000000-0000-0000-0000-000000026238")


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make(handler: Any, clock: Clock | None = None, *, with_token: bool = True) -> RefdataClient:
    def route(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/token"):
            return httpx2.Response(200, json={"access_token": "t", "expires_in": 300})
        assert request.headers["Authorization"] == "Bearer t"
        response: httpx2.Response = handler(request)
        return response

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(route))
    token = (
        ServiceToken(
            http, token_url="http://kc.test/token", client_id="am-marketdata", client_secret="s"
        )
        if with_token
        else None
    )
    return RefdataClient(http, "http://refdata.test/", token, clock=clock or Clock())


async def test_coupons_and_calendar_are_cached_for_a_while() -> None:
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        if request.url.path.startswith("/calendar/"):
            return httpx2.Response(200, json=[{"day": "2026-11-04", "is_trading": False}])
        return httpx2.Response(
            200,
            json={
                "params": {
                    "coupons": [
                        {"date": "2026-12-02", "start_date": "2026-06-03", "amount": "35.4"},
                        {"date": "2027-06-02", "start_date": None, "amount": None},
                    ]
                }
            },
        )

    clock = Clock()
    refdata = make(handler, clock)

    periods = await refdata.coupon_periods(BOND, date(2026, 10, 27))
    assert periods == [CouponPeriod(date(2026, 6, 3), date(2026, 12, 2), Decimal("35.4"))]
    await refdata.coupon_periods(BOND, date(2026, 10, 27))
    assert await refdata.calendar(2026) == {date(2026, 11, 4): False}
    await refdata.calendar(2026)
    assert len(calls) == 2

    clock.now = CACHE_SECONDS
    await refdata.calendar(2026)
    assert len(calls) == 3


async def test_before_issue_there_are_no_coupons() -> None:
    refdata = make(lambda r: httpx2.Response(404))

    assert await refdata.coupon_periods(BOND, date(2020, 1, 1)) == []


async def test_cache_is_cleared_when_full(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("marketdata.refdata.CACHE_LIMIT", 1)
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        return httpx2.Response(200, json=[])

    refdata = make(handler)
    await refdata.calendar(2025)
    await refdata.calendar(2026)
    await refdata.calendar(2025)

    assert calls == ["/calendar/2025", "/calendar/2026", "/calendar/2025"]


async def test_instruments_and_board() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/instruments":
            return httpx2.Response(
                200,
                json=[
                    {
                        "id": str(BOND),
                        "secid": "SU26238RMFS4",
                        "isin": "RU000A1038V6",
                        "kind": "bond",
                        "short_name": "ОФЗ 26238",
                    }
                ],
            )
        return httpx2.Response(200, json={"current": {"params": {"board": ""}}})

    refdata = make(handler)

    [item] = await refdata.instruments()
    assert (item.id, item.secid, item.short_name) == (BOND, "SU26238RMFS4", "ОФЗ 26238")
    assert await refdata.board(BOND) is None


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx2.Response(500),
        lambda r: (_ for _ in ()).throw(httpx2.ConnectError("down", request=r)),
    ],
)
async def test_failures_mean_refdata_unavailable(handler: Any) -> None:
    with pytest.raises(RefdataUnavailableError):
        await make(handler).instruments()


async def test_no_credentials_no_refdata() -> None:
    with pytest.raises(RefdataUnavailableError, match="not configured"):
        await make(lambda r: httpx2.Response(200, json=[]), with_token=False).instruments()
