"""Непрерывное измерение доходности и спреда (решение 156, design/04).

Эталоны — те же бумаги, что в test_golden.py; независимая проверка — формулы
перевода из эффективных годовых в непрерывные, посчитанные во float.
"""

import json
import math
from datetime import date
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

from am_quant.bond import Bond, Coupon, Horizon, HorizonKind, Principal, horizons
from am_quant.fixed import yield_to
from am_quant.spread import (
    FloaterTerms,
    floater_spread,
    funding_continuous,
    linker_spread,
    price_at_continuous_spread,
)
from am_quant.yields import continuous, from_continuous, years

FIXTURES = Path(__file__).parent / "fixtures"
SETTLE = date(2026, 10, 29)  # как в test_golden.py: цена закрытия 28.10.2026, расчёты T+1
RUONIA = Decimal("0.139")
KEY_RATE = Decimal("0.14")


def load(secid: str) -> Bond:
    data = json.loads((FIXTURES / f"{secid}.json").read_text(encoding="utf-8"))
    return Bond(
        face_value=Decimal(data["face_value"]),
        maturity=date.fromisoformat(data["maturity"]),
        coupons=tuple(
            Coupon(
                date.fromisoformat(c["start"]),
                date.fromisoformat(c["end"]),
                None if c["amount"] is None else Decimal(c["amount"]),
            )
            for c in data["coupons"]
        ),
        principal=tuple(
            Principal(date.fromisoformat(p["date"]), Decimal(p["amount"]))
            for p in data["principal"]
        ),
    )


