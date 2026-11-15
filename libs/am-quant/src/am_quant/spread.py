"""Флоатеры и линкеры: вменённый спред над фондированием по RUONIA (решение 75).

Фондирование — RUONIA с ежедневной капитализацией, в эффективных годовых:

    Eф = (1 + R/365)^365 − 1

Спред s — постоянная добавка, при которой P = Σ CFᵢ / (1 + Eф + s)^tᵢ.
Прогноз ставки — плоский: ставка индекса на все будущие периоды равна
текущей. Разложение спреда — цепочкой, все части в эффективных годовых
(раздел «Разложение спреда» в design/04).

Непрерывное измерение (решение 156): фондирование 365·ln(1 + R/365), спред
ln(1 + Eф + s) − ln(1 + Eф), разложение — той же цепочкой в непрерывных.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext

from am_quant.bond import ZERO, Bond, CashFlow, Coupon, Horizon, QuantError, cash_flows
from am_quant.yields import (
    DAYS,
    PRECISION,
    Risk,
    continuous,
    continuous_risk,
    from_continuous,
    power,
    present_value,
    risk,
    solve_yield,
)

ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class FloaterTerms:
    index: Decimal  # текущая ставка индекса: ключевая ставка, RUONIA или RUSFAR
    margin: Decimal  # маржа к ставке индекса
    capitalized: bool  # ставка капитализируется внутри купонного периода


@dataclass(frozen=True, slots=True)
class Breakdown:
    """Из чего сложился спред. Части складываются в спред точно."""

    basis: Decimal  # ставка индекса вместо RUONIA
    capitalization: Decimal  # простая ставка за период вместо ежедневной капитализации
    margin: Decimal  # маржа сверх ставки индекса; у линкера — купон
    other: Decimal  # остаток: неполный текущий период, объявленный купон, НКД
    price: Decimal  # дисконт или премия к номиналу

    @property
    def total(self) -> Decimal:
        with localcontext() as context:
            context.prec = PRECISION
            return self.basis + self.capitalization + self.margin + self.other + self.price


@dataclass(frozen=True, slots=True)
class Measured:
    """Спред в одном измерении: фондирование, спред, разложение и метрики по спреду."""

    funding: Decimal
    spread: Decimal
    breakdown: Breakdown
    risk: Risk


@dataclass(frozen=True, slots=True)
class SpreadResult:
    horizon: Horizon
    flows: tuple[CashFlow, ...]
    funding: Decimal  # Eф — эффективная годовая ставка фондирования
    spread: Decimal
    breakdown: Breakdown
    risk: Risk  # по доходности Eф + s: спред-дюрация и DV01 по спреду
    continuous: Measured  # то же в непрерывном измерении (решение 156)


def funding_rate(ruonia: Decimal) -> Decimal:
    """Eф = (1 + R/365)^365 − 1."""
    with localcontext() as context:
        context.prec = PRECISION
        return (ONE + ruonia / DAYS) ** 365 - ONE


def funding_continuous(ruonia: Decimal) -> Decimal:
    """Eф_c = 365 · ln(1 + R/365) — фондирование в непрерывном измерении, ≈ R."""
    with localcontext() as context:
        context.prec = PRECISION
        return DAYS * (ONE + ruonia / DAYS).ln()


def floater_coupon(outstanding: Decimal, days: int, terms: FloaterTerms) -> Decimal:
    """Купон флоатера на бумагу по текущей ставке индекса — плоский прогноз, без округления.

    Без капитализации: N · (I + m) · d/365; с капитализацией:
    N · ((1 + I/365)^d − 1 + m · d/365). N — непогашенный номинал в начале периода.
    """
    with localcontext() as context:
        context.prec = PRECISION
        tau = Decimal(days) / DAYS
        if terms.capitalized:
            index_part = (ONE + terms.index / DAYS) ** days - ONE
        else:
            index_part = terms.index * tau
        return outstanding * (index_part + terms.margin * tau)


def floater_flows(
    bond: Bond, settle: date, horizon: Horizon, terms: FloaterTerms
) -> tuple[CashFlow, ...]:
    """Потоки флоатера: объявленный купон как есть, будущие — по текущей ставке индекса."""

    def coupon(c: Coupon) -> Decimal:
        if c.amount is not None:
            return c.amount
        return floater_coupon(bond.outstanding(c.start), c.days, terms)

    return cash_flows(bond, settle, horizon, coupon)


def reset_duration(
    bond: Bond, settle: date, terms: FloaterTerms, frequency: int | None = None
) -> Decimal:
    """Модифицированная дюрация флоатера до ближайшей выплаты купона (design/09):

        MD = t / (1 + y),   y = (1 + c/f)^f − 1

    t — лет до ближайшей выплаты (ACT/365), c — ставка текущего купона: объявленная,
    по объявленной сумме или прогноз по текущей ставке индекса; f — выплат в год,
    без условий выпуска — по длине текущего периода. На рост ставок флоатер отвечает
    только до ближайшей выплаты: дальше купон догонит ставку.
    """
    coupon = next((c for c in bond.coupons if c.end > settle), None)
    if coupon is None:
        raise QuantError("no_coupon", "Впереди нет купонов — дюрацию до купона не посчитать.")
    with localcontext() as context:
        context.prec = PRECISION
        tau = Decimal(coupon.days) / DAYS
        if coupon.rate is not None:
            rate = coupon.rate
        elif coupon.amount is not None:
            rate = coupon.amount / (bond.outstanding(coupon.start) * tau)
        else:
            rate = terms.index + terms.margin
        per_year = Decimal(frequency) if frequency else DAYS / Decimal(coupon.days)
        effective = power(ONE + rate / per_year, per_year) - ONE
        return Decimal((coupon.end - settle).days) / DAYS / (ONE + effective)


def linker_flows(
    bond: Bond, settle: date, horizon: Horizon, face_value: Decimal, ruonia: Decimal
) -> tuple[CashFlow, ...]:
    """Потоки линкера: номинал растёт с RUONIA, N(D) = N(S) · (1 + R/365)^(D − S);
    купон — k · N(D) · d/365, в конце — погашение N(T).

    ``face_value`` — номинал на дату расчётов. Линкеры с амортизацией не считаем:
    доля амортизации зависит от индекса, а справочник её не хранит.
    """
    if bond.principal:
        raise QuantError(
            "linker_amortization", "Линкер с амортизацией пока не считаем — нужна доработка."
        )

    def nominal(day: date) -> Decimal:
        with localcontext() as context:
            context.prec = PRECISION
            return face_value * (ONE + ruonia / DAYS) ** (day - settle).days

    def coupon(c: Coupon) -> Decimal | None:
        if c.rate is None:
            return None
        return c.rate * nominal(c.end) * c.days / DAYS

    return cash_flows(bond, settle, horizon, coupon, nominal)


def floater_spread(
    bond: Bond,
    settle: date,
    horizon: Horizon,
    *,
    terms: FloaterTerms,
    ruonia: Decimal,
    dirty: Decimal,
    accrued: Decimal,
) -> SpreadResult:
    """Спред флоатера по грязной цене и его разложение."""
    flows = floater_flows(bond, settle, horizon, terms)
    tau = _mean_period(bond, settle, horizon)
    with localcontext() as context:
        context.prec = PRECISION
        index = funding_rate(terms.index)
        index_c = funding_continuous(terms.index)
        basis = index - funding_rate(ruonia)
        basis_c = index_c - funding_continuous(ruonia)
        if terms.capitalized:
            compounded = power(ONE + terms.index / DAYS, tau * DAYS)
            capitalization = capitalization_c = ZERO
            margin = power(compounded + terms.margin * tau, ONE / tau) - ONE - index
            margin_c = (compounded + terms.margin * tau).ln() / tau - index_c
        else:
            simple = power(ONE + terms.index * tau, ONE / tau) - ONE
            simple_c = (ONE + terms.index * tau).ln() / tau
            capitalization = simple - index
            capitalization_c = simple_c - index_c
            margin = power(ONE + (terms.index + terms.margin) * tau, ONE / tau) - ONE - simple
            margin_c = (ONE + (terms.index + terms.margin) * tau).ln() / tau - simple_c
    par = bond.outstanding(settle) + accrued
    return _spread(
        flows,
        settle,
        horizon,
        ruonia=ruonia,
        dirty=dirty,
        par=par,
        parts=(basis, capitalization, margin),
        parts_c=(basis_c, capitalization_c, margin_c),
    )


def linker_spread(
    bond: Bond,
    settle: date,
    horizon: Horizon,
    *,
    face_value: Decimal,
    ruonia: Decimal,
    dirty: Decimal,
    accrued: Decimal,
) -> SpreadResult:
    """Спред линкера. Базис и капитализация — 0: индекс номинала — та же RUONIA
    с ежедневной капитализацией. Вместо маржи — купон: (1 + Eф) · ((1 + k·τ)^(1/τ) − 1)."""
    flows = linker_flows(bond, settle, horizon, face_value, ruonia)
    tau = _mean_period(bond, settle, horizon)
    rates = [c.rate for c in _periods(bond, settle, horizon) if c.rate is not None]
    with localcontext() as context:
        context.prec = PRECISION
        rate = sum(rates, ZERO) / len(rates)
        coupon = (ONE + funding_rate(ruonia)) * (power(ONE + rate * tau, ONE / tau) - ONE)
        coupon_c = (ONE + rate * tau).ln() / tau
    par = face_value + accrued
    return _spread(
        flows,
        settle,
        horizon,
        ruonia=ruonia,
        dirty=dirty,
        par=par,
        parts=(ZERO, ZERO, coupon),
        parts_c=(ZERO, ZERO, coupon_c),
    )


def price_at_spread(
    flows: Sequence[CashFlow], settle: date, ruonia: Decimal, spread: Decimal
) -> Decimal:
    """Грязная цена при спреде ``spread`` над фондированием."""
    with localcontext() as context:
        context.prec = PRECISION
        return present_value(flows, settle, funding_rate(ruonia) + spread)


def price_at_continuous_spread(
    flows: Sequence[CashFlow], settle: date, ruonia: Decimal, spread: Decimal
) -> Decimal:
    """Грязная цена при непрерывном спреде: доходность e^(Eф_c + s_c) − 1."""
    with localcontext() as context:
        context.prec = PRECISION
        rate = from_continuous(funding_continuous(ruonia) + spread)
        return present_value(flows, settle, rate)


def _spread(
    flows: tuple[CashFlow, ...],
    settle: date,
    horizon: Horizon,
    *,
    ruonia: Decimal,
    dirty: Decimal,
    par: Decimal,
    parts: tuple[Decimal, Decimal, Decimal],
    parts_c: tuple[Decimal, Decimal, Decimal],
) -> SpreadResult:
    with localcontext() as context:
        context.prec = PRECISION
        funding = funding_rate(ruonia)
        funding_c = funding_continuous(ruonia)
        market = solve_yield(flows, settle, dirty)
        par_yield = solve_yield(flows, settle, par)
        spread = market - funding
        spread_c = continuous(market) - funding_c
        continuous_view = Measured(
            funding=funding_c,
            spread=spread_c,
            breakdown=_breakdown(parts_c, spread_c, continuous(par_yield) - funding_c),
            risk=continuous_risk(flows, settle, market),
        )
        return SpreadResult(
            horizon,
            flows,
            funding,
            spread,
            _breakdown(parts, spread, par_yield - funding),
            risk(flows, settle, market),
            continuous_view,
        )


def _breakdown(
    parts: tuple[Decimal, Decimal, Decimal], spread: Decimal, at_par: Decimal
) -> Breakdown:
    """Цепочка частей: «прочее» и поправка на цену замыкают её на спред точно."""
    basis, capitalization, margin = parts
    return Breakdown(
        basis=basis,
        capitalization=capitalization,
        margin=margin,
        other=at_par - basis - capitalization - margin,
        price=spread - at_par,
    )


def _periods(bond: Bond, settle: date, horizon: Horizon) -> list[Coupon]:
    return [c for c in bond.coupons if settle < c.end <= horizon.date]


def _mean_period(bond: Bond, settle: date, horizon: Horizon) -> Decimal:
    """τ — средняя длина купонных периодов до горизонта, в годах."""
    periods = _periods(bond, settle, horizon)
    if not periods:
        raise QuantError("no_coupons", "До горизонта нет купонов — спред не разложить.")
    return Decimal(sum(c.days for c in periods)) / len(periods) / DAYS
