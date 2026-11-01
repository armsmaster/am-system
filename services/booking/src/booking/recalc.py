"""Пересчёт задним числом (design/05, «Ролл и пересчёт задним числом»).

Изменение входов ставит фонд в очередь с даты, начиная с которой его
состояние могло измениться. Пересчёт — по фонду целиком: перевод связывает
его портфели (решения 37 и 98). Воркер забирает фонд (захват — только этот
экземпляр), роллит дни до сегодня и сохраняет снапшоты всех портфелей фонда;
прежние снапшоты остаются замещёнными. Запросы к справочнику и сервису цен —
вне транзакции.

Оценка (design/06): ролл берёт у сервиса цен отметки — цену для оценки на конец
дня, НКД на T+1, номинал — за весь пересчитываемый период одним запросом
на бумагу. В начале нового дня вчерашний день пересчитывается по ценам закрытия;
цены за прошлые дни, пришедшие позже, ставят фонды в очередь событием.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from am_common.app import Resources, Worker
from am_common.clock import MOSCOW, moscow_now
from am_common.consumer import StreamConsumer
from am_common.db import Database
from am_common.events import Event, stream_name
from booking.clients import (
    MarketdataClient,
    RefdataClient,
    ServiceUnavailableError,
    service_token,
)
from booking.repository import Claim, Repository, Snapshot
from booking.roll import (
    Contribution,
    FeeRate,
    InstrumentKind,
    Mark,
    MarksOn,
    Operation,
    OperationKind,
    Repo,
    Side,
    State,
    Trade,
    from_json,
    roll_fund_days,
    to_json,
)
from booking.settings import BookingSettings
from booking.terms import InstrumentHistory, TermsBook

logger = logging.getLogger(__name__)

# Пересчёт «с начала истории»: новая версия бумаги без даты начала.
BEGINNING = date(2000, 1, 1)


def refdata_client(resources: Resources) -> RefdataClient:
    settings = cast(BookingSettings, resources.settings)
    return RefdataClient(resources.http, settings.refdata_url, service_token(resources))


def marketdata_client(resources: Resources) -> MarketdataClient:
    settings = cast(BookingSettings, resources.settings)
    return MarketdataClient(resources.http, settings.marketdata_url, service_token(resources))


async def recalc_once(
    database: Database,
    refdata: RefdataClient,
    now: datetime,
    stale: timedelta,
    *,
    marketdata: MarketdataClient | None = None,
) -> UUID | None:
    """Пересчитывает один фонд из очереди; None — очередь пуста.

    ``marketdata`` — откуда брать отметки для оценки; без него ролл ведёт только позиции.
    """
    async with database.session() as session, session.begin():
        claim = await Repository(session).claim(now, stale)
    if claim is None:
        return None
    try:
        await recompute(database, refdata, claim, now, marketdata=marketdata)
    except (ServiceUnavailableError, SQLAlchemyError) as exc:
        # Захват снимаем сразу: следующая попытка не ждёт срока захвата.
        logger.warning("recalc of %s failed, will retry: %s", claim.fund_id, exc)
        async with database.session() as session, session.begin():
            await Repository(session).release(claim.fund_id)
        return None
    async with database.session() as session, session.begin():
        await Repository(session).finish(claim)
    return claim.fund_id


@dataclass(frozen=True, slots=True)
class FundInputs:
    """Вход ролла фонда: сделки по портфелям, операции, состояния накануне ``first_day``."""

    trades: dict[UUID, list[Trade]]
    operations: list[Operation]
    previous: list[Snapshot]
    first_day: date
    rates: list[FeeRate] = field(default_factory=list)
    repos: dict[UUID, list[Repo]] = field(default_factory=dict)

    @property
    def states(self) -> dict[UUID, State]:
        return {s.portfolio_id: from_json(s.state) for s in self.previous}


async def load_inputs(repo: Repository, fund_id: UUID, since: date, today: date) -> FundInputs:
    """Ролл начинается после последнего снапшота до ``since``; снапшотов нет —
    с первой сделки или операции фонда."""
    trade_rows = await repo.fund_trades(fund_id)
    operation_rows = await repo.fund_operations(fund_id)
    # Компенсационные взносы — не операции фонда, а часть своей сделки РЕПО.
    operations = [_operation(r) for r in operation_rows if r.kind != "repo_contribution"]
    contributions: dict[UUID, list[Contribution]] = defaultdict(list)
    for row in operation_rows:
        if row.kind == "repo_contribution":
            contributions[row.trade_id].append(_contribution(row))
    previous = await repo.fund_before(fund_id, since)
    trades: dict[UUID, list[Trade]] = defaultdict(list)
    repos: dict[UUID, list[Repo]] = defaultdict(list)
    for row in trade_rows:
        if row.kind == "repo":
            repos[row.portfolio_id].append(_repo(row, contributions.get(row.trade_id, [])))
        else:
            trades[row.portfolio_id].append(_trade(row))
    if previous:
        first_day = previous[0].day + timedelta(days=1)
    elif trade_rows or operations:
        first_day = min([r.trade_date for r in trade_rows] + [o.day for o in operations])
    else:
        # Ни сделок, ни операций — прежние снапшоты замещаются пустым запуском.
        first_day = today + timedelta(days=1)
    rates = [
        FeeRate(r.kind, r.rate, r.valid_from, r.portfolio_id) for r in await repo.fee_rates(fund_id)
    ]
    return FundInputs(dict(trades), operations, previous, first_day, rates, dict(repos))


def fund_instruments(inputs: FundInputs) -> list[UUID]:
    """Бумаги фонда: из сделок, переводов и позиций на начало ролла."""
    instruments = (
        {t.instrument_id for trades in inputs.trades.values() for t in trades}
        | {
            r.instrument_id
            for repos in inputs.repos.values()
            for r in repos
            if r.instrument_id is not None
        }
        | {o.instrument_id for o in inputs.operations if o.instrument_id is not None}
        | {i for state in inputs.states.values() for i in state.holdings}
        | {e.instrument_id for state in inputs.states.values() for e in state.pending}
    )
    return sorted(instruments, key=str)


async def terms_book(
    refdata: RefdataClient,
    inputs: FundInputs,
    till: date,
    rates: dict[str, Decimal] | None = None,
) -> tuple[TermsBook, dict[date, bool]]:
    """Условия бумаг фонда и торговый календарь от начала ролла до ``till``.
    ``rates`` — ставки индексов для прогноза купонов флоатеров (только лестница)."""
    histories: dict[UUID, InstrumentHistory] = {}
    for instrument_id in fund_instruments(inputs):
        history = await refdata.history(instrument_id)
        if history is not None:
            histories[instrument_id] = history
    exceptions = await refdata.calendar_around(inputs.first_day - timedelta(days=1), till)
    return TermsBook(histories, exceptions, rates), exceptions


async def marks_book(
    marketdata: MarketdataClient, instruments: Iterable[UUID], first: date, last: date
) -> MarksOn:
    """Отметки сервиса цен по бумагам за период — один запрос на бумагу."""
    found: dict[tuple[UUID, date], Mark] = {}
    if first <= last:
        for instrument_id in instruments:
            for day, mark in (await marketdata.marks(instrument_id, first, last)).items():
                found[(instrument_id, day)] = mark

    def marks_on(instrument_id: UUID, day: date) -> Mark:
        return found.get((instrument_id, day), Mark(price=None))

    return marks_on


async def recompute(
    database: Database,
    refdata: RefdataClient,
    claim: Claim,
    now: datetime,
    *,
    marketdata: MarketdataClient | None = None,
) -> None:
    today = now.astimezone(MOSCOW).date()
    async with database.session() as session:
        inputs = await load_inputs(Repository(session), claim.fund_id, claim.since, today)
    book, _ = await terms_book(refdata, inputs, today)
    marks = (
        None
        if marketdata is None
        else await marks_book(marketdata, fund_instruments(inputs), inputs.first_day, today)
    )
    days = roll_fund_days(
        inputs.states,
        inputs.first_day,
        today,
        trades=inputs.trades,
        operations=inputs.operations,
        marks_on=marks,
        rates=inputs.rates,
        terms_on=book,
        repos=inputs.repos,
    )
    replace_from = inputs.first_day if inputs.previous else min(claim.since, inputs.first_day)
    async with database.session() as session, session.begin():
        await Repository(session).save_run(
            {
                "id": uuid4(),
                "fund_id": claim.fund_id,
                "since": replace_from,
                "till": today,
                "started_at": now,
                "finished_at": moscow_now(),
            },
            replace_from,
            [
                (portfolio_id, state.day, to_json(state))
                for day in days
                for portfolio_id, state in sorted(day.items(), key=lambda item: str(item[0]))
            ],
            now,
        )


def _trade(row: Any) -> Trade:
    return Trade(
        id=row.trade_id,
        instrument_id=row.instrument_id,
        kind=InstrumentKind(row.instrument_kind),
        side=Side(row.side),
        quantity=row.quantity,
        price=row.price,
        face_value=row.face_value,
        accrued=row.accrued,
        amount=row.amount,
        fee=row.fee,
        trade_at=row.trade_at,
        trade_date=row.trade_date,
        settle_date=row.settle_date,
    )


def _repo(row: Any, contributions: list[Contribution]) -> Repo:
    """Сделка РЕПО: сторона первой части — продажа у прямого (design/07)."""
    return Repo(
        id=row.trade_id,
        instrument_id=row.instrument_id,
        kind=None if row.instrument_id is None else InstrumentKind(row.instrument_kind),
        direct=row.side == Side.SELL,
        quantity=row.quantity,
        trade_at=row.trade_at,
        trade_date=row.trade_date,
        first_date=row.settle_date,
        first_amount=row.amount,
        second_date=row.second_date,
        second_amount=row.second_amount,
        fee=row.fee,
        contributions=tuple(sorted(contributions, key=lambda c: (c.day, str(c.id)))),
    )


def _contribution(row: Any) -> Contribution:
    """Взнос со знаком для фонда: ``in`` — фонду, ``out`` — от фонда."""
    sign = 1 if row.direction == "in" else -1
    return Contribution(
        id=row.operation_id,
        day=row.op_date,
        cash=sign * (row.amount or Decimal(0)),
        quantity=sign * (row.quantity or 0),
    )


def _operation(row: Any) -> Operation:
    return Operation(
        id=row.operation_id,
        kind=OperationKind(row.kind),
        portfolio_id=row.portfolio_id,
        day=row.op_date,
        amount=row.amount or Decimal(0),
        units=row.units or Decimal(0),
        planned=row.planned_date,
        settled=row.settled_date,
        receiver_id=row.receiver_id,
        instrument_id=row.instrument_id,
        instrument_kind=None
        if row.instrument_kind is None
        else InstrumentKind(row.instrument_kind),
        quantity=row.quantity or 0,
        fee_kind=row.fee_kind,
        period_end=row.period_end,
    )


async def extend_to_today(database: Database, now: datetime) -> int:
    """Новый день: фонды досчитываются до сегодня — купоны идут по графику и без сделок.
    Последний посчитанный день пересчитывается заново: днём его оценивали по текущим
    ценам, теперь — по ценам закрытия."""
    today = now.astimezone(MOSCOW).date()
    async with database.session() as session, session.begin():
        repo = Repository(session)
        behind = {f: d for f, d in (await repo.last_days()).items() if d < today}
        for fund_id, last in behind.items():
            await repo.mark_dirty(fund_id, last, now)
    return len(behind)


class MarketEvents:
    """События соседей: новая версия бумаги и правка календаря в справочнике,
    цены за прошлые дни в сервисе цен."""

    def __init__(self, database: Database, clock: Callable[[], datetime] = moscow_now) -> None:
        self._database = database
        self._clock = clock

    async def handle(self, event: Event) -> None:
        if event.type == "instrument.version_added":
            valid_from = event.data.get("valid_from")
            since = BEGINNING if valid_from is None else date.fromisoformat(valid_from)
            await self._mark(since, instrument_id=UUID(event.entity_id))
        elif event.type in {"calendar.day_set", "calendar.day_cleared"}:
            await self._mark(date.fromisoformat(event.entity_id))
        elif event.type == "prices.backfilled":
            for item in event.data["instruments"]:
                await self._mark(date.fromisoformat(item["since"]), instrument_id=UUID(item["id"]))

    async def _mark(self, since: date, instrument_id: UUID | None = None) -> None:
        now = self._clock()
        async with self._database.session() as session, session.begin():
            repo = Repository(session)
            funds = (
                await repo.funds_with_activity()
                if instrument_id is None
                else await repo.funds_holding(instrument_id)
            )
            for fund_id in funds:
                await repo.mark_dirty(fund_id, since, now)


_ERRORS = (SQLAlchemyError, OSError)


def recalc_worker(settings: BookingSettings, clock: Callable[[], datetime] = moscow_now) -> Worker:
    async def recalc(resources: Resources) -> None:
        database = resources.require_database()
        refdata = refdata_client(resources)
        marketdata = marketdata_client(resources)
        stale = timedelta(seconds=settings.recalc_stale_seconds)
        day: date | None = None
        while True:
            try:
                now = clock()
                if now.astimezone(MOSCOW).date() != day:
                    await extend_to_today(database, now)
                    day = now.astimezone(MOSCOW).date()
                while await recalc_once(database, refdata, clock(), stale, marketdata=marketdata):
                    pass
            except _ERRORS as exc:
                logger.warning("recalc failed, will retry: %s", exc)
            await asyncio.sleep(settings.recalc_seconds)

    return recalc


def market_consumer(settings: BookingSettings) -> Worker:
    async def market_events(resources: Resources) -> None:
        consumer = StreamConsumer(
            resources.require_redis(),
            streams=[stream_name("refdata"), stream_name("marketdata")],
            group=settings.consumer_group,
            consumer=settings.consumer_name,
            handler=MarketEvents(resources.require_database()).handle,
        )
        await consumer.run()

    return market_events
