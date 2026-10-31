"""Флоатеры и линкеры: вменённый спред над фондированием по RUONIA (решение 75).

Фондирование — RUONIA с ежедневной капитализацией, в эффективных годовых:

    Eф = (1 + R/365)^365 − 1

Спред s — постоянная добавка, при которой P = Σ CFᵢ / (1 + Eф + s)^tᵢ.
Прогноз ставки — плоский: ставка индекса на все будущие периоды равна
текущей. Разложение спреда — цепочкой, все части в эффективных годовых
(раздел «Разложение спреда» в design/04).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext

from am_quant.bond import ZERO, Bond, CashFlow, Coupon, Horizon, QuantError, cash_flows
from am_quant.yields import DAYS, PRECISION, Risk, power, present_value, risk, solve_yield

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
class SpreadResult:
    horizon: Horizon
    flows: tuple[CashFlow, ...]
    funding: Decimal  # Eф — эффективная годовая ставка фондирования
    spread: Decimal
    breakdown: Breakdown
    risk: Risk  # по доходности Eф + s: спред-дюрация и DV01 по спреду


def funding_rate(ruonia: Decimal) -> Decimal:
    """Eф = (1 + R/365)^365 − 1."""
    with localcontext() as context:
        context.prec = PRECISION
        return (ONE + ruonia / DAYS) ** 365 - ONE


def floater_flows(
    bond: Bond, settle: date, horizon: Horizon, terms: FloaterTerms
) -> tuple[CashFlow, ...]:
    """Потоки флоатера: объявленный купон как есть, будущие — по текущей ставке индекса.

    Без капитализации: N · (I + m) · d/365; с капитализацией:
    N · ((1 + I/365)^d − 1 + m · d/365). N — непогашенный номинал в начале периода.
    """

    def coupon(c: Coupon) -> Decimal:
        if c.amount is not None:
            return c.amount
        with localcontext() as context:
            context.prec = PRECISION
            tau = Decimal(c.days) / DAYS
            if terms.capitalized:
                index_part = (ONE + terms.index / DAYS) ** c.days - ONE
            else:
                index_part = terms.index * tau
            return bond.outstanding(c.start) * (index_part + terms.margin * tau)

    return cash_flows(bond, settle, horizon, coupon)


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
        basis = index - funding_rate(ruonia)
        if terms.capitalized:
            compounded = power(ONE + terms.index / DAYS, tau * DAYS)
            capitalization = ZERO
            margin = power(compounded + terms.margin * tau, ONE / tau) - ONE - index
        else:
            simple = power(ONE + terms.index * tau, ONE / tau) - ONE
            capitalization = simple - index
            margin = power(ONE + (terms.index + terms.margin) * tau, ONE / tau) - ONE - simple
    par = bond.outstanding(settle) + accrued
    parts = (basis, capitalization, margin)
    return _spread(flows, settle, horizon, ruonia=ruonia, dirty=dirty, par=par, parts=parts)


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
    par = face_value + accrued
    parts = (ZERO, ZERO, coupon)
    return _spread(flows, settle, horizon, ruonia=ruonia, dirty=dirty, par=par, parts=parts)


def price_at_spread(
    flows: Sequence[CashFlow], settle: date, ruonia: Decimal, spread: Decimal
) -> Decimal:
    """Грязная цена при спреде ``spread`` над фондированием."""
    with localcontext() as context:
        context.prec = PRECISION
        return present_value(flows, settle, funding_rate(ruonia) + spread)


def _spread(
    flows: tuple[CashFlow, ...],
    settle: date,
    horizon: Horizon,
    *,
    ruonia: Decimal,
    dirty: Decimal,
    par: Decimal,
    parts: tuple[Decimal, Decimal, Decimal],
) -> SpreadResult:
    with localcontext() as context:
        context.prec = PRECISION
        funding = funding_rate(ruonia)
        market = solve_yield(flows, settle, dirty)
        spread = market - funding
        at_par = solve_yield(flows, settle, par) - funding
        basis, capitalization, margin = parts
        breakdown = Breakdown(
            basis=basis,
            capitalization=capitalization,
            margin=margin,
            other=at_par - basis - capitalization - margin,
            price=spread - at_par,
        )
        return SpreadResult(horizon, flows, funding, spread, breakdown, risk(flows, settle, market))


def _periods(bond: Bond, settle: date, horizon: Horizon) -> list[Coupon]:
    return [c for c in bond.coupons if settle < c.end <= horizon.date]


def _mean_period(bond: Bond, settle: date, horizon: Horizon) -> Decimal:
    """τ — средняя длина купонных периодов до горизонта, в годах."""
    periods = _periods(bond, settle, horizon)
    if not periods:
        raise QuantError("no_coupons", "До горизонта нет купонов — спред не разложить.")
    return Decimal(sum(c.days for c in periods)) / len(periods) / DAYS
