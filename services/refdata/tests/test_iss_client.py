"""Клиент ISS поверх подменного HTTP: какие запросы делает и как сообщает о сбоях."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx2
import pytest

from refdata.domain import InstrumentKind, NominalIndexation
from refdata.iss import IssClient, IssError

if TYPE_CHECKING:
    from conftest import FakeIss


def client(transport: httpx2.MockTransport) -> IssClient:
    return IssClient(httpx2.AsyncClient(transport=transport), "http://iss.test/iss/")


async def test_bond_takes_search_description_and_schedule(iss: FakeIss) -> None:

    bond = await client(iss.transport()).find("su26238rmfs4")

    assert bond.secid == "SU26238RMFS4"
    assert iss.requests == [
        "/iss/securities.json",
        "/iss/securities/SU26238RMFS4.json",
        "/iss/statistics/engines/stock/markets/bonds/bondization/SU26238RMFS4.json",
    ]


async def test_fund_share_has_no_schedule(iss: FakeIss) -> None:

    fund = await client(iss.transport()).find("LQDT")

    assert fund.kind is InstrumentKind.FUND_SHARE
    assert len(iss.requests) == 2


async def test_indexed_nominal_is_flagged(iss: FakeIss) -> None:
    iss.describe("SU26238RMFS4", "FACEVALUE_TYPE", "Индексируемый")

    bond = await client(iss.transport()).find("SU26238RMFS4")

    assert bond.params.nominal_indexation is NominalIndexation.UNKNOWN


def respond(status: int, body: Any) -> httpx2.MockTransport:
    content = body if isinstance(body, bytes) else json.dumps(body).encode()
    return httpx2.MockTransport(lambda _request: httpx2.Response(status, content=content))


@pytest.mark.parametrize(
    ("transport", "message"),
    [
        (respond(500, {}), "500"),
        (respond(200, b"<html>"), "failed"),
        (
            respond(
                200,
                {
                    "securities": {"columns": ["secid"], "data": [["X"]]},
                    "securities.cursor": {
                        "columns": ["INDEX", "TOTAL", "PAGESIZE"],
                        "data": [[0, 200, 100]],
                    },
                },
            ),
            "incomplete",
        ),
    ],
    ids=["server error", "not json", "incomplete page"],
)
async def test_failures_are_reported(transport: httpx2.MockTransport, message: str) -> None:
    with pytest.raises(IssError, match=message):
        await client(transport).find("X")


async def test_network_failure(iss: FakeIss) -> None:
    iss.down = True

    with pytest.raises(IssError, match="ISS is down"):
        await client(iss.transport()).find("SU26238RMFS4")
