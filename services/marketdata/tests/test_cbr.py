"""Веб-сервис Банка России: RUONIA — на записанном ответе настоящего сервиса (09.11.2026)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import pytest

from marketdata.cbr import CbrClient, CbrError, parse_ruonia

RECORDED = (Path(__file__).parent / "fixtures" / "cbr" / "ruonia.xml").read_bytes()
URL = "http://cbr.test/DailyInfoWebServ/DailyInfo.asmx"


def client(handler: Any) -> CbrClient:
    return CbrClient(httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), URL)


async def test_ruonia_from_the_recorded_answer() -> None:
    sent: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, content=RECORDED)

    found = await client(handler).ruonia(date(2026, 10, 28), date(2026, 11, 9))

    # Дата — та, за которую ставка: за 06.11 ЦБ опубликовал её 09.11. 04.11 — праздник.
    assert found == [
        (date(2026, 10, 28), Decimal("14.0900")),
        (date(2026, 10, 29), Decimal("14.0500")),
        (date(2026, 10, 30), Decimal("14.1200")),
        (date(2026, 11, 2), Decimal("14.0600")),
        (date(2026, 11, 3), Decimal("13.9200")),
        (date(2026, 11, 5), Decimal("13.7800")),
        (date(2026, 11, 6), Decimal("13.7200")),
    ]
    [request] = sent
    assert (request.method, str(request.url)) == ("POST", URL)
    assert request.headers["Content-Type"] == "application/soap+xml; charset=utf-8"
    body = request.content.decode("utf-8")
    assert "<fromDate>2026-10-28</fromDate>" in body
    assert "<ToDate>2026-11-09</ToDate>" in body


def test_no_rows_is_an_empty_answer() -> None:
    # ЦБ ещё не опубликовал ставку за период — таблица пустая.
    empty = RECORDED.split(b'<Ruonia xmlns="">')[0] + b"</diffgr:diffgram></RuoniaResult>"
    empty += b"</RuoniaResponse></soap:Body></soap:Envelope>"
    assert parse_ruonia(empty) == []


@pytest.mark.parametrize(
    "answer",
    [
        httpx2.Response(500, text="soap:Receiver"),
        httpx2.Response(200, text="<html>технические работы"),
        httpx2.Response(200, text="<r><ro><D0>2026-11-06T00:00:00+03:00</D0></ro></r>"),
        httpx2.Response(200, text="<r><ro><D0>вчера</D0><ruo>13.72</ruo></ro></r>"),
        # Сущности не разворачиваем: так устроены атаки на разбор XML.
        httpx2.Response(200, text='<!DOCTYPE r [<!ENTITY a "1">]><r><ro><ruo>&a;</ruo></ro></r>'),
    ],
    ids=["fault", "not-xml", "no-rate", "bad-date", "entities"],
)
async def test_bad_answers(answer: httpx2.Response) -> None:
    with pytest.raises(CbrError):
        await client(lambda _: answer).ruonia(date(2026, 11, 6), date(2026, 11, 6))


async def test_unreachable() -> None:
    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route", request=request)

    with pytest.raises(CbrError, match="Ruonia failed"):
        await client(down).ruonia(date(2026, 11, 6), date(2026, 11, 6))
