"""Эталоны шага 4 (docs/design/04-bond-analytics.md, «Наши эталоны» и «Флоатеры и линкеры»).

Числа посчитаны независимым прототипом и сверены с ISS там, где биржа их
публикует. После утверждения методики сверяемся с ними, а не с ISS.
Графики бумаг — ответы ISS bondization на 29.10.2026 (fixtures/).
"""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import pytest

from am_quant.bond import Bond, Coupon, Horizon, HorizonKind, Offer, Principal, QuantError, horizons
from am_quant.fixed import fixed_flows, price_at_yield, yield_to
from am_quant.spread import (
    FloaterTerms,
    SpreadResult,
    floater_spread,
    funding_rate,
    linker_spread,
    price_at_spread,
)

FIXTURES = Path(__file__).parent / "fixtures"
SETTLE = date(2026, 10, 29)  # цена закрытия 28.10.2026, расчёты T+1
RUONIA = Decimal("0.139")  # допущение: ISS RUONIA не публикует (вопрос 76)
KEY_RATE = Decimal("0.14")  # индекс KEYRATE в ISS, 28.10.2026


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
        offers=tuple(
            Offer(date.fromisoformat(o["date"]), Decimal(o["price"])) for o in data["offers"]
        ),
    )


def rounded(value: Decimal, places: int) -> str:
    # + 0 превращает «−0,0000» в «0,0000».
    return str(value.quantize(Decimal(1).scaleb(-places)) + 0)


def percent(value: Decimal) -> str:
    return rounded(value * 100, 6)


def six(value: Decimal) -> str:
    return rounded(value, 6)


def bp(value: Decimal) -> str:
    return rounded(value * 10_000, 4)


@dataclass(frozen=True)
class Fixed:
    secid: str
    clean: str  # чистая цена, %
    accrued: str  # НКД на 29.10, ₽
    horizon: HorizonKind
    # Эталон: грязная цена, доходность %, дюрация Маколея (лет, дней), MD, DV01, выпуклость.
    expected: str


FIXED = [
    Fixed("SU26238RMFS4", "52.493", "28.79", HorizonKind.MATURITY,
          "553.720000 15.935227 6.824692 2491.01 5.886642 0.325955 60.252300"),
    Fixed("SU46020RMFS2", "60.8", "14.75", HorizonKind.MATURITY,
          "622.750000 15.862790 5.787755 2112.53 4.995353 0.311086 36.353930"),
    Fixed("RU000A0ZZ4P9", "103.14", "8.62", HorizonKind.OFFER,
          "1040.020000 15.232750 0.916100 334.38 0.795000 0.082682 1.336653"),
]  # fmt: skip


@pytest.mark.parametrize("case", FIXED, ids=lambda c: c.secid)
def test_fixed_coupon_bonds(case: Fixed) -> None:
    bond = load(case.secid)
    horizon = horizons(bond, SETTLE)[0]
    dirty = Decimal(case.clean) * bond.outstanding(SETTLE) / 100 + Decimal(case.accrued)
    result = yield_to(bond, SETTLE, horizon, dirty)
    metrics = result.risk
    values = [six(dirty), percent(result.yield_rate), six(metrics.macaulay)]
    values += [rounded(metrics.macaulay_days, 2), six(metrics.modified), six(metrics.dv01)]
    values += [six(metrics.convexity)]

    assert horizon.kind is case.horizon
    assert " ".join(values) == case.expected
    # Обратная задача: цена по доходности возвращает грязную цену.
    assert six(price_at_yield(bond, SETTLE, horizon, result.yield_rate)) == six(dirty)


def test_offer_bond_to_maturity_needs_announced_coupons() -> None:
    # РЖД БО 001Р-06R: купон после оферты 14.10.2027 не объявлен.
    bond = load("RU000A0ZZ4P9")
    assert [h.kind for h in horizons(bond, SETTLE)] == [HorizonKind.OFFER, HorizonKind.MATURITY]
    with pytest.raises(QuantError) as error:
        fixed_flows(bond, SETTLE, horizons(bond, SETTLE)[1])
    assert error.value.code == "coupon_unknown"


def test_funding_rate() -> None:
    assert percent(funding_rate(RUONIA)) == "14.909369"


def check(result: SpreadResult, expected: str) -> None:
    """Базис, капитализация, маржа, прочее, поправка на цену, спред — бп; MD, DV01."""
    parts = result.breakdown
    values = [bp(parts.basis), bp(parts.capitalization), bp(parts.margin), bp(parts.other)]
    values += [bp(parts.price), bp(result.spread), six(result.risk.modified), six(result.risk.dv01)]
    assert " ".join(values) == expected
    assert abs(parts.total - result.spread) < Decimal("1e-30")


@dataclass(frozen=True)
class Real:
    name: str
    secid: str
    index: Decimal
    margin: str
    clean: str  # чистая цена 28.10, %
    accrued: str  # НКД на 29.10, ₽
    horizon: HorizonKind
    expected: str


