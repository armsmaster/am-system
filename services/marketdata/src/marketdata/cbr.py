"""Веб-сервис Банка России DailyInfo: RUONIA (решение 148) и ключевая ставка (решение 157).

RUONIA биржа не публикует — только фьючерсы на неё. Банк России отдаёт ставку
методом ``Ruonia`` веб-сервиса DailyInfo (SOAP 1.2): по строке на день — дата,
за которую ставка (``D0``), и ставка в % годовых (``ruo``). Ставку за день ЦБ
публикует на следующий рабочий день, около 14:30 по Москве.

Ключевую ставку отдаёт метод ``KeyRate``: по строке на рабочий день — дата (``DT``)
и ставка (``Rate``). Индекс KEYRATE в ISS начинается 24.08.2026, а базису RUONIA
к ключевой ставке нужна история за 5 лет — её берём отсюда.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx2
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from marketdata.rates import RateKind

ENVELOPE = """<?xml version="1.0" encoding="utf-8"?>
<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Body>
    <{method} xmlns="http://web.cbr.ru/">
      <fromDate>{since}</fromDate>
      <ToDate>{till}</ToDate>
    </{method}>
  </soap12:Body>
</soap12:Envelope>
"""
CONTENT_TYPE = "application/soap+xml; charset=utf-8"
# Ставка → метод DailyInfo, строка таблицы ответа, поле даты, поле ставки.
METHODS = {
    RateKind.RUONIA: ("Ruonia", "ro", "D0", "ruo"),
    RateKind.KEY_RATE: ("KeyRate", "KR", "DT", "Rate"),
}


class CbrError(Exception):
    """Веб-сервис ЦБ не ответил или ответил не так, как ожидалось."""


class CbrClient:
    def __init__(self, http: httpx2.AsyncClient, url: str, *, timeout: float = 30.0) -> None:
        self._http = http
        self._url = url
        self._timeout = timeout

    async def rates(self, kind: RateKind, since: date, till: date) -> list[tuple[date, Decimal]]:
        """Ставка ``kind`` за дни с ``since`` по ``till``: дата, за которую ставка, и % годовых."""
        method = METHODS[kind][0]
        body = ENVELOPE.format(method=method, since=since.isoformat(), till=till.isoformat())
        try:
            response = await self._http.post(
                self._url,
                content=body.encode("utf-8"),
                headers={"Content-Type": CONTENT_TYPE},
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx2.HTTPError as exc:
            raise CbrError(f"CBR request {method} failed: {exc}") from exc
        return parse_rates(response.content, kind)


def parse_rates(content: bytes, kind: RateKind) -> list[tuple[date, Decimal]]:
    """Строки таблицы ответа: DataSet лежит внутри ответа без пространства имён."""
    _, row_tag, day_tag, value_tag = METHODS[kind]
    try:
        # defusedxml: ответ приходит из сети — сущности и DTD в нём не разворачиваем.
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        raise CbrError(f"CBR answer is not XML: {exc}") from exc
    found: list[tuple[date, Decimal]] = []
    for row in root.iter(row_tag):
        day, value = row.findtext(day_tag), row.findtext(value_tag)
        try:
            # Дата — полночь по Москве со смещением: «2026-11-06T00:00:00+03:00».
            found.append((datetime.fromisoformat(day or "").date(), Decimal(value or "")))
        except (ValueError, InvalidOperation) as exc:
            raise CbrError(f"CBR answer has a bad row: {day!r}, {value!r}") from exc
    return sorted(found)
