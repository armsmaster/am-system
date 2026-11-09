"""Веб-сервис Банка России DailyInfo: ставка RUONIA (решение 148).

RUONIA биржа не публикует — только фьючерсы на неё. Банк России отдаёт ставку
методом ``Ruonia`` веб-сервиса DailyInfo (SOAP 1.2): по строке на день — дата,
за которую ставка (``D0``), и ставка в % годовых (``ruo``). Ставку за день ЦБ
публикует на следующий рабочий день, около 14:30 по Москве.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx2
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

ENVELOPE = """<?xml version="1.0" encoding="utf-8"?>
<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Body>
    <Ruonia xmlns="http://web.cbr.ru/">
      <fromDate>{since}</fromDate>
      <ToDate>{till}</ToDate>
    </Ruonia>
  </soap12:Body>
</soap12:Envelope>
"""
CONTENT_TYPE = "application/soap+xml; charset=utf-8"


class CbrError(Exception):
    """Веб-сервис ЦБ не ответил или ответил не так, как ожидалось."""


class CbrClient:
    def __init__(self, http: httpx2.AsyncClient, url: str, *, timeout: float = 30.0) -> None:
        self._http = http
        self._url = url
        self._timeout = timeout

    async def ruonia(self, since: date, till: date) -> list[tuple[date, Decimal]]:
        """RUONIA за дни с ``since`` по ``till``: дата, за которую ставка, и % годовых."""
        body = ENVELOPE.format(since=since.isoformat(), till=till.isoformat())
        try:
            response = await self._http.post(
                self._url,
                content=body.encode("utf-8"),
                headers={"Content-Type": CONTENT_TYPE},
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx2.HTTPError as exc:
            raise CbrError(f"CBR request Ruonia failed: {exc}") from exc
        return parse_ruonia(response.content)


def parse_ruonia(content: bytes) -> list[tuple[date, Decimal]]:
    """Строки ``ro`` ответа: таблица DataSet лежит внутри ответа без пространства имён."""
    try:
        # defusedxml: ответ приходит из сети — сущности и DTD в нём не разворачиваем.
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        raise CbrError(f"CBR answer is not XML: {exc}") from exc
    found: list[tuple[date, Decimal]] = []
    for row in root.iter("ro"):
        day, value = row.findtext("D0"), row.findtext("ruo")
        try:
            # D0 — полночь по Москве со смещением: «2026-11-06T00:00:00+03:00».
            found.append((datetime.fromisoformat(day or "").date(), Decimal(value or "")))
        except (ValueError, InvalidOperation) as exc:
            raise CbrError(f"CBR answer has a bad row: D0={day!r}, ruo={value!r}") from exc
    return found
