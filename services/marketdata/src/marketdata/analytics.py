"""Расчёт по облигации на момент: доходность и метрики или вменённый спред (design/04).

Условия выпуска — из справочника на дату расчётов S (следующий торговый день
после дня цены, вопрос 80). Цена — цена для оценки на момент или введённая
трейдером: чистая цена, доходность или спред («что если»). НКД и номинал —
на дату расчётов (шаг 3). Ставки — последние известные на день цены.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from am_common.calendar import add_trading_days
from am_quant.bond import (
    HUNDRED,
    Bond,
    CashFlow,
    Coupon,
    Horizon,
    Offer,
    Principal,
    QuantError,
    horizons,
)
from am_quant.fixed import YieldResult, price_at_yield, yield_to
from am_quant.spread import (
    FloaterTerms,
    SpreadResult,
    floater_flows,
    floater_spread,
    linker_flows,
    linker_spread,
    price_at_spread,
)
from marketdata.prices import Choice
from marketdata.queries import (
    accrued_on_date,
    calendar_around,
    face_value_on_date,
    price_views,
    trading_day_of,
)
from marketdata.rates import RateKind, RatePoint, rate_on
from marketdata.refdata import RefdataClient
from marketdata.repository import InstrumentRecord, Repository

BASIS_POINTS = Decimal(10_000)


class BondType(StrEnum):
    FIXED = "fixed"  # фиксированный купон: доходность
    FLOATER = "floater"  # спред над фондированием
    LINKER = "linker"  # номинал на RUONIA: спред над фондированием


class PriceBasis(StrEnum):
    """От чего посчитана цена расчёта."""

    VALUATION = "valuation"  # цена для оценки на момент
    PRICE = "price"  # чистая цена, введённая трейдером
    YIELD = "yield"  # из доходности, введённой трейдером
    SPREAD = "spread"  # из спреда, введённого трейдером


@dataclass(frozen=True, slots=True)
class FloaterSpec:
    """Параметры флоатера из справочника (решение 75)."""

    rate: RateKind  # плавающая ставка
    margin: Decimal  # маржа, доля
    capitalized: bool


@dataclass(frozen=True, slots=True)
class Terms:
    bond: Bond
    type: BondType
    floater: FloaterSpec | None = None


@dataclass(frozen=True, slots=True)
class Inputs:
    """Цена «что если»: не больше одного значения; ничего — цена для оценки."""

    price: Decimal | None = None  # чистая, %
    yield_percent: Decimal | None = None  # % годовых
    spread_bp: Decimal | None = None


@dataclass(frozen=True, slots=True)
class HorizonYield:
    horizon: Horizon
    result: YieldResult | None
    problem: QuantError | None  # почему к этому горизонту не посчитать


@dataclass(frozen=True, slots=True)
class SpreadView:
    result: SpreadResult
    funding: RatePoint  # RUONIA
    index: RatePoint | None  # ставка индекса флоатера; у линкера — нет
    terms: Terms


@dataclass(frozen=True, slots=True)
class Analytics:
    instrument: InstrumentRecord
    at: datetime
    trading_day: date
    settlement: date
    type: BondType
    basis: PriceBasis
    valuation: Choice | None  # цена для оценки, если расчёт — по ней
    clean: Decimal  # % от непогашенного номинала
    face_value: Decimal  # непогашенный номинал на дату расчётов
    accrued: Decimal  # НКД на дату расчётов
    dirty: Decimal
    yields: list[HorizonYield]
    spread: SpreadView | None


async def analytics(
    session: AsyncSession,
    refdata: RefdataClient,
    instrument_id: UUID,
    at: datetime,
    inputs: Inputs | None = None,
) -> Analytics:
    inputs = inputs or Inputs()
    repo = Repository(session)
    record = await repo.get(instrument_id)
    if record.kind != "bond":
        raise QuantError("not_a_bond", "Доходность и спред считаются только по облигациям.")
    day = await trading_day_of(refdata, at)
    # Дата расчётов может уйти в следующий год — нужен и его календарь.
    exceptions = await calendar_around(refdata, day)
    exceptions |= await calendar_around(refdata, day + timedelta(days=15))
    settle = add_trading_days(day, 1, exceptions)

    params = await refdata.terms(instrument_id, settle)
    if params is None:
        raise QuantError("no_terms", f"В справочнике нет версии бумаги на {settle:%d.%m.%Y}.")
    terms = bond_terms(params)
    ahead = horizons(terms.bond, settle)
    if not ahead:
        raise QuantError("matured", "Бумага погашена.")
    setup = _Setup(
        terms=terms,
        settle=settle,
        horizons=ahead,
        face=await _face_value(session, instrument_id, terms, settle),
        accrued=await _accrued(session, refdata, instrument_id, settle),
        rates=await _rates(repo, terms, day),
    )

    valuation: Choice | None = None
    found = _from_inputs(setup, inputs)
    if found is not None:
        dirty, basis = found
        clean = (dirty - setup.accrued) * HUNDRED / setup.face
    else:
        if inputs.price is not None:
            clean, basis = inputs.price, PriceBasis.PRICE
        else:
            [view] = await price_views(session, refdata, at, [instrument_id])
            valuation = view.price
            if valuation is None:
                raise QuantError("no_price", "Нет цены для оценки на этот момент.")
            clean, basis = valuation.value, PriceBasis.VALUATION
        dirty = clean * setup.face / HUNDRED + setup.accrued

    fixed = terms.type is BondType.FIXED
    return Analytics(
        instrument=record,
        at=at,
        trading_day=day,
        settlement=settle,
        type=terms.type,
        basis=basis,
        valuation=valuation,
        clean=clean,
        face_value=setup.face,
        accrued=setup.accrued,
        dirty=dirty,
        yields=[_yield(terms.bond, settle, h, dirty) for h in ahead] if fixed else [],
        spread=None if fixed else _spread(setup, dirty),
    )


@dataclass(frozen=True, slots=True)
class _Setup:
    """Всё для расчёта, кроме цены."""

    terms: Terms
    settle: date
    horizons: list[Horizon]  # первый — главный (вопрос 78)
    face: Decimal
    accrued: Decimal
    rates: dict[RateKind, RatePoint]


def _from_inputs(setup: _Setup, inputs: Inputs) -> tuple[Decimal, PriceBasis] | None:
    """Грязная цена из доходности или спреда «что если»; None — их не задали."""
    horizon = setup.horizons[0]
    if inputs.yield_percent is not None:
        if setup.terms.type is not BondType.FIXED:
            raise QuantError(
                "yield_for_floater", "У флоатера и линкера задайте спред, а не доходность."
            )
        rate = inputs.yield_percent / HUNDRED
        return price_at_yield(setup.terms.bond, setup.settle, horizon, rate), PriceBasis.YIELD
    if inputs.spread_bp is not None:
        if setup.terms.type is BondType.FIXED:
            raise QuantError(
                "spread_for_fixed", "У облигации с фиксированным купоном задайте доходность."
            )
        flows = _spread_flows(setup, horizon)
        ruonia = setup.rates[RateKind.RUONIA].value / HUNDRED
        spread = inputs.spread_bp / BASIS_POINTS
        return price_at_spread(flows, setup.settle, ruonia, spread), PriceBasis.SPREAD
    return None


def bond_terms(params: dict[str, Any]) -> Terms:
    """Условия выпуска из справочника → облигация для am-quant."""
    indexation = params.get("nominal_indexation")
    kind = params.get("coupon_kind")
    if indexation == "unknown":
        raise QuantError(
            "indexation_unknown",
            "Номинал индексируемый, но не указано, на что, — заполните в справочнике.",
        )
    if kind in {None, "unknown"}:
        raise QuantError("coupon_kind_unknown", "Не указан тип купона — заполните в справочнике.")
    if params.get("initial_face_value") is None or params.get("maturity_date") is None:
        raise QuantError(
            "terms_incomplete", "В справочнике нет начального номинала или даты погашения."
        )
    face = Decimal(params["initial_face_value"])
    linker = indexation == "ruonia"
    bond = Bond(
        face_value=face,
        maturity=date.fromisoformat(params["maturity_date"]),
        coupons=_coupons(params),
        principal=() if linker else _principal(params, face),
        offers=tuple(
            Offer(
                date=date.fromisoformat(o["date"]),
                price=HUNDRED if o.get("price") is None else Decimal(o["price"]),
                window_end=None
                if o.get("window_end") is None
                else date.fromisoformat(o["window_end"]),
            )
            for o in params.get("offers", [])
        ),
    )
    if linker:
        if any(not a.get("is_maturity") for a in params.get("amortizations", [])):
            raise QuantError(
                "linker_amortization", "Линкер с амортизацией пока не считаем — нужна доработка."
            )
        return Terms(bond, BondType.LINKER)
    if kind == "fixed":
        return Terms(bond, BondType.FIXED)
    floater = params.get("floater")
    if floater is None:
        raise QuantError(
            "floater_terms_missing",
            "Не заполнены параметры флоатера: ставка, маржа, капитализация — "
            "укажите их в справочнике.",
        )
    if floater["rate"] not in set(RateKind):
        raise QuantError(
            "rate_unsupported",
            "Спред считается для флоатеров на ключевую ставку, RUONIA и RUSFAR.",
        )
    spec = FloaterSpec(
        rate=RateKind(floater["rate"]),
        margin=Decimal(floater["margin_bp"]) / BASIS_POINTS,
        capitalized=bool(floater["capitalized"]),
    )
    return Terms(bond, BondType.FLOATER, spec)


def _coupons(params: dict[str, Any]) -> tuple[Coupon, ...]:
    """Купоны; начало периода, если ISS его не дал, — дата предыдущего купона или выпуска."""
    result: list[Coupon] = []
    previous = params.get("issue_date")
    for c in sorted(params.get("coupons", []), key=lambda c: c["date"]):
        start = c.get("start_date") or previous
        previous = c["date"]
        if start is None:
            raise QuantError(
                "coupon_start_unknown", f"У купона {c['date']} неизвестно начало периода."
            )
        result.append(
            Coupon(
                start=date.fromisoformat(start),
                end=date.fromisoformat(c["date"]),
                amount=None if c.get("amount") is None else Decimal(c["amount"]),
                rate=None if c.get("rate") is None else Decimal(c["rate"]) / HUNDRED,
            )
        )
    return tuple(result)


def _principal(params: dict[str, Any], face: Decimal) -> tuple[Principal, ...]:
    result: list[Principal] = []
    for a in params.get("amortizations", []):
        if a.get("amount") is not None:
            amount = Decimal(a["amount"])
        elif a.get("percent") is not None:
            amount = Decimal(a["percent"]) * face / HUNDRED
        else:
            raise QuantError(
                "amortization_unknown", f"Неизвестна сумма погашения номинала {a['date']}."
            )
        result.append(Principal(date.fromisoformat(a["date"]), amount))
    return tuple(result)


async def _accrued(
    session: AsyncSession, refdata: RefdataClient, instrument_id: UUID, settle: date
) -> Decimal:
    found = await accrued_on_date(session, refdata, instrument_id, settle)
    if found is None:
        raise QuantError(
            "accrued_missing", f"Нет НКД на {settle:%d.%m.%Y}: нет точек ISS, чтобы досчитать."
        )
    return found.value


async def _face_value(
    session: AsyncSession, instrument_id: UUID, terms: Terms, settle: date
) -> Decimal:
    """Номинал линкера — из ряда рыночных данных, остальных — из графика погашений."""
    if terms.type is not BondType.LINKER:
        return terms.bond.outstanding(settle)
    found = await face_value_on_date(session, instrument_id, settle)
    if found is None:
        raise QuantError(
            "face_value_missing",
            f"Нет номинала на {settle:%d.%m.%Y}: нет точек ISS, чтобы досчитать.",
        )
    return found.value


async def _rates(repo: Repository, terms: Terms, day: date) -> dict[RateKind, RatePoint]:
    """RUONIA — для фондирования; у флоатера ещё ставка индекса."""
    if terms.type is BondType.FIXED:
        return {}
    kinds = {RateKind.RUONIA} | ({terms.floater.rate} if terms.floater else set())
    points = await repo.rates(kinds, day)
    found: dict[RateKind, RatePoint] = {}
    for kind in sorted(kinds):
        point = rate_on((p for p in points if p.kind is kind), day)
        if point is None:
            hint = " — её присылает скрипт (docs/api/price-feed)" if kind is RateKind.RUONIA else ""
            raise QuantError("rate_missing", f"Нет ставки {kind.value} на {day:%d.%m.%Y}{hint}.")
        found[kind] = point
    return found


def _floater_terms(spec: FloaterSpec, rates: dict[RateKind, RatePoint]) -> FloaterTerms:
    return FloaterTerms(rates[spec.rate].value / HUNDRED, spec.margin, spec.capitalized)


def _spread_flows(setup: _Setup, horizon: Horizon) -> tuple[CashFlow, ...]:
    """Потоки флоатера или линкера: у линкера нет параметров флоатера."""
    spec = setup.terms.floater
    if spec is None:
        ruonia = setup.rates[RateKind.RUONIA].value / HUNDRED
        return linker_flows(setup.terms.bond, setup.settle, horizon, setup.face, ruonia)
    return floater_flows(setup.terms.bond, setup.settle, horizon, _floater_terms(spec, setup.rates))


def _yield(bond: Bond, settle: date, horizon: Horizon, dirty: Decimal) -> HorizonYield:
    try:
        return HorizonYield(horizon, yield_to(bond, settle, horizon, dirty), None)
    except QuantError as problem:
        return HorizonYield(horizon, None, problem)


def _spread(setup: _Setup, dirty: Decimal) -> SpreadView:
    terms, horizon = setup.terms, setup.horizons[0]
    funding = setup.rates[RateKind.RUONIA]
    common = {
        "ruonia": funding.value / HUNDRED,
        "dirty": dirty,
        "accrued": setup.accrued,
    }
    if terms.floater is None:
        result = linker_spread(terms.bond, setup.settle, horizon, face_value=setup.face, **common)
        return SpreadView(result, funding, None, terms)
    result = floater_spread(
        terms.bond,
        setup.settle,
        horizon,
        terms=_floater_terms(terms.floater, setup.rates),
        **common,
    )
    return SpreadView(result, funding, setup.rates[terms.floater.rate], terms)
