"""Свойства спреда, которые обязаны выполняться (раздел «Как проверяем» в design/04)."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from am_quant.bond import Bond, Coupon, Horizon, HorizonKind, Principal, QuantError
from am_quant.spread import (
    FloaterTerms,
    floater_spread,
    linker_flows,
    linker_spread,
    reset_duration,
)

SETTLE = date(2026, 10, 29)
RUONIA = Decimal("0.139")
PAR, NO_ACCRUED = Decimal(1000), Decimal(0)


def floater(months: int) -> Bond:
    coupons, start = [], SETTLE
    for i in range(1, 24 // months + 1):
        month = SETTLE.month - 1 + months * i
        end = date(SETTLE.year + month // 12, month % 12 + 1, 28)
        coupons.append(Coupon(start, end))
        start = end
    return Bond(PAR, start, tuple(coupons), (Principal(start, PAR),))


def spread(bond: Bond, terms: FloaterTerms, price: Decimal = PAR) -> Decimal:
    horizon = Horizon(HorizonKind.MATURITY, bond.maturity)
    return floater_spread(
        bond, SETTLE, horizon, terms=terms, ruonia=RUONIA, dirty=price, accrued=NO_ACCRUED
    ).spread


def test_ruonia_with_capitalization_and_no_margin_at_par_is_funding() -> None:
    terms = FloaterTerms(RUONIA, Decimal(0), capitalized=True)
    assert abs(spread(floater(3), terms)) < Decimal("1e-10")


@pytest.mark.parametrize("capitalized", [False, True])
def test_parts_add_up_with_a_tiny_rest(capitalized: bool) -> None:
    bond = floater(3)
    terms = FloaterTerms(Decimal("0.14"), Decimal("0.013"), capitalized)
    horizon = Horizon(HorizonKind.MATURITY, bond.maturity)
    result = floater_spread(
        bond, SETTLE, horizon, terms=terms, ruonia=RUONIA, dirty=Decimal(990), accrued=NO_ACCRUED
    )
    assert abs(result.breakdown.total - result.spread) < Decimal("1e-30")
    assert abs(result.breakdown.other) < Decimal("0.1") / 10_000


def test_lower_price_means_wider_spread() -> None:
    terms = FloaterTerms(Decimal("0.14"), Decimal("0.013"), capitalized=False)
    prices = [Decimal(p) for p in ("1010", "1000", "995", "980")]
    spreads = [spread(floater(3), terms, p) for p in prices]
    assert spreads == sorted(spreads)


def test_rarer_coupon_loses_more_to_capitalization() -> None:
    terms = FloaterTerms(Decimal("0.14"), Decimal("0.013"), capitalized=False)
    losses = []
    for months in (1, 3, 6):
        bond = floater(months)
        horizon = Horizon(HorizonKind.MATURITY, bond.maturity)
        result = floater_spread(
            bond, SETTLE, horizon, terms=terms, ruonia=RUONIA, dirty=PAR, accrued=NO_ACCRUED
        )
        losses.append(result.breakdown.capitalization)
    assert losses[0] > losses[1] > losses[2]


def linker() -> Bond:
    return Bond(
        PAR,
        date(2027, 10, 29),
        (
            Coupon(date(2026, 4, 29), date(2026, 10, 29), rate=Decimal("0.02")),
            Coupon(date(2026, 10, 29), date(2027, 4, 29), rate=Decimal("0.02")),
            Coupon(date(2027, 4, 29), date(2027, 10, 29), rate=Decimal("0.02")),
        ),
    )


def test_linker_nominal_grows_with_ruonia() -> None:
    horizon = Horizon(HorizonKind.MATURITY, date(2027, 10, 29))
    flows = linker_flows(linker(), SETTLE, horizon, Decimal(1050), RUONIA)
    redemption = Decimal(1050) * (1 + RUONIA / 365) ** 365
    assert abs(flows[-1].amount - redemption) < Decimal("1e-20")
    assert abs(flows[-2].amount - Decimal("0.02") * redemption * 183 / 365) < Decimal("1e-20")


def test_linker_coupon_without_rate() -> None:
    bond = replace(linker(), coupons=(Coupon(date(2027, 4, 29), date(2027, 10, 29)),))
    horizon = Horizon(HorizonKind.MATURITY, date(2027, 10, 29))
    with pytest.raises(QuantError) as error:
        linker_spread(
            bond, SETTLE, horizon, face_value=PAR, ruonia=RUONIA, dirty=PAR, accrued=NO_ACCRUED
        )
    assert error.value.code == "coupon_unknown"


def test_linker_with_amortization_is_not_supported() -> None:
    bond = replace(linker(), principal=(Principal(date(2027, 4, 29), Decimal(500)),))
    horizon = Horizon(HorizonKind.MATURITY, date(2027, 10, 29))
    with pytest.raises(QuantError) as error:
        linker_flows(bond, SETTLE, horizon, PAR, RUONIA)
    assert error.value.code == "linker_amortization"


def test_no_coupons_before_the_horizon() -> None:
    # Остался только номинал: разложить спред не на что.
    bond = Bond(PAR, date(2027, 1, 15), (), (Principal(date(2027, 1, 15), PAR),))
    terms = FloaterTerms(RUONIA, Decimal(0), capitalized=False)
    with pytest.raises(QuantError) as error:
        spread(bond, terms, Decimal(990))
    assert error.value.code == "no_coupons"


# --- дюрация флоатера до ближайшего купона (design/09) -------------------------

# Эмитент А из примера шага 9: КС 14,00% + 1,30%, купон раз в месяц, до выплаты 20 дней.
MONTHLY = Bond(
    face_value=Decimal(1000),
    maturity=date(2027, 11, 30),
    coupons=(
        Coupon(date(2026, 11, 1), date(2026, 12, 1)),
        Coupon(date(2026, 12, 1), date(2026, 12, 31)),
    ),
)
KEY_RATE = FloaterTerms(index=Decimal("0.14"), margin=Decimal("0.013"), capitalized=False)


def test_reset_duration_of_the_methodology_example() -> None:
    # t = 20/365 = 0,0548; y = (1 + 0,153/12)^12 − 1 = 16,42%; MD = 0,0548 / 1,1642.
    found = reset_duration(MONTHLY, date(2026, 11, 11), KEY_RATE, frequency=12)

    assert round(found, 6) == Decimal("0.047066")


def test_reset_duration_takes_the_announced_coupon() -> None:
    # Объявленная ставка важнее прогноза; объявлена только сумма — ставка из суммы.
    rate = replace(MONTHLY, coupons=(replace(MONTHLY.coupons[0], rate=Decimal("0.2")),))
    amount = replace(MONTHLY, coupons=(replace(MONTHLY.coupons[0], amount=Decimal("16.44")),))
    settle = date(2026, 11, 11)

    by_rate = reset_duration(rate, settle, KEY_RATE, frequency=12)
    by_amount = reset_duration(amount, settle, KEY_RATE, frequency=12)

    assert by_rate < reset_duration(MONTHLY, settle, KEY_RATE, frequency=12)
    # 16,44 ₽ за 30 дней на 1 000 ₽ — 20% годовых: как объявленная ставка 20%.
    assert round(by_amount, 4) == round(by_rate, 4)


def test_reset_duration_without_frequency_and_without_coupons() -> None:
    # Без условий выпуска число выплат в год — по длине периода: 365 / 30.
    found = reset_duration(MONTHLY, date(2026, 11, 11), KEY_RATE)
    assert round(found, 3) == Decimal("0.047")

    with pytest.raises(QuantError) as refused:
        reset_duration(MONTHLY, date(2026, 12, 31), KEY_RATE)
    assert refused.value.code == "no_coupon"
