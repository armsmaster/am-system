"""Доходность и метрики на потоках, где ответ известен заранее."""

from datetime import date
from decimal import Decimal

import pytest

from am_quant import yields
from am_quant.bond import CashFlow, FlowKind, QuantError
from am_quant.yields import power, present_value, risk, solve_yield

SETTLE = date(2026, 10, 29)
IN_A_YEAR = date(2027, 10, 29)
EXACT = Decimal("1e-20")


def zero_coupon(amount: str = "1000") -> list[CashFlow]:
    return [CashFlow(IN_A_YEAR, Decimal(amount), FlowKind.PRINCIPAL)]


def test_zero_coupon_bond_yield_and_risk() -> None:
    # Через год 1000, сейчас 900: доходность 1000/900 − 1, дюрация — ровно год.
    flows = zero_coupon()
    rate = solve_yield(flows, SETTLE, Decimal(900))
    assert abs(rate - (Decimal(1000) / 900 - 1)) < Decimal("1e-12")

    metrics = risk(flows, SETTLE, rate)
    assert abs(metrics.macaulay - 1) < EXACT
    assert abs(metrics.macaulay_days - 365) < EXACT
    assert abs(metrics.modified - 1 / (1 + rate)) < EXACT
    assert abs(metrics.dv01 - metrics.modified * 900 / 10_000) < Decimal("1e-15")
    assert abs(metrics.convexity - 2 / (1 + rate) ** 2) < EXACT


def test_price_at_yield_is_the_inverse() -> None:
    flows = [
        CashFlow(date(2027, 4, 29), Decimal(80), FlowKind.COUPON),
        CashFlow(IN_A_YEAR, Decimal(1080), FlowKind.COUPON),
    ]
    rate = solve_yield(flows, SETTLE, Decimal("987.65"))
    assert abs(present_value(flows, SETTLE, rate) - Decimal("987.65")) < Decimal("1e-9")


def test_newton_overshoot_falls_back_to_bisection() -> None:
    # Цена вдесятеро выше потока: первый шаг Ньютона уходит ниже −99%.
    rate = solve_yield(zero_coupon("100"), SETTLE, Decimal(1000))
    assert abs(rate - Decimal("-0.9")) < Decimal("1e-11")


def test_bisection_when_newton_runs_out_of_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yields, "NEWTON_STEPS", 1)
    rate = solve_yield(zero_coupon(), SETTLE, Decimal(900))
    assert abs(rate - (Decimal(1000) / 900 - 1)) < Decimal("1e-11")


@pytest.mark.parametrize("price", ["0", "-5", "200000"])
def test_price_without_a_reasonable_yield(price: str) -> None:
    with pytest.raises(QuantError) as error:
        solve_yield(zero_coupon(), SETTLE, Decimal(price))
    assert error.value.code == "yield_out_of_range"


def test_power() -> None:
    assert abs(power(Decimal(4), Decimal("0.5")) - 2) < EXACT
