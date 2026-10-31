"""Облигации с фиксированным купоном: доходность к горизонту, цена по доходности, метрики.

Потоки — из графика бумаги: купоны, амортизации, погашение или выкуп
по оферте (раздел «Облигации с фиксированным купоном» в design/04).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from am_quant.bond import Bond, CashFlow, Horizon, announced, cash_flows
from am_quant.yields import Risk, present_value, risk, solve_yield


@dataclass(frozen=True, slots=True)
class YieldResult:
    horizon: Horizon
    flows: tuple[CashFlow, ...]
    yield_rate: Decimal  # эффективная годовая доходность, доля
    risk: Risk


def fixed_flows(bond: Bond, settle: date, horizon: Horizon) -> tuple[CashFlow, ...]:
    return cash_flows(bond, settle, horizon, announced)


def yield_to(bond: Bond, settle: date, horizon: Horizon, dirty: Decimal) -> YieldResult:
    """Доходность и метрики по грязной цене на дату расчётов."""
    flows = fixed_flows(bond, settle, horizon)
    rate = solve_yield(flows, settle, dirty)
    return YieldResult(horizon, flows, rate, risk(flows, settle, rate))


def price_at_yield(bond: Bond, settle: date, horizon: Horizon, rate: Decimal) -> Decimal:
    """Грязная цена при доходности ``rate`` к горизонту."""
    return present_value(fixed_flows(bond, settle, horizon), settle, rate)
