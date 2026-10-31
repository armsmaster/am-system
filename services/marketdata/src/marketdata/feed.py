"""Приём цен, баров и ставок от скрипта: спецификация docs/api/price-feed (решение 64).

Пакет, который не проходит схему, отклоняется целиком (422). Плохой элемент
пакета отклоняется отдельно с кодом и сообщением, остальные принимаются.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from am_common.clock import MOSCOW
from am_common.events import Actor
from marketdata.backfill import announce, past_days
from marketdata.prices import FEED_TYPES, MAX_TIMEFRAME, PriceType
from marketdata.rates import RateKind
from marketdata.repository import Repository

MAX_ITEMS = 5000
FUTURE_TOLERANCE = timedelta(minutes=5)
PRICE_STEP = Decimal("0.000001")  # до 6 знаков после запятой; больше — округляем
RATE_STEP = Decimal("0.0001")  # ставка — до 4 знаков

Source = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_-]{1,32}$")]
Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=20)]
Price = Annotated[Decimal, Field(gt=0)]
Timeframe = Annotated[str, StringConstraints(pattern=r"^([1-9][0-9]{0,2}m|[1-4]h)$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuoteIn(_Strict):
    code: Code
    time: AwareDatetime
    vwap: Price | None = None
    last: Price | None = None
    bid: Price | None = None
    ask: Price | None = None
    close: Price | None = None
    market_price: Price | None = None
    admitted_quote: Price | None = None

    @model_validator(mode="after")
    def _has_a_price(self) -> QuoteIn:
        if not self.prices():
            raise ValueError("snapshot has no prices")
        return self

    def prices(self) -> dict[PriceType, Decimal]:
        return {kind: value for kind in FEED_TYPES if (value := getattr(self, kind.value))}


class QuotesIn(_Strict):
    source: Source
    quotes: Annotated[list[QuoteIn], Field(min_length=1, max_length=MAX_ITEMS)]


class BarIn(_Strict):
    code: Code
    timeframe: Timeframe
    start: AwareDatetime
    open: Price
    high: Price
    low: Price
    close: Price
    volume: Annotated[Decimal, Field(ge=0)] | None = None

    @property
    def minutes(self) -> int:
        number = int(self.timeframe[:-1])
        return number * 60 if self.timeframe.endswith("h") else number


class BarsIn(_Strict):
    source: Source
    bars: Annotated[list[BarIn], Field(min_length=1, max_length=MAX_ITEMS)]


class RateIn(_Strict):
    rate: RateKind
    date: dt.date
    value: Annotated[Decimal, Field(ge=0, le=100)]  # % годовых


class RatesIn(_Strict):
    source: Source
    rates: Annotated[list[RateIn], Field(min_length=1, max_length=MAX_ITEMS)]


class Rejected(BaseModel):
    index: int
    code: str
    error: str
    message: str


class FeedResult(BaseModel):
    batch_id: UUID
    accepted: int
    rejected: list[Rejected]


@dataclass(frozen=True, slots=True)
class _Problem:
    error: str
    message: str

    def rejected(self, index: int, code: str) -> Rejected:
        return Rejected(index=index, code=code, error=self.error, message=self.message)


_DUPLICATE = _Problem("duplicate_in_batch", "Та же цена или тот же бар уже есть в этом пакете.")
_DUPLICATE_RATE = _Problem("duplicate_in_batch", "Эта ставка на эту дату уже есть в пакете.")


async def ingest_quotes(
    repo: Repository, batch: QuotesIn, ids: Mapping[str, UUID], client: Actor, now: datetime
) -> FeedResult:
    batch_id = uuid4()
    rows: list[dict[str, Any]] = []
    rejected: list[Rejected] = []
    seen: set[tuple[UUID, PriceType, datetime]] = set()
    for index, item in enumerate(batch.quotes):
        found = _instrument(item.code, ids, item.time, now)
        if isinstance(found, _Problem):
            rejected.append(found.rejected(index, item.code))
            continue
        keys = [(found, kind, item.time) for kind in item.prices()]
        if any(key in seen for key in keys):
            rejected.append(_DUPLICATE.rejected(index, item.code))
            continue
        seen.update(keys)
        rows += [
            {
                "instrument_id": found,
                "source": batch.source,
                "price_type": kind.value,
                "observed_at": item.time,
                "value": _six_places(value),
                "received_at": now,
                "batch_id": batch_id,
            }
            for kind, value in item.prices().items()
        ]
    await repo.add_quotes(rows)
    await _announce(repo, ids, client, now, ((r["instrument_id"], r["observed_at"]) for r in rows))
    return await _finish(
        repo,
        batch_id=batch_id,
        kind="quotes",
        source=batch.source,
        client=client.username,
        now=now,
        items=batch.quotes,
        rejected=rejected,
    )


async def ingest_bars(
    repo: Repository, batch: BarsIn, ids: Mapping[str, UUID], client: Actor, now: datetime
) -> FeedResult:
    batch_id = uuid4()
    rows: list[dict[str, Any]] = []
    rejected: list[Rejected] = []
    seen: set[tuple[UUID, int, datetime]] = set()
    for index, item in enumerate(batch.bars):
        problem = _bar_problem(item)
        if problem is not None:
            rejected.append(problem.rejected(index, item.code))
            continue
        # Бар должен закончиться: его конец не позже текущего времени (с запасом на часы).
        found = _instrument(item.code, ids, item.start + timedelta(minutes=item.minutes), now)
        if isinstance(found, _Problem):
            rejected.append(found.rejected(index, item.code))
            continue
        key = (found, item.minutes, item.start)
        if key in seen:
            rejected.append(_DUPLICATE.rejected(index, item.code))
            continue
        seen.add(key)
        rows.append(
            {
                "instrument_id": key[0],
                "source": batch.source,
                "timeframe": item.minutes,
                "start_at": item.start,
                "open": _six_places(item.open),
                "high": _six_places(item.high),
                "low": _six_places(item.low),
                "close": _six_places(item.close),
                "volume": item.volume,
                "received_at": now,
                "batch_id": batch_id,
            }
        )
    await repo.add_bars(rows)
    await _announce(repo, ids, client, now, ((r["instrument_id"], r["start_at"]) for r in rows))
    return await _finish(
        repo,
        batch_id=batch_id,
        kind="bars",
        source=batch.source,
        client=client.username,
        now=now,
        items=batch.bars,
        rejected=rejected,
    )


async def ingest_rates(repo: Repository, batch: RatesIn, client: str, now: datetime) -> FeedResult:
    batch_id = uuid4()
    rows: list[dict[str, Any]] = []
    rejected: list[Rejected] = []
    seen: set[tuple[RateKind, date]] = set()
    today = now.astimezone(MOSCOW).date()
    for index, item in enumerate(batch.rates):
        if item.date > today:
            problem = _Problem(
                "future_date", "Дата ставки позже сегодняшней — проверьте дату и часовой пояс."
            )
            rejected.append(problem.rejected(index, item.rate.value))
            continue
        if (item.rate, item.date) in seen:
            rejected.append(_DUPLICATE_RATE.rejected(index, item.rate.value))
            continue
        seen.add((item.rate, item.date))
        rows.append(
            {
                "kind": item.rate.value,
                "as_of": item.date,
                "source": batch.source,
                "value": item.value.quantize(RATE_STEP, rounding=ROUND_HALF_UP),
                "received_at": now,
                "batch_id": batch_id,
            }
        )
    await repo.upsert_rates(rows)
    return await _finish(
        repo,
        batch_id=batch_id,
        kind="rates",
        source=batch.source,
        client=client,
        now=now,
        items=batch.rates,
        rejected=rejected,
    )


def _instrument(
    code: str, ids: Mapping[str, UUID], moment: datetime, now: datetime
) -> UUID | _Problem:
    instrument_id = ids.get(code.upper())
    if instrument_id is None:
        return _Problem(
            "unknown_instrument",
            f"Бумаги {code} нет в справочнике. Загрузите её в разделе «Справочник».",
        )
    if moment > now + FUTURE_TOLERANCE:
        return _Problem(
            "future_time",
            "Время позже текущего больше чем на 5 минут — проверьте часы и часовой пояс.",
        )
    return instrument_id


def _bar_problem(item: BarIn) -> _Problem | None:
    if item.minutes > MAX_TIMEFRAME:
        return _Problem("bad_timeframe", "Длина бара — не больше 240 минут.")
    local = item.start.astimezone(MOSCOW)
    since_midnight = local.hour * 60 + local.minute
    if local.second or local.microsecond or since_midnight % item.minutes:
        return _Problem(
            "misaligned_start",
            f"Начало бара по Москве ({local:%H:%M:%S}) не кратно его длине — {item.timeframe}.",
        )
    if item.high < max(item.open, item.close, item.low) or item.low > min(item.open, item.close):
        return _Problem(
            "inconsistent_ohlc", "Максимум или минимум бара противоречат открытию и закрытию."
        )
    return None


def _six_places(value: Decimal) -> Decimal:
    return value.quantize(PRICE_STEP, rounding=ROUND_HALF_UP)


async def _finish(
    repo: Repository,
    *,
    batch_id: UUID,
    kind: str,
    source: str,
    client: str,
    now: datetime,
    items: Sequence[object],
    rejected: list[Rejected],
) -> FeedResult:
    await repo.add_batch(
        {
            "id": batch_id,
            "client": client,
            "source": source,
            "kind": kind,
            "received_at": now,
            "accepted": len(items) - len(rejected),
            "rejected": [r.model_dump() for r in rejected],
        }
    )
    return FeedResult(batch_id=batch_id, accepted=len(items) - len(rejected), rejected=rejected)


async def _announce(
    repo: Repository,
    ids: Mapping[str, UUID],
    client: Actor,
    now: datetime,
    observed: Iterable[tuple[UUID, datetime]],
) -> None:
    """Цены за прошлые дни — событие для пересчёта оценки позиций."""
    today = now.astimezone(MOSCOW).date()
    earliest = past_days(((i, t.astimezone(MOSCOW).date()) for i, t in observed), today)
    await announce(repo.session, earliest, {i: code for code, i in ids.items()}, client)