# Настоящие флоатеры (вопрос 79): условия — ISS, маржа — COUPON_BENCHMARK_SPREAD.
REAL = [
    Real("Газпром нефть 003P-08R: КС + 1,30, раз в месяц, погашение 12.12.2026",
         "RU000A107HG1", KEY_RATE, "0.013", "100.07", "6.71", HorizonKind.MATURITY,
         "11.4923 -8.8823 148.5907 -2.1971 -67.4938 81.5098 0.103286 0.010405"),
    Real("Газпром нефть 003P-09R: RUONIA + 1,40, раз в квартал, погашение 23.12.2027",
         "RU000A107HH9", RUONIA, "0.014", "100.16", "14.75", HorizonKind.MATURITY,
         "0.0000 -26.7176 155.9129 -2.6679 -17.2149 109.3125 0.915914 0.093089"),
    Real("РЖД БО 001P-48R: RUONIA + 1,75, раз в месяц, купон 15,73% объявлен, оферта 15.12.2028",
         "RU000A10D5C5", RUONIA, "0.0175", "99.90", "0", HorizonKind.OFFER,
         "0.0000 -9.0469 200.1677 0.4658 6.4026 197.9892 1.562136 0.156057"),
]  # fmt: skip


@pytest.mark.parametrize("case", REAL, ids=lambda c: c.secid)
def test_real_floaters(case: Real) -> None:
    bond = load(case.secid)
    horizon = horizons(bond, SETTLE)[0]
    terms = FloaterTerms(case.index, Decimal(case.margin), capitalized=False)
    accrued = Decimal(case.accrued)
    dirty = Decimal(case.clean) * 10 + accrued
    result = floater_spread(
        bond, SETTLE, horizon, terms=terms, ruonia=RUONIA, dirty=dirty, accrued=accrued
    )
    assert horizon.kind is case.horizon
    check(result, case.expected)
    # Обратная задача: цена по спреду.
    back = price_at_spread(result.flows, SETTLE, RUONIA, result.spread)
    assert abs(back - dirty) < Decimal("1e-9")


def synthetic(months: int) -> Bond:
    """Флоатер на 2 года от 29.10.2026, купоны 28-го числа (таблица примеров в design/04)."""
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


@dataclass(frozen=True)
class Synthetic:
    name: str
    months: int
    index: Decimal
    margin: str
    capitalized: bool
    expected: str


SYNTHETIC = [
    Synthetic("КС + 130, без капитализации, раз в квартал", 3, KEY_RATE, "0.013", False,
              "11.4923 -27.1992 144.8136 0.0084 33.1435 162.2586 1.509917 0.150237"),
    Synthetic("КС + 130, с капитализацией, раз в квартал", 3, KEY_RATE, "0.013", True,
              "11.4923 0.0000 145.0706 0.0014 33.2866 189.8509 1.503417 0.149590"),
    Synthetic("RUONIA + 130, без капитализации, раз в квартал", 3, RUONIA, "0.013", False,
              "0.0000 -26.7901 144.7089 0.0082 33.0852 151.0122 1.512583 0.150502"),
    Synthetic("КС + 130, без капитализации, раз в месяц", 1, KEY_RATE, "0.013", False,
              "11.4923 -9.0089 148.5644 -0.0038 33.6815 184.7255 1.485717 0.147829"),
    Synthetic("КС + 130, без капитализации, раз в полгода", 6, KEY_RATE, "0.013", False,
              "11.4923 -53.4292 139.5225 0.0176 32.3808 129.9839 1.545600 0.153787"),
    Synthetic("RUONIA + 0, с капитализацией, раз в квартал", 3, RUONIA, "0", True,
              "0.0000 0.0000 0.0000 0.0000 32.4725 32.4725 1.541164 0.153346"),
]  # fmt: skip


@pytest.mark.parametrize("case", SYNTHETIC, ids=lambda c: c.name)
def test_synthetic_floaters_at_99_50(case: Synthetic) -> None:
    bond = synthetic(case.months)
    horizon = horizons(bond, SETTLE)[0]
    terms = FloaterTerms(case.index, Decimal(case.margin), case.capitalized)
    result = floater_spread(
        bond, SETTLE, horizon, terms=terms, ruonia=RUONIA, dirty=Decimal(995), accrued=Decimal(0)
    )
    check(result, case.expected)


def test_veb_linker() -> None:
    """ВЭБ.РФ ПБО-002Р-58: номинал на RUONIA, купон 1,85% два раза в год.

    Настоящие данные ISS 27.10.2026: цена 99,75%, номинал 1065,92 ₽ и НКД 8,97 ₽
    на дату расчётов 28.10.
    """
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
    dirty = Decimal("99.75") * face / 100 + accrued
    horizon = Horizon(HorizonKind.MATURITY, bond.maturity)
    result = linker_spread(
        bond,
        date(2026, 10, 28),
        horizon,
        face_value=face,
        ruonia=RUONIA,
        dirty=dirty,
        accrued=accrued,
    )
    check(result, "0.0000 0.0000 213.5357 -0.0283 11.3386 224.8461 2.188054 0.234609")
