"""Веб-сервис Банка России: RUONIA и ключевая ставка — на записанных ответах настоящего сервиса."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import pytest

from marketdata.cbr import CbrClient, CbrError, parse_rates
from marketdata.rates import RateKind

FIXTURES = Path(__file__).parent / "fixtures" / "cbr"
RECORDED = (FIXTURES / "ruonia.xml").read_bytes()  # ответ 09.11.2026
KEY_RATE = (FIXTURES / "key_rate.xml").read_bytes()  # ответ 15.11.2026
URL = "http://cbr.test/DailyInfoWebServ/DailyInfo.asmx"


def client(handler: Any) -> CbrClient:
    return CbrClient(httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), URL)


async def test_ruonia_from_the_recorded_answer() -> None:
    sent: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, content=RECORDED)

    found = await client(handler).rates(RateKind.RUONIA, date(2026, 10, 28), date(2026, 11, 9))

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
    assert '<Ruonia xmlns="http://web.cbr.ru/">' in body


async def test_key_rate_from_the_recorded_answer() -> None:
    sent: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, content=KEY_RATE)

    found = await client(handler).rates(RateKind.KEY_RATE, date(2026, 10, 20), date(2026, 10, 28))

    # ЦБ отдаёт рабочие дни от новых к старым — сортируем по дате.
    assert found == [
        (date(2026, 10, day), Decimal("14.00")) for day in (20, 21, 22, 23, 26, 27, 28)
    ]
    assert '<KeyRate xmlns="http://web.cbr.ru/">' in sent[0].content.decode("utf-8")


def test_no_rows_is_an_empty_answer() -> None:
    # ЦБ ещё не опубликовал ставку за период — таблица пустая.
    empty = RECORDED.split(b'<Ruonia xmlns="">')[0] + b"</diffgr:diffgram></RuoniaResult>"
    empty += b"</RuoniaResponse></soap:Body></soap:Envelope>"
    assert parse_rates(empty, RateKind.RUONIA) == []


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
        await client(lambda _: answer).rates(RateKind.RUONIA, date(2026, 11, 6), date(2026, 11, 6))


async def test_unreachable() -> None:
    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route", request=request)

    with pytest.raises(CbrError, match="KeyRate failed"):
        await client(down).rates(RateKind.KEY_RATE, date(2026, 11, 6), date(2026, 11, 6))
