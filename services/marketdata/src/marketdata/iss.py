"""MOEX ISS для рыночных данных: итоги дня и данные дня (решения 60, 63, 72).

Итоги дня — история торгов: закрытие, средневзвешенная, рыночная цена,
признаваемая котировка, НКД и номинал на дату торгов. Данные дня —
с задержкой 15 минут: bid, ask, последняя сделка, средневзвешенная,
НКД и номинал на дату расчётов.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import httpx2

from am_common.clock import MOSCOW
from marketdata.prices import PriceType

MARKETS = {"bond": "bonds", "fund_share": "shares"}
HISTORY_COLUMNS = "TRADEDATE,BOARDID,CLOSE,WAPRICE,MARKETPRICE3,ADMITTEDQUOTE,ACCINT,FACEVALUE"
SECURITIES_COLUMNS = {
    "bonds": "SECID,BOARDID,ACCRUEDINT,SETTLEDATE,FACEVALUEONSETTLEDATE",
    "shares": "SECID,BOARDID",
}
MARKETDATA_COLUMNS = (
    "SECID,BOARDID,BID,OFFER,LAST,WAPRICE,NUMTRADES,TRADINGSTATUS,UPDATETIME,SYSTIME"
)
# Итог дня относится к концу дня по Москве: вечером он сменяет внутридневные цены.
END_OF_DAY = time(23, 59, 59)
BATCH = 10  # бумаг в одном запросе данных дня
TRADING = "T"

_DAILY_PRICES = {
    PriceType.CLOSE: "CLOSE",
    PriceType.VWAP: "WAPRICE",
    PriceType.MARKET_PRICE: "MARKETPRICE3",
    PriceType.ADMITTED_QUOTE: "ADMITTEDQUOTE",
}


class IssError(Exception):
    """ISS не ответил или ответил не так, как ожидалось."""


@dataclass(frozen=True, slots=True)
class DailyRow:
    """Итог торгового дня по бумаге."""

    day: date
    prices: dict[PriceType, Decimal] = field(default_factory=dict)
    accrued: Decimal | None = None  # на дату торгов
    face_value: Decimal | None = None  # на дату торгов

    @property
    def time(self) -> datetime:
        return datetime.combine(self.day, END_OF_DAY, tzinfo=MOSCOW)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Данные дня по бумаге: цены на ``time`` и НКД с номиналом на дату расчётов."""

    secid: str
    time: datetime | None
    prices: dict[PriceType, Decimal] = field(default_factory=dict)
    settle_date: date | None = None
    accrued: Decimal | None = None
    face_value: Decimal | None = None


