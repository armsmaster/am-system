"""HTTP API рыночных данных. Правила — в docs/design/03-marketdata.md."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, Field, StringConstraints

from am_common.app import AppResources, DbSession
from am_common.auth import CurrentPrincipal, Principal, Role, require_any_role
from am_common.clock import MOSCOW
from am_common.events import Actor
from am_quant.bond import Horizon, HorizonKind, QuantError
from am_quant.yields import BASIS_POINTS
from marketdata.accrual import OnDate
from marketdata.analytics import Analytics, BondType, Inputs, PriceBasis, SpreadView, analytics
from marketdata.commands import Commands, refresh_instruments
from marketdata.feed import (
    BarsIn,
    FeedResult,
    QuotesIn,
    RatesIn,
    Rejected,
    ingest_bars,
    ingest_quotes,
    ingest_rates,
)
from marketdata.prices import Choice, PriceType, Rule, RuleViolationError
from marketdata.queries import (
    PriceView,
    accrued_on_date,
    face_value_on_date,
    marks,
    price_views,
    series,
)
from marketdata.rates import RATE_LABELS, RateKind, RatePoint, rate_on
from marketdata.refdata import RefdataClient, RefdataUnavailableError, refdata_client
from marketdata.repository import NotFoundError, Repository, RuleRecord
from marketdata.settings import MarketdataSettings

Feed = Annotated[Principal, Depends(require_any_role(Role.PRICE_FEED))]
Trading = Annotated[Principal, Depends(require_any_role(Role.TRADING))]
# Смотреть цены могут все роли; присылать — только скрипт; править — трейдинг.
Viewer = CurrentPrincipal
Comment = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
At = Annotated[AwareDatetime | None, Query(description="Момент; по умолчанию — сейчас")]
# Отметки за период — не больше десяти лет за запрос.
MAX_MARK_DAYS = 3660

router = APIRouter()


def get_refdata(request: Request, resources: AppResources) -> RefdataClient:
    """Клиент справочника живёт всё время работы сервиса: он хранит токен и кэш."""
    client: RefdataClient | None = getattr(request.app.state, "refdata", None)
    if client is None:
        client = request.app.state.refdata = refdata_client(resources)
    return client


Refdata = Annotated[RefdataClient, Depends(get_refdata)]


def get_now(request: Request) -> datetime:
    clock: Callable[[], datetime] = request.app.state.clock
    return clock()


Now = Annotated[datetime, Depends(get_now)]


# --- схемы ----------------------------------------------------------------------


class InstrumentOut(BaseModel):
    id: UUID
    secid: str
    isin: str | None
    kind: str
    short_name: str


class ChoiceOut(BaseModel):
    value: Decimal
    type: str
    source: str
    time: datetime
    stale: bool
    reason: str


class CandidateOut(BaseModel):
    type: PriceType
    source: str
    value: Decimal
    time: datetime
    today: bool


class RuleOut(BaseModel):
    price_types: list[PriceType]
    dema_timeframe: int | None
    dema_half_life: Decimal | None
    spread_threshold_bp: int


class PriceOut(BaseModel):
    instrument: InstrumentOut
    at: datetime
    trading_day: date
    price: ChoiceOut | None
    candidates: list[CandidateOut]
    rule: RuleOut
    spread_bp: Decimal | None
    wide_spread: bool


class QuoteOut(BaseModel):
    type: PriceType
    source: str
    time: datetime
    value: Decimal


class BarOut(BaseModel):
    start: datetime
    end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


class DemaOut(BaseModel):
    time: datetime
    value: Decimal


class ManualOut(BaseModel):
    value: Decimal
    entered_at: datetime
    author: str
    comment: str


class RuleRecordOut(RuleOut):
    valid_from: datetime
    author: str
    comment: str


class SeriesOut(BaseModel):
    instrument: InstrumentOut
    quotes: list[QuoteOut]
    bars: dict[int, list[BarOut]]  # таймфрейм в минутах → бары
    dema: list[DemaOut]
    manual: list[ManualOut]
    rules: list[RuleRecordOut]


class OnDateOut(BaseModel):
    date: date
    value: Decimal
    method: str
    note: str


class MarkOut(BaseModel):
    """Отметка для оценки позиции на конец дня (design/06)."""

    date: date
    price: ChoiceOut | None  # цена для оценки на 23:59:59 МСК
    settle_date: dt.date  # T+1: расчёты по сделке, заключённой в этот день
    accrued: Decimal | None  # облигация: НКД на дату расчётов T+1
    face_value: Decimal | None  # облигация: номинал на день


class RateOut(BaseModel):
    rate: RateKind
    label: str
    date: date | None  # дата значения; None — ставки ещё нет
    value: Decimal | None  # % годовых
    source: str | None  # iss или источник скрипта


class ProblemOut(BaseModel):
    code: str
    message: str


class HorizonOut(BaseModel):
    kind: HorizonKind
    date: date
    price: Decimal  # % от непогашенного номинала


class YieldOut(BaseModel):
    horizon: HorizonOut
    yield_percent: Decimal | None = None
    macaulay_years: Decimal | None = None
    macaulay_days: Decimal | None = None
    modified_duration: Decimal | None = None
    dv01: Decimal | None = None  # ₽ на бумагу за 1 бп
    convexity: Decimal | None = None
    problem: ProblemOut | None = None  # почему к этому горизонту не посчитать


class BreakdownOut(BaseModel):
    basis_bp: Decimal
    capitalization_bp: Decimal
    margin_bp: Decimal  # у линкера — купон
    other_bp: Decimal
    price_bp: Decimal


class SpreadOut(BaseModel):
    horizon: HorizonOut
    rate: RateKind | None  # плавающая ставка флоатера; у линкера — None
    margin_bp: Decimal | None
    capitalized: bool | None
    funding: RateOut  # RUONIA
    funding_percent: Decimal  # эффективная годовая ставка фондирования
    index: RateOut | None  # ставка индекса флоатера
    spread_bp: Decimal
    yield_percent: Decimal  # фондирование + спред
    breakdown: BreakdownOut
    spread_duration: Decimal
    spread_dv01: Decimal  # ₽ на бумагу за 1 бп спреда


class PriceUsedOut(BaseModel):
    basis: PriceBasis
    clean: Decimal  # % от непогашенного номинала
    face_value: Decimal
    accrued: Decimal
    dirty: Decimal
    valuation: ChoiceOut | None  # цена для оценки, если расчёт — по ней


class AnalyticsOut(BaseModel):
    instrument: InstrumentOut
    at: datetime
    trading_day: date
    settlement_date: date
    type: BondType
    price: PriceUsedOut
    yields: list[YieldOut]
    spread: SpreadOut | None


class BatchOut(BaseModel):
    id: UUID
    client: str
    source: str
    kind: str
    received_at: datetime
    accepted: int
    rejected: list[Rejected]


class RuleIn(BaseModel):
    price_types: Annotated[list[PriceType], Field(min_length=1)]
    dema_timeframe: int | None = None
    dema_half_life: Annotated[Decimal, Field(max_digits=10, decimal_places=2)] | None = None
    spread_threshold_bp: int = 50
    comment: Comment


class ManualIn(BaseModel):
    value: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
    comment: Comment


# --- приём от скрипта -------------------------------------------------------------


@router.post("/feed/v1/quotes", tags=["feed"])
async def feed_quotes(
    batch: QuotesIn,
    *,
    client: Feed,
    session: DbSession,
    refdata: Refdata,
    resources: AppResources,
    now: Now,
) -> FeedResult:
    """Снимки цен от скрипта (docs/api/price-feed)."""
    ids = await _resolve(session, refdata, resources, {q.code for q in batch.quotes})
    async with session.begin():
        return await ingest_quotes(
            Repository(session), batch, ids, Actor.from_principal(client), now
        )


@router.post("/feed/v1/bars", tags=["feed"])
async def feed_bars(
    batch: BarsIn,
    *,
    client: Feed,
    session: DbSession,
    refdata: Refdata,
    resources: AppResources,
    now: Now,
) -> FeedResult:
    """OHLC-бары от скрипта (docs/api/price-feed)."""
    ids = await _resolve(session, refdata, resources, {b.code for b in batch.bars})
    async with session.begin():
        return await ingest_bars(Repository(session), batch, ids, Actor.from_principal(client), now)


@router.post("/feed/v1/rates", tags=["feed"])
async def feed_rates(batch: RatesIn, *, client: Feed, session: DbSession, now: Now) -> FeedResult:
    """Ставки от скрипта: RUONIA, а при необходимости — ключевая ставка и RUSFAR."""
    async with session.begin():
        return await ingest_rates(Repository(session), batch, client.username, now)


@router.get("/feed/batches", tags=["feed"])
async def batches(
    _user: Viewer, session: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[BatchOut]:
    """Последние пакеты от скрипта и что в них отклонено."""
    rows = await Repository(session).recent_batches(limit)
    return [
        BatchOut(
            id=row.id,
            client=row.client,
            source=row.source,
            kind=row.kind,
            received_at=row.received_at,
            accepted=row.accepted,
            rejected=[Rejected.model_validate(item) for item in row.rejected],
        )
        for row in rows
    ]


# --- цены -----------------------------------------------------------------------


@router.get("/overview", tags=["prices"])
async def overview(
    _user: Viewer, session: DbSession, refdata: Refdata, now: Now, at: At = None
) -> list[PriceOut]:
    """Все бумаги: цена для оценки, цены всех типов и разброс между ними."""
    return [_price_out(v) for v in await price_views(session, refdata, at or now)]


@router.get("/instruments/{instrument_id}/price", tags=["prices"])
async def price(
    instrument_id: UUID,
    _user: Viewer,
    session: DbSession,
    refdata: Refdata,
    now: Now,
    at: At = None,
) -> PriceOut:
    """Цена для оценки на момент и все цены-кандидаты."""
    [view] = await price_views(session, refdata, at or now, [instrument_id])
    return _price_out(view)


@router.get("/instruments/{instrument_id}/series", tags=["prices"])
async def price_series(
    instrument_id: UUID,
    _user: Viewer,
    session: DbSession,
    now: Now,
    since: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    till: Annotated[AwareDatetime | None, Query(alias="to")] = None,
) -> SeriesOut:
    """Цены, бары и DEMA для графика; по умолчанию — последние 5 дней."""
    end = till or now
    view = await series(session, instrument_id, since or end - timedelta(days=5), end)
    return SeriesOut(
        instrument=_instrument_out(view.instrument),
        quotes=[
            QuoteOut(type=o.type, source=o.source, time=o.time, value=o.value) for o in view.quotes
        ],
        bars={
            timeframe: [
                BarOut(start=b.start, end=b.end, open=b.open, high=b.high, low=b.low, close=b.close)
                for b in items
            ]
            for timeframe, items in view.bars.items()
        },
        dema=[DemaOut(time=p.time, value=_six(p.value)) for p in view.dema],
        manual=[ManualOut(**_fields(m)) for m in view.manual],
        rules=[_rule_record_out(r) for r in view.rules],
    )


@router.get("/instruments/{instrument_id}/accrued-interest", tags=["prices"])
async def accrued_interest(
    instrument_id: UUID,
    _user: Viewer,
    session: DbSession,
    refdata: Refdata,
    day: Annotated[date, Query(alias="date")],
) -> OnDateOut:
    """НКД на дату и откуда он: точка ISS или досчёт (решение 73)."""
    return _on_date(day, await accrued_on_date(session, refdata, instrument_id, day), "НКД")


@router.get("/instruments/{instrument_id}/marks", tags=["prices"])
async def valuation_marks(
    instrument_id: UUID,
    _user: Viewer,
    session: DbSession,
    refdata: Refdata,
    first: Annotated[date, Query(alias="from")],
    last: Annotated[date, Query(alias="till")],
) -> list[MarkOut]:
    """Цена, НКД на T+1 и номинал на каждый день периода — для оценки позиций."""
    if last < first or (last - first).days > MAX_MARK_DAYS:
        raise QuantError(
            "bad_period", f"Период — от одного дня до {MAX_MARK_DAYS} дней, конец не раньше начала."
        )
    return [
        MarkOut(
            date=m.day,
            price=_choice_out(m.price),
            settle_date=m.settle_date,
            accrued=None if m.accrued is None else m.accrued.value,
            face_value=None if m.face is None else m.face.value,
        )
        for m in await marks(session, refdata, instrument_id, first, last)
    ]


@router.get("/instruments/{instrument_id}/face-value", tags=["prices"])
async def face_value(
    instrument_id: UUID,
    _user: Viewer,
    session: DbSession,
    day: Annotated[date, Query(alias="date")],
) -> OnDateOut:
    """Номинал на дату и откуда он: точка ISS или досчёт (решение 63)."""
    return _on_date(day, await face_value_on_date(session, instrument_id, day), "номинал")


# --- ставки и расчёты -----------------------------------------------------------


@router.get("/rates", tags=["analytics"])
async def rates(
    _user: Viewer,
    session: DbSession,
    now: Now,
    day: Annotated[date | None, Query(alias="date", description="По умолчанию — сегодня")] = None,
) -> list[RateOut]:
    """Ставки на дату: последняя известная по каждой, её дата и источник."""
    on = day or now.astimezone(MOSCOW).date()
    points = await Repository(session).rates(list(RateKind), on)
    return [
        _rate_out(kind, rate_on((p for p in points if p.kind is kind), on)) for kind in RateKind
    ]


@router.get("/instruments/{instrument_id}/analytics", tags=["analytics"])
async def bond_analytics(
    instrument_id: UUID,
    *,
    _user: Viewer,
    session: DbSession,
    refdata: Refdata,
    now: Now,
    at: At = None,
    price: Annotated[Decimal | None, Query(gt=0, le=1000, description="Чистая цена, %")] = None,
    yield_percent: Annotated[
        Decimal | None, Query(alias="yield", gt=-99, lt=1000, description="Доходность, %")
    ] = None,
    spread_bp: Annotated[
        Decimal | None, Query(alias="spread", ge=-5000, le=10000, description="Спред, бп")
    ] = None,
) -> AnalyticsOut:
    """Доходность и метрики или вменённый спред по цене для оценки (design/04).

    «Что если»: задайте одно из ``price``, ``yield`` или ``spread``.
    """
    given = [v for v in (price, yield_percent, spread_bp) if v is not None]
    if len(given) > 1:
        raise QuantError("one_input", "Задайте что-то одно: цену, доходность или спред.")
    inputs = Inputs(price=price, yield_percent=yield_percent, spread_bp=spread_bp)
    return _analytics_out(await analytics(session, refdata, instrument_id, at or now, inputs))


# --- правило и ручная цена ------------------------------------------------------


@router.get("/instruments/{instrument_id}/rules", tags=["rules"])
async def rules(instrument_id: UUID, _user: Viewer, session: DbSession) -> list[RuleRecordOut]:
    repo = Repository(session)
    await repo.get(instrument_id)
    return [_rule_record_out(r) for r in (await repo.rules([instrument_id])).get(instrument_id, [])]


@router.put("/instruments/{instrument_id}/rule", tags=["rules"])
async def set_rule(
    instrument_id: UUID, body: RuleIn, user: Trading, session: DbSession, now: Now
) -> RuleRecordOut:
    """Правило оценки бумаги; действует с момента сохранения (решение 70)."""
    rule = Rule(
        types=tuple(body.price_types),
        dema_timeframe=body.dema_timeframe,
        dema_half_life=body.dema_half_life,
        spread_threshold_bp=body.spread_threshold_bp,
    )
    saved = await Commands(session, Actor.from_principal(user), clock=lambda: now).set_rule(
        instrument_id, rule, body.comment
    )
    return _rule_record_out(saved)


@router.post("/instruments/{instrument_id}/manual-prices", status_code=201, tags=["rules"])
async def manual_price(
    instrument_id: UUID, body: ManualIn, user: Trading, session: DbSession, now: Now
) -> ManualOut:
    """Ручная цена до конца торгового дня (решения 67 и 70)."""
    commands = Commands(session, Actor.from_principal(user), clock=lambda: now)
    return ManualOut(
        **_fields(await commands.set_manual_price(instrument_id, body.value, body.comment))
    )


# --- ошибки → ответы HTTP -------------------------------------------------------


def install_error_handlers(app: FastAPI) -> None:
    def problem(status_code: int, code: str, message: str) -> Response:
        return JSONResponse(
            status_code=status_code, content={"detail": {"code": code, "message": message}}
        )

    async def rule_violation(_request: Request, exc: Exception) -> Response:
        violation = cast(RuleViolationError, exc)
        return problem(422, violation.code, violation.message)

    async def not_found(_request: Request, exc: Exception) -> Response:
        missing = cast(NotFoundError, exc)
        return problem(404, "not_found", f"Не найдено: {missing.what}")

    async def refdata_unavailable(_request: Request, _exc: Exception) -> Response:
        return problem(503, "refdata_unavailable", "Справочник недоступен. Повторите позже.")

    async def not_computable(_request: Request, exc: Exception) -> Response:
        missing = cast(QuantError, exc)
        return problem(422, missing.code, missing.message)

    app.add_exception_handler(RuleViolationError, rule_violation)
    app.add_exception_handler(NotFoundError, not_found)
    app.add_exception_handler(RefdataUnavailableError, refdata_unavailable)
    app.add_exception_handler(QuantError, not_computable)


def install_feed_limit(app: FastAPI, settings: MarketdataSettings) -> None:
    """Пакет от скрипта больше лимита отклоняем до разбора (413)."""

    @app.middleware("http")
    async def limit(request: Request, call_next: Any) -> Response:
        size = request.headers.get("content-length")
        if "/feed/" in request.url.path and size and int(size) > settings.feed_max_bytes:
            return JSONResponse(
                status_code=413,
                content={
                    "detail": {
                        "code": "too_large",
                        "message": "Пакет больше 5 МБ — разбейте его на части.",
                    }
                },
            )
        response: Response = await call_next(request)
        return response


# --- вспомогательное ------------------------------------------------------------


async def _resolve(
    session: DbSession, refdata: RefdataClient, resources: AppResources, codes: set[str]
) -> dict[str, UUID]:
    """Коды бумаг → id; неизвестные коды — повод обновить список бумаг из справочника."""
    async with session.begin():
        ids = await Repository(session).ids_by_code(codes)
    if len(ids) < len({c.upper() for c in codes}):
        try:
            await refresh_instruments(resources.require_database(), refdata)
        except RefdataUnavailableError:
            return ids
        async with session.begin():
            ids = await Repository(session).ids_by_code(codes)
    return ids


def _price_out(view: PriceView) -> PriceOut:
    return PriceOut(
        instrument=_instrument_out(view.instrument),
        at=view.at,
        trading_day=view.trading_day,
        price=_choice_out(view.price),
        candidates=[
            CandidateOut(
                type=c.type, source=c.source, value=_six(c.value), time=c.time, today=c.today
            )
            for c in view.candidates
        ],
        rule=_rule_out(view.rule),
        spread_bp=view.spread_bp,
        wide_spread=view.wide_spread,
    )


def _choice_out(choice: Choice | None) -> ChoiceOut | None:
    if choice is None:
        return None
    return ChoiceOut(
        value=_six(choice.value),
        type=choice.type,
        source=choice.source,
        time=choice.time,
        stale=choice.stale,
        reason=choice.reason,
    )


def _rate_out(kind: RateKind, point: RatePoint | None) -> RateOut:
    return RateOut(
        rate=kind,
        label=RATE_LABELS[kind],
        date=None if point is None else point.as_of,
        value=None if point is None else point.value,
        source=None if point is None else point.source,
    )


def _horizon_out(horizon: Horizon) -> HorizonOut:
    return HorizonOut(kind=horizon.kind, date=horizon.date, price=horizon.price)


def _analytics_out(found: Analytics) -> AnalyticsOut:
    yields: list[YieldOut] = []
    for item in found.yields:
        if item.result is None:
            problem = cast(QuantError, item.problem)
            yields.append(
                YieldOut(
                    horizon=_horizon_out(item.horizon),
                    problem=ProblemOut(code=problem.code, message=problem.message),
                )
            )
            continue
        metrics = item.result.risk
        yields.append(
            YieldOut(
                horizon=_horizon_out(item.horizon),
                yield_percent=_six(item.result.yield_rate * 100),
                macaulay_years=_six(metrics.macaulay),
                macaulay_days=_places(metrics.macaulay_days, 2),
                modified_duration=_six(metrics.modified),
                dv01=_six(metrics.dv01),
                convexity=_six(metrics.convexity),
            )
        )
    return AnalyticsOut(
        instrument=_instrument_out(found.instrument),
        at=found.at,
        trading_day=found.trading_day,
        settlement_date=found.settlement,
        type=found.type,
        price=PriceUsedOut(
            basis=found.basis,
            clean=_six(found.clean),
            face_value=_six(found.face_value),
            accrued=_six(found.accrued),
            dirty=_six(found.dirty),
            valuation=_choice_out(found.valuation),
        ),
        yields=yields,
        spread=None if found.spread is None else _spread_out(found.spread),
    )


def _spread_out(view: SpreadView) -> SpreadOut:
    result, spec = view.result, view.terms.floater
    parts = result.breakdown
    return SpreadOut(
        horizon=_horizon_out(result.horizon),
        rate=None if spec is None else spec.rate,
        margin_bp=None if spec is None else _bp(spec.margin),
        capitalized=None if spec is None else spec.capitalized,
        funding=_rate_out(view.funding.kind, view.funding),
        funding_percent=_six(result.funding * 100),
        index=None if view.index is None else _rate_out(view.index.kind, view.index),
        spread_bp=_bp(result.spread),
        yield_percent=_six((result.funding + result.spread) * 100),
        breakdown=BreakdownOut(
            basis_bp=_bp(parts.basis),
            capitalization_bp=_bp(parts.capitalization),
            margin_bp=_bp(parts.margin),
            other_bp=_bp(parts.other),
            price_bp=_bp(parts.price),
        ),
        spread_duration=_six(result.risk.modified),
        spread_dv01=_six(result.risk.dv01),
    )


def _instrument_out(record: Any) -> InstrumentOut:
    return InstrumentOut(
        id=record.id,
        secid=record.secid,
        isin=record.isin,
        kind=record.kind,
        short_name=record.short_name,
    )


def _rule_out(rule: Rule) -> RuleOut:
    return RuleOut(
        price_types=list(rule.types),
        dema_timeframe=rule.dema_timeframe,
        dema_half_life=rule.dema_half_life,
        spread_threshold_bp=rule.spread_threshold_bp,
    )


def _rule_record_out(record: RuleRecord) -> RuleRecordOut:
    return RuleRecordOut(
        **_rule_out(record.rule).model_dump(),
        valid_from=record.valid_from,
        author=record.author,
        comment=record.comment,
    )


def _fields(price: Any) -> dict[str, Any]:
    return {
        "value": price.value,
        "entered_at": price.entered_at,
        "author": price.author,
        "comment": price.comment,
    }


def _on_date(day: date, found: OnDate | None, what: str) -> OnDateOut:
    if found is None:
        raise NotFoundError(f"{what} на {day:%d.%m.%Y} — нет точек ISS, чтобы досчитать", day)
    return OnDateOut(date=day, value=found.value, method=found.method, note=found.note)


def _six(value: Decimal) -> Decimal:
    return _places(value, 6)


def _bp(value: Decimal) -> Decimal:
    return _places(value * BASIS_POINTS, 2)


def _places(value: Decimal, places: int) -> Decimal:
    # + 0 превращает «−0,00» в «0,00».
    return value.quantize(Decimal(1).scaleb(-places)) + 0
