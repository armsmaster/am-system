"""Доходность, цена по доходности и риск-метрики (решения 12 и 23).

Эффективная годовая доходность, годовая капитализация, время — ACT/365
от даты расчётов S:

    P = Σ CFᵢ / (1 + y)^tᵢ,    tᵢ = (Dᵢ − S) / 365

Считаем в Decimal с 40 значащими цифрами: (1 + y)^−t = exp(−t · ln(1 + y)).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext

from am_quant.bond import ZERO, CashFlow, QuantError

PRECISION = 40
DAYS = Decimal(365)
BASIS_POINTS = Decimal(10_000)
TOLERANCE = Decimal("1e-12")
NEWTON_STEPS = 50
LOWEST = Decimal("-0.99")  # −99% годовых: ниже доходность не ищем
HIGHEST = Decimal(10)  # 1000% годовых


@dataclass(frozen=True, slots=True)
class Risk:
    macaulay: Decimal  # дюрация Маколея, годы
    modified: Decimal  # модифицированная дюрация, годы
    dv01: Decimal  # ₽ на бумагу за 1 бп
    convexity: Decimal  # годы²

    @property
    def macaulay_days(self) -> Decimal:
        return self.macaulay * DAYS


def years(settle: date, day: date) -> Decimal:
    return Decimal((day - settle).days) / DAYS


def power(base: Decimal, exponent: Decimal) -> Decimal:
    """base^exponent для положительного base и дробного exponent."""
    with localcontext() as context:
        context.prec = PRECISION
        return (exponent * base.ln()).exp()


def present_value(flows: Sequence[CashFlow], settle: date, rate: Decimal) -> Decimal:
    """Грязная цена при доходности ``rate``."""
    with localcontext() as context:
        context.prec = PRECISION
        return _sums(flows, settle, rate)[0]


def solve_yield(flows: Sequence[CashFlow], settle: date, price: Decimal) -> Decimal:
    """Доходность, при которой приведённые потоки равны грязной цене ``price``.

    Метод Ньютона от 10%. Если он уходит за пределы от −99% до 1000% или
    не сходится за 50 шагов — деление этого отрезка пополам. Точность — 10⁻¹².
    """
    with localcontext() as context:
        context.prec = PRECISION
        rate = Decimal("0.1")
        for _ in range(NEWTON_STEPS):
            value, weighted, _ = _sums(flows, settle, rate)
            # f(y) = PV(y) − P, f'(y) = −Σ t·CF·v / (1 + y)
            step = (value - price) * (1 + rate) / weighted
            if not LOWEST < rate + step < HIGHEST:
                break
            rate += step
            if abs(step) < TOLERANCE:
                return rate
        return _bisect(flows, settle, price)


def risk(flows: Sequence[CashFlow], settle: date, rate: Decimal) -> Risk:
    """Дюрации, DV01 и выпуклость при доходности ``rate`` (таблица формул в design/04)."""
    with localcontext() as context:
        context.prec = PRECISION
        value, weighted, squared = _sums(flows, settle, rate)
        macaulay = weighted / value
        modified = macaulay / (1 + rate)
        return Risk(
            macaulay=macaulay,
            modified=modified,
            dv01=modified * value / BASIS_POINTS,
            convexity=squared / (value * (1 + rate) ** 2),
        )


def _sums(
    flows: Sequence[CashFlow], settle: date, rate: Decimal
) -> tuple[Decimal, Decimal, Decimal]:
    """Σ CF·v, Σ t·CF·v и Σ t(t+1)·CF·v, где v = (1 + y)^−t."""
    log = (1 + rate).ln()
    value = weighted = squared = ZERO
    for flow in flows:
        t = years(settle, flow.date)
        discounted = flow.amount * (-t * log).exp()
        value += discounted
        weighted += t * discounted
        squared += t * (t + 1) * discounted
    return value, weighted, squared


def _bisect(flows: Sequence[CashFlow], settle: date, price: Decimal) -> Decimal:
    low, high = LOWEST, HIGHEST
    if not _sums(flows, settle, high)[0] <= price <= _sums(flows, settle, low)[0]:
        raise QuantError(
            "yield_out_of_range",
            "Доходность при такой цене — вне пределов от −99% до 1000% годовых.",
        )
    while high - low > TOLERANCE:
        middle = (low + high) / 2
        if _sums(flows, settle, middle)[0] > price:
            low = middle
        else:
            high = middle
    return (low + high) / 2
