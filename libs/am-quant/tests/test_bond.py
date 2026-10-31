"""Горизонт и денежные потоки: оферты, амортизация, НКД на дату оферты."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from am_quant.bond import (
    Bond,
    CashFlow,
    Coupon,
    FlowKind,
    Horizon,
    HorizonKind,
    Offer,
    Principal,
    QuantError,
    announced,
    cash_flows,
    horizons,
)

SETTLE = date(2026, 10, 29)


BOND = Bond(
    face_value=Decimal(1000),
    maturity=date(2027, 10, 29),
    coupons=(
        Coupon(date(2026, 4, 29), date(2026, 10, 29), Decimal(50)),
        Coupon(date(2026, 10, 29), date(2027, 4, 29), Decimal(50)),
        Coupon(date(2027, 4, 29), date(2027, 10, 29), Decimal(50)),
    ),
    principal=(Principal(date(2027, 10, 29), Decimal(1000)),),
)
MATURITY = Horizon(HorizonKind.MATURITY, date(2027, 10, 29))


def test_outstanding_after_amortization() -> None:
    amortizing = replace(
        BOND,
        principal=(
            Principal(date(2027, 4, 29), Decimal(400)),
            Principal(date(2027, 10, 29), Decimal(600)),
        ),
    )
    assert amortizing.outstanding(date(2027, 4, 28)) == 1000
    assert amortizing.outstanding(date(2027, 4, 29)) == 600
    assert amortizing.outstanding(date(2027, 10, 29)) == 0


def test_nearest_offer_that_can_still_be_used() -> None:
    offers = (
        Offer(date(2026, 11, 5), window_end=date(2026, 10, 28)),  # приём заявок закончился
        Offer(date(2027, 6, 1), Decimal("101"), window_end=date(2027, 5, 20)),
        Offer(date(2027, 10, 29)),  # в день погашения — это погашение
    )
    assert horizons(replace(BOND, offers=offers), SETTLE) == [
        Horizon(HorizonKind.OFFER, date(2027, 6, 1), Decimal("101")),
        Horizon(HorizonKind.MATURITY, date(2027, 10, 29)),
    ]
    assert horizons(BOND, date(2027, 10, 29)) == []


def test_flows_after_settlement_till_the_horizon() -> None:
    # Купон 29.10 уходит держателю на дату отсечки — покупателю с расчётами 29.10 его нет.
    assert cash_flows(BOND, SETTLE, MATURITY, announced) == (
        CashFlow(date(2027, 4, 29), Decimal(50), FlowKind.COUPON),
        CashFlow(date(2027, 10, 29), Decimal(50), FlowKind.COUPON),
        CashFlow(date(2027, 10, 29), Decimal(1000), FlowKind.PRINCIPAL),
    )


def test_offer_inside_a_coupon_period_pays_accrued_interest() -> None:
    # Оферта 15.07.2027: 77 дней из 183 периода 29.04–29.10 — НКД 50 × 77/183.
    offer = Horizon(HorizonKind.OFFER, date(2027, 7, 15))
    flows = cash_flows(BOND, SETTLE, offer, announced)
    assert flows[-1].kind is FlowKind.OFFER
    assert flows[-1].amount == Decimal(1000) + Decimal(50) * 77 / 183


def test_offer_inside_a_period_with_unknown_coupon() -> None:
    # Купон после оферты не объявлен — НКД на дату оферты не учитываем, как биржа.
    unknown = replace(
        BOND,
        coupons=(
            Coupon(date(2026, 10, 29), date(2027, 4, 29), Decimal(50)),
            Coupon(date(2027, 4, 29), date(2027, 10, 29)),
        ),
    )
    offer = Horizon(HorizonKind.OFFER, date(2027, 5, 4))
    assert cash_flows(unknown, SETTLE, offer, announced)[-1] == CashFlow(
        date(2027, 5, 4), Decimal(1000), FlowKind.OFFER
    )


def test_unknown_coupon_before_the_horizon() -> None:
    unknown = replace(BOND, coupons=(Coupon(date(2027, 4, 29), date(2027, 10, 29)),))
    with pytest.raises(QuantError) as error:
        cash_flows(unknown, SETTLE, MATURITY, announced)
    assert error.value.code == "coupon_unknown"
    assert "29.10.2027" in error.value.message


def test_rest_of_the_nominal_is_paid_at_maturity() -> None:
    # В графике нет погашения — остаток номинала платится в дату погашения.
    flows = cash_flows(replace(BOND, principal=()), SETTLE, MATURITY, announced)
    assert flows[-1] == CashFlow(date(2027, 10, 29), Decimal(1000), FlowKind.PRINCIPAL)


def test_nothing_left_to_pay() -> None:
    repaid = replace(BOND, coupons=(), principal=(Principal(date(2026, 6, 1), Decimal(1000)),))
    with pytest.raises(QuantError) as error:
        cash_flows(repaid, SETTLE, MATURITY, announced)
    assert error.value.code == "no_flows"