class IssClient:
    def __init__(self, http: httpx2.AsyncClient, base_url: str, *, timeout: float = 20.0) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def history(
        self, secid: str, kind: str, board: str | None, since: date, till: date
    ) -> list[DailyRow]:
        """Итоги дней с ``since`` по ``till``; ISS отдаёт историю страницами по 100 строк."""
        path = f"history/engines/stock/markets/{MARKETS[kind]}/securities/{secid}.json"
        found: list[DailyRow] = []
        start = 0
        while True:
            payload = await self._get(
                path,
                **{
                    "from": since.isoformat(),
                    "till": till.isoformat(),
                    "start": str(start),
                    "history.columns": HISTORY_COLUMNS,
                },
            )
            found += [
                parse_daily(row)
                for row in rows(payload, "history")
                if board is None or row["BOARDID"] == board
            ]
            [cursor] = rows(payload, "history.cursor")
            start = int(cursor["INDEX"]) + int(cursor["PAGESIZE"])
            if start >= int(cursor["TOTAL"]):
                return found

    async def snapshots(
        self, kind: str, instruments: Sequence[tuple[str, str | None]]
    ) -> list[Snapshot]:
        """Данные дня по бумагам одного рынка: (код, основной режим торгов)."""
        market = MARKETS[kind]
        boards = dict(instruments)
        result: list[Snapshot] = []
        for offset in range(0, len(instruments), BATCH):
            chunk = [secid for secid, _ in instruments[offset : offset + BATCH]]
            payload = await self._get(
                f"engines/stock/markets/{market}/securities.json",
                securities=",".join(chunk),
                **{
                    "iss.only": "securities,marketdata",
                    "securities.columns": SECURITIES_COLUMNS[market],
                    "marketdata.columns": MARKETDATA_COLUMNS,
                },
            )
            result += parse_snapshots(payload, boards)
        return result

    async def _get(self, path: str, **params: str) -> dict[str, Any]:
        try:
            response = await self._http.get(
                f"{self._base}/{path}",
                params={"iss.meta": "off", **params},
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx2.HTTPError, ValueError) as exc:
            raise IssError(f"ISS request {path} failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise IssError(f"ISS request {path}: unexpected answer")
        return payload


def rows(payload: Mapping[str, Any], block: str) -> list[dict[str, Any]]:
    table = payload.get(block)
    if not isinstance(table, Mapping):
        raise IssError(f"ISS response has no block {block!r}")
    columns = table["columns"]
    return [dict(zip(columns, row, strict=True)) for row in table["data"]]


def parse_daily(row: Mapping[str, Any]) -> DailyRow:
    prices = {
        kind: price for kind, column in _DAILY_PRICES.items() if (price := _price(row.get(column)))
    }
    return DailyRow(
        day=date.fromisoformat(row["TRADEDATE"]),
        prices=prices,
        accrued=_decimal(row.get("ACCINT")),
        face_value=_price(row.get("FACEVALUE")),
    )


def parse_snapshots(payload: Mapping[str, Any], boards: Mapping[str, str | None]) -> list[Snapshot]:
    def wanted(row: Mapping[str, Any]) -> bool:
        board = boards.get(row["SECID"], "")
        return row["SECID"] in boards and (board is None or row["BOARDID"] == board)

    settlement = {row["SECID"]: row for row in rows(payload, "securities") if wanted(row)}
    result: list[Snapshot] = []
    for row in rows(payload, "marketdata"):
        if not wanted(row):
            continue
        security = settlement.get(row["SECID"], {})
        trading = row.get("TRADINGSTATUS") == TRADING
        prices: dict[PriceType, Decimal] = {}
        if trading:
            prices = {
                kind: price
                for kind, column in ((PriceType.BID, "BID"), (PriceType.ASK, "OFFER"))
                if (price := _price(row.get(column)))
            }
            if (row.get("NUMTRADES") or 0) > 0:
                prices |= {
                    kind: price
                    for kind, column in ((PriceType.LAST, "LAST"), (PriceType.VWAP, "WAPRICE"))
                    if (price := _price(row.get(column)))
                }
        settle = security.get("SETTLEDATE")
        result.append(
            Snapshot(
                secid=row["SECID"],
                time=_moment(row) if trading else None,
                prices=prices,
                settle_date=date.fromisoformat(settle) if settle else None,
                accrued=_decimal(security.get("ACCRUEDINT")),
                face_value=_price(security.get("FACEVALUEONSETTLEDATE")),
            )
        )
    return result


def _moment(row: Mapping[str, Any]) -> datetime:
    """Время данных: дата — из времени ISS, часы — из времени обновления (с задержкой)."""
    day = datetime.fromisoformat(row["SYSTIME"]).date()
    return datetime.combine(day, time.fromisoformat(row["UPDATETIME"]), tzinfo=MOSCOW)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        # Через строку: ISS отдаёт числа JSON, float даёт «хвост» в младших знаках.
        return Decimal(str(value))
    except ArithmeticError as exc:
        raise IssError(f"not a number: {value!r}") from exc


def _price(value: Any) -> Decimal | None:
    number = _decimal(value)
    return number if number is not None and number > 0 else None