def synthetic(months: int) -> Bond:
    """Флоатер на 2 года от 29.10.2026, купоны 28-го числа — как в test_golden.py."""
    coupons, start = [], SETTLE
    for i in range(1, 24 // months + 1):
        month = SETTLE.month - 1 + months * i
        end = date(SETTLE.year + month // 12, month % 12 + 1, 28)
        coupons.append(Coupon(start, end))
        start = end
    return Bond(
        face_value=Decimal(1000),
        maturity=start,
        coupons=tuple(coupons),
        principal=(Principal(start, Decimal(1000)),),
    )


def rounded(value: Decimal, places: int) -> str:
    return str(value.quantize(Decimal(1).scaleb(-places)) + 0)


def percent(value: Decimal) -> str:
    return rounded(value * 100, 6)


def six(value: Decimal) -> str:
    return rounded(value, 6)


def bp(value: Decimal) -> str:
    return rounded(value * 10_000, 4)


def test_continuous_and_back() -> None:
    rate = Decimal("0.15935227")
    assert abs(float(continuous(rate)) - math.log(1.15935227)) < 1e-15
    assert abs(from_continuous(continuous(rate)) - rate) < Decimal("1e-35")


def test_funding_is_close_to_the_overnight_rate() -> None:
    # 365·ln(1 + R/365) ≈ R: при 13,90% — 13,8974%.
    assert abs(float(funding_continuous(RUONIA)) - 365 * math.log(1 + 0.139 / 365)) < 1e-12
    assert percent(funding_continuous(RUONIA)) == "13.897354"


def test_fixed_coupon_bond_in_both_measures() -> None:
    bond = load("SU26238RMFS4")
    horizon = horizons(bond, SETTLE)[0]
    dirty = Decimal("52.493") * bond.outstanding(SETTLE) / 100 + Decimal("28.79")
    result = yield_to(bond, SETTLE, horizon, dirty)
    metrics = result.continuous_risk

    assert percent(result.continuous_rate) == "14.786146"
    # Дюрация Маколея та же; модифицированная в непрерывном измерении ей равна.
    assert metrics.macaulay == result.risk.macaulay == metrics.modified
    assert six(metrics.dv01) == six(metrics.macaulay * dirty / 10_000) == "0.377897"
    # Выпуклость — Σ t²·PV / P, независимо во float.
    y = float(result.continuous_rate)
    expected = sum(
        float(years(SETTLE, f.date)) ** 2
        * float(f.amount)
        * math.exp(-y * float(years(SETTLE, f.date)))
        for f in result.flows
    ) / float(dirty)
    assert abs(float(metrics.convexity) - expected) < 1e-9
    assert six(metrics.convexity) == "74.160286"


def parts(values: tuple[Decimal, ...]) -> str:
    return " ".join(bp(v) for v in values)


def test_floater_spread_and_breakdown_in_continuous() -> None:
    """КС + 130 без капитализации раз в месяц, цена 99,50: та же цепочка в непрерывных."""
    bond = synthetic(1)
    horizon = horizons(bond, SETTLE)[0]
    terms = FloaterTerms(KEY_RATE, Decimal("0.013"), capitalized=False)
    result = floater_spread(
        bond, SETTLE, horizon, terms=terms, ruonia=RUONIA, dirty=Decimal(995), accrued=Decimal(0)
    )
    view = result.continuous
    found = view.breakdown

    assert parts(
        (found.basis, found.capitalization, found.margin, found.other, found.price, view.spread)
    ) == ("9.9962 -7.8353 128.4321 -0.0032 28.8893 159.4791")
    assert abs(found.total - view.spread) < Decimal("1e-30")
    # Спред — разность непрерывных доходностей бумаги и фондирования.
    funding = float(result.funding)
    expected = math.log(1 + funding + float(result.spread)) - math.log(1 + funding)
    assert abs(float(view.spread) - expected) < 1e-12
    assert view.funding == funding_continuous(RUONIA)
    # Части — по формулам design/04, независимо во float; τ — средний период.
    periods = [c for c in bond.coupons if SETTLE < c.end <= horizon.date]
    tau = sum(c.days for c in periods) / len(periods) / 365
    index = 365 * math.log(1 + 0.14 / 365)
    simple = math.log(1 + 0.14 * tau) / tau
    assert abs(float(found.basis) - (index - 365 * math.log(1 + 0.139 / 365))) < 1e-12
    assert abs(float(found.capitalization) - (simple - index)) < 1e-12
    assert abs(float(found.margin) - (math.log(1 + 0.153 * tau) / tau - simple)) < 1e-12
    # Метрики по непрерывному спреду: модифицированная дюрация = Маколея.
    assert view.risk.modified == view.risk.macaulay == result.risk.macaulay
    # Обратная задача: цена по непрерывному спреду.
    back = price_at_continuous_spread(result.flows, SETTLE, RUONIA, view.spread)
    assert abs(back - Decimal(995)) < Decimal("1e-9")


def test_capitalized_floater_in_continuous() -> None:
    bond = synthetic(3)
    terms = FloaterTerms(KEY_RATE, Decimal("0.013"), capitalized=True)
    result = floater_spread(
        bond,
        SETTLE,
        horizons(bond, SETTLE)[0],
        terms=terms,
        ruonia=RUONIA,
        dirty=Decimal(995),
        accrued=Decimal(0),
    )
    found = result.continuous.breakdown

    assert (
        parts((found.capitalization, found.margin, result.continuous.spread))
        == "0.0000 125.3330 163.8680"
    )


def test_linker_coupon_reads_as_its_rate_in_continuous() -> None:
    """ВЭБ.РФ ПБО-002Р-58: купон 1,85% весит 184 бп, а не 214, — ставки складываются."""
    days = ["2026-05-15", "2026-11-19", "2027-05-26", "2027-11-30", "2028-06-05"]
    days += ["2028-12-10", "2029-06-16"]
    bond = Bond(
        face_value=Decimal(1000),
        maturity=date(2029, 6, 16),
        coupons=tuple(
            Coupon(date.fromisoformat(s), date.fromisoformat(e), rate=Decimal("0.0185"))
            for s, e in pairwise(days)
        ),
    )
    face, accrued = Decimal("1065.92"), Decimal("8.97")
    result = linker_spread(
        bond,
        date(2026, 10, 28),
        Horizon(HorizonKind.MATURITY, bond.maturity),
        face_value=face,
        ruonia=RUONIA,
        dirty=Decimal("99.75") * face / 100 + accrued,
        accrued=accrued,
    )
    found = result.continuous.breakdown

    assert (
        parts(
            (
                found.basis,
                found.capitalization,
                found.margin,
                found.other,
                found.price,
                result.continuous.spread,
            )
        )
        == "0.0000 0.0000 184.1241 -0.0241 9.6828 193.7828"
    )
