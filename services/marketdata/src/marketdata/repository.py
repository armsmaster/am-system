"""Чтение и запись рыночных данных в БД."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Row, Table, func, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.ext.asyncio import AsyncSession

from marketdata.db import (
    bar,
    feed_batch,
    instrument,
    iss_poll,
    manual_price,
    price_rule,
    quote,
    rate,
)
from marketdata.dema import Bar
from marketdata.prices import ManualPrice, Observation, PriceType, Rule
from marketdata.rates import RateKind, RatePoint
from marketdata.refdata import RefInstrument

HISTORY = "iss_history"  # точка из истории торгов — на дату торгов
SETTLEMENT = "iss_settlement"  # точка из данных дня — на дату расчётов


class NotFoundError(Exception):
    def __init__(self, what: str, key: object) -> None:
        super().__init__(f"{what} {key} not found")
        self.what = what
        self.key = key


@dataclass(frozen=True, slots=True)
class InstrumentRecord:
    id: UUID
    secid: str
    isin: str | None
    kind: str
    short_name: str
    board: str | None
    history_till: date | None


@dataclass(frozen=True, slots=True)
class RuleRecord:
    valid_from: datetime
    rule: Rule
    author: str
    comment: str


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Сессия — чтобы событие ушло в той же транзакции, что и данные."""
        return self._session

    # --- бумаги -----------------------------------------------------------------

    async def sync_instruments(self, items: Sequence[RefInstrument]) -> None:
        """Копия списка бумаг справочника: новые добавляются, известные обновляются."""
        if not items:
            return
        statement = insert(instrument).values(
            [
                {
                    "id": item.id,
                    "secid": item.secid,
                    "isin": item.isin,
                    "kind": item.kind,
                    "short_name": item.short_name,
                }
                for item in items
            ]
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[instrument.c.id],
                set_={
                    "secid": statement.excluded.secid,
                    "isin": statement.excluded.isin,
                    "kind": statement.excluded.kind,
                    "short_name": statement.excluded.short_name,
                },
            )
        )

    async def instruments(self) -> list[InstrumentRecord]:
        result = await self._session.execute(select(instrument).order_by(instrument.c.secid))
        return [_instrument(row) for row in result]

    async def get(self, instrument_id: UUID) -> InstrumentRecord:
        row = (
            await self._session.execute(select(instrument).where(instrument.c.id == instrument_id))
        ).one_or_none()
        if row is None:
            raise NotFoundError("instrument", instrument_id)
        return _instrument(row)

    async def ids_by_code(self, codes: Iterable[str]) -> dict[str, UUID]:
        """Код биржи или ISIN (без учёта регистра) → id бумаги."""
        wanted = {code.strip().upper() for code in codes}
        result = await self._session.execute(
            select(instrument.c.id, instrument.c.secid, instrument.c.isin).where(
                or_(
                    func.upper(instrument.c.secid).in_(wanted),
                    func.upper(instrument.c.isin).in_(wanted),
                )
            )
        )
        found: dict[str, UUID] = {}
        for row in result:
            for code in (row.secid, row.isin):
                if code and code.upper() in wanted:
                    found[code.upper()] = row.id
        return found

    async def set_board(self, instrument_id: UUID, board: str | None) -> None:
        await self._session.execute(
            update(instrument).where(instrument.c.id == instrument_id).values(board=board)
        )

    async def claim_history(self, instrument_id: UUID, now: datetime, stale: timedelta) -> bool:
        """Забирает загрузку истории ISS по бумаге; зависшую дольше ``stale`` — заново."""
        result = await self._session.execute(
            update(instrument)
            .where(
                instrument.c.id == instrument_id,
                or_(
                    instrument.c.history_claimed_at.is_(None),
                    instrument.c.history_claimed_at < now - stale,
                ),
            )
            .values(history_claimed_at=now)
            .returning(instrument.c.id)
        )
        return result.one_or_none() is not None

    async def finish_history(self, instrument_id: UUID, till: date | None) -> None:
        """Снимает захват; ``till`` — история загружена по эту дату (None — не загружена)."""
        values: dict[str, Any] = {"history_claimed_at": None}
        if till is not None:
            values["history_till"] = till
        await self._session.execute(
            update(instrument).where(instrument.c.id == instrument_id).values(**values)
        )

    # --- цены -------------------------------------------------------------------

    async def add_quotes(self, rows: Sequence[dict[str, Any]]) -> None:
        if rows:
            await self._session.execute(insert(quote), list(rows))

    async def latest_quotes(
        self, instrument_ids: Sequence[UUID], at: datetime
    ) -> dict[UUID, list[Observation]]:
        """Последняя на момент ``at`` цена каждого типа из каждого источника."""
        if not instrument_ids:
            return {}
        result = await self._session.execute(
            select(
                quote.c.instrument_id,
                quote.c.price_type,
                quote.c.source,
                quote.c.observed_at,
                quote.c.value,
            )
            .where(quote.c.instrument_id.in_(instrument_ids), quote.c.observed_at <= at)
            .ext(distinct_on(quote.c.instrument_id, quote.c.price_type, quote.c.source))
            .order_by(
                quote.c.instrument_id,
                quote.c.price_type,
                quote.c.source,
                quote.c.observed_at.desc(),
                quote.c.received_at.desc(),
            )
        )
        found: dict[UUID, list[Observation]] = {}
        for row in result:
            found.setdefault(row.instrument_id, []).append(_observation(row))
        return found

    async def quotes_between(
        self, instrument_id: UUID, since: datetime, till: datetime
    ) -> list[Observation]:
        """Цены за период; из повторных — последняя полученная."""
        result = await self._session.execute(
            select(quote.c.price_type, quote.c.source, quote.c.observed_at, quote.c.value)
            .where(
                quote.c.instrument_id == instrument_id,
                quote.c.observed_at >= since,
                quote.c.observed_at <= till,
            )
            .ext(distinct_on(quote.c.price_type, quote.c.source, quote.c.observed_at))
            .order_by(
                quote.c.price_type,
                quote.c.source,
                quote.c.observed_at,
                quote.c.received_at.desc(),
            )
        )
        return sorted((_observation(row) for row in result), key=lambda o: o.time)

    # --- бары -------------------------------------------------------------------

    async def add_bars(self, rows: Sequence[dict[str, Any]]) -> None:
        if rows:
            await self._session.execute(insert(bar), list(rows))

    async def bars(
        self,
        instrument_id: UUID,
        timeframe: int,
        *,
        ending_by: datetime,
        starting_from: datetime | None = None,
    ) -> list[Bar]:
        """Бары таймфрейма, закрытые к ``ending_by``; из повторных — последний полученный."""
        conditions = [
            bar.c.instrument_id == instrument_id,
            bar.c.timeframe == timeframe,
            bar.c.start_at <= ending_by - timedelta(minutes=timeframe),
        ]
        if starting_from is not None:
            conditions.append(bar.c.start_at >= starting_from)
        result = await self._session.execute(
            select(bar)
            .where(*conditions)
            .ext(distinct_on(bar.c.start_at))
            .order_by(bar.c.start_at, bar.c.received_at.desc())
        )
        return [
            Bar(
                start=row.start_at,
                timeframe=row.timeframe,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
            )
            for row in result
        ]

    async def bar_timeframes(self, instrument_id: UUID) -> list[int]:
        result = await self._session.execute(
            select(bar.c.timeframe)
            .where(bar.c.instrument_id == instrument_id)
            .distinct()
            .order_by(bar.c.timeframe)
        )
        return [row.timeframe for row in result]

    # --- НКД и номинал ----------------------------------------------------------

    async def upsert_points(
        self, table: Table, rows: Sequence[dict[str, Any]]
    ) -> list[tuple[UUID, date]]:
        """Записывает точки; возвращает новые и изменившиеся — бумага и дата.

        Та же точка с тем же значением не перезаписывается: данные дня ISS опрашиваются
        каждые 5 минут, а пересчёт нужен, только когда значение новое.
        """
        # Одна точка на ключ: ISS может прислать дату дважды — действует последняя.
        unique = {(r["instrument_id"], r["as_of"], r["source"]): r for r in rows}
        if not unique:
            return []
        statement = insert(table).values(list(unique.values()))
        result = await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c.instrument_id, table.c.as_of, table.c.source],
                set_={"value": statement.excluded.value, "received_at": func.now()},
                where=table.c.value != statement.excluded.value,
            ).returning(table.c.instrument_id, table.c.as_of)
        )
        return [(row.instrument_id, row.as_of) for row in result]

    async def points(self, table: Table, instrument_id: UUID) -> dict[date, Decimal]:
        """Точки ISS по датам; на одну дату история торгов главнее данных дня."""
        result = await self._session.execute(
            select(table.c.as_of, table.c.source, table.c.value).where(
                table.c.instrument_id == instrument_id
            )
        )
        found: dict[date, Decimal] = {}
        for row in sorted(result, key=lambda r: (r.as_of, r.source == HISTORY)):
            found[row.as_of] = row.value
        return found

    # --- денежные ставки --------------------------------------------------------

    async def upsert_rates(self, rows: Sequence[dict[str, Any]]) -> None:
        unique = {(r["kind"], r["as_of"], r["source"]): r for r in rows}
        if not unique:
            return
        statement = insert(rate).values(list(unique.values()))
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[rate.c.kind, rate.c.as_of, rate.c.source],
                set_={
                    "value": statement.excluded.value,
                    "received_at": statement.excluded.received_at,
                    "batch_id": statement.excluded.batch_id,
                },
            )
        )

    async def rates(self, kinds: Iterable[RateKind], till: date) -> list[RatePoint]:
        """Ставки не позже даты: по каждой ставке — все значения последней известной даты."""
        latest = (
            select(rate.c.kind, func.max(rate.c.as_of).label("as_of"))
            .where(rate.c.kind.in_([k.value for k in kinds]), rate.c.as_of <= till)
            .group_by(rate.c.kind)
            .subquery()
        )
        result = await self._session.execute(
            select(rate).join(
                latest, (rate.c.kind == latest.c.kind) & (rate.c.as_of == latest.c.as_of)
            )
        )
        return [
            RatePoint(RateKind(row.kind), row.as_of, row.value, row.source, row.received_at)
            for row in result
        ]

    async def last_rate_date(self, kind: RateKind, source: str) -> date | None:
        result = await self._session.execute(
            select(func.max(rate.c.as_of)).where(rate.c.kind == kind.value, rate.c.source == source)
        )
        last: date | None = result.scalar_one()
        return last

    # --- правила оценки ---------------------------------------------------------

    async def rules(self, instrument_ids: Sequence[UUID]) -> dict[UUID, list[RuleRecord]]:
        """История правил по бумагам, от ранних к поздним."""
        if not instrument_ids:
            return {}
        result = await self._session.execute(
            select(price_rule)
            .where(price_rule.c.instrument_id.in_(instrument_ids))
            .order_by(price_rule.c.instrument_id, price_rule.c.valid_from)
        )
        found: dict[UUID, list[RuleRecord]] = {}
        for row in result:
            found.setdefault(row.instrument_id, []).append(
                RuleRecord(
                    valid_from=row.valid_from,
                    rule=Rule(
                        types=tuple(PriceType(t) for t in row.price_types),
                        dema_timeframe=row.dema_timeframe,
                        dema_half_life=row.dema_half_life,
                        spread_threshold_bp=row.spread_threshold_bp,
                    ),
                    author=row.author,
                    comment=row.comment,
                )
            )
        return found

    async def add_rule(self, instrument_id: UUID, record: RuleRecord) -> None:
        rule = record.rule
        await self._session.execute(
            insert(price_rule).values(
                instrument_id=instrument_id,
                valid_from=record.valid_from,
                price_types=[t.value for t in rule.types],
                dema_timeframe=rule.dema_timeframe,
                dema_half_life=rule.dema_half_life,
                spread_threshold_bp=rule.spread_threshold_bp,
                author=record.author,
                comment=record.comment,
            )
        )

    # --- ручные цены ------------------------------------------------------------

    async def add_manual(self, instrument_id: UUID, price: ManualPrice) -> None:
        await self._session.execute(
            insert(manual_price).values(
                instrument_id=instrument_id,
                value=price.value,
                entered_at=price.entered_at,
                author=price.author,
                comment=price.comment,
            )
        )

    async def latest_manual(
        self, instrument_ids: Sequence[UUID], at: datetime
    ) -> dict[UUID, ManualPrice]:
        """Последняя введённая к ``at`` ручная цена по каждой бумаге."""
        if not instrument_ids:
            return {}
        result = await self._session.execute(
            select(manual_price)
            .where(
                manual_price.c.instrument_id.in_(instrument_ids), manual_price.c.entered_at <= at
            )
            .ext(distinct_on(manual_price.c.instrument_id))
            .order_by(
                manual_price.c.instrument_id,
                manual_price.c.entered_at.desc(),
                manual_price.c.id.desc(),
            )
        )
        return {row.instrument_id: _manual(row) for row in result}

    async def manual_between(
        self, instrument_id: UUID, since: datetime, till: datetime
    ) -> list[ManualPrice]:
        result = await self._session.execute(
            select(manual_price)
            .where(
                manual_price.c.instrument_id == instrument_id,
                manual_price.c.entered_at >= since,
                manual_price.c.entered_at <= till,
            )
            .order_by(manual_price.c.entered_at, manual_price.c.id)
        )
        return [_manual(row) for row in result]

    # --- пакеты скрипта ---------------------------------------------------------

    async def add_batch(self, values: dict[str, Any]) -> None:
        await self._session.execute(insert(feed_batch).values(**values))

    async def recent_batches(self, limit: int) -> list[Row[Any]]:
        result = await self._session.execute(
            select(feed_batch).order_by(feed_batch.c.received_at.desc()).limit(limit)
        )
        return list(result)

    # --- опрос ISS внутри дня ---------------------------------------------------

    async def claim_poll(self, slot: datetime) -> bool:
        """Забирает 5-минутный слот опроса ISS: его выполнит один экземпляр сервиса."""
        result = await self._session.execute(
            insert(iss_poll)
            .values(slot=slot)
            .on_conflict_do_nothing(index_elements=[iss_poll.c.slot])
            .returning(iss_poll.c.slot)
        )
        return result.one_or_none() is not None

    async def finish_poll(self, slot: datetime, now: datetime, polled: int, failed: int) -> None:
        await self._session.execute(
            update(iss_poll)
            .where(iss_poll.c.slot == slot)
            .values(finished_at=now, polled=polled, failed=failed)
        )


def _instrument(row: Row[Any]) -> InstrumentRecord:
    return InstrumentRecord(
        id=row.id,
        secid=row.secid,
        isin=row.isin,
        kind=row.kind,
        short_name=row.short_name,
        board=row.board,
        history_till=row.history_till,
    )


def _observation(row: Any) -> Observation:
    return Observation(
        type=PriceType(row.price_type), source=row.source, time=row.observed_at, value=row.value
    )


def _manual(row: Row[Any]) -> ManualPrice:
    return ManualPrice(
        value=row.value, entered_at=row.entered_at, author=row.author, comment=row.comment
    )
