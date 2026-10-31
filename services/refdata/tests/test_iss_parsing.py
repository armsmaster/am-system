"""Разбор ответов ISS на записанных ответах настоящего ISS (октябрь 2026).

Фикстуры — tests/fixtures/iss: поиск, описание и график платежей для
облигаций каждого типа и пая БПИФ.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from refdata.domain import (
    CouponKind,
    InstrumentKind,
    IssFloater,
    NominalIndexation,
    OfferKind,
    RateKind,
)
from refdata.iss import (
    IssError,
    NotFoundInIssError,
    Security,
    UnsupportedSecurityError,
    parse_search,
    parse_security,
    pick,
    rows,
)

FIXTURES = Path(__file__).parent / "fixtures" / "iss"


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


def security(code: str) -> Security:
    candidate = pick(code, parse_search(load(f"search_{code}")))
    bond = FIXTURES / f"bond_{code}.json"
    return parse_security(
        candidate, load(f"desc_{code}"), load(f"bond_{code}") if bond.exists() else None
    )


def test_fixed_coupon_ofz() -> None:
    ofz = security("SU26238RMFS4")

    assert (ofz.secid, ofz.isin, ofz.kind) == ("SU26238RMFS4", "RU000A1038V6", InstrumentKind.BOND)
    params = ofz.params
    assert params.short_name == "ОФЗ 26238"
    assert params.board == "TQOB"
    assert params.currency == "RUB"
    assert params.issuer == "Министерство финансов Российской Федерации"
    assert params.initial_face_value == Decimal(1000)
    assert params.maturity_date == date(2041, 5, 15)
    assert params.coupon_kind is CouponKind.FIXED
    assert params.coupon_frequency == 2
    assert params.nominal_indexation is NominalIndexation.NONE
    assert len(params.coupons) == 40
    first = params.coupons[0]
    assert (first.date, first.start_date, first.rate, first.amount) == (
        date(2021, 12, 8),
        date(2021, 6, 16),
        Decimal("7.1"),
        Decimal("34.04"),
    )
    # Погашение — отдельной строкой графика амортизаций.
    assert [(a.date, a.percent, a.is_maturity) for a in params.amortizations] == [
        (date(2041, 5, 15), Decimal(100), True)
    ]
    assert params.offers == ()
    assert params.floater is None


def test_floater_is_recognised_but_needs_terms() -> None:
    floater = security("SU29014RMFS6").params

    assert floater.coupon_kind is CouponKind.FLOATER
    assert floater.floater is None  # параметры флоатера заполняет сопровождение
    assert floater.iss_floater is None  # в этой записи ISS ставки и маржи ещё нет
    # Первый купон в ISS без ставки и суммы — так и сохраняем.
    assert (floater.coupons[0].rate, floater.coupons[0].amount, floater.coupons[0].record_date) == (
        None,
        None,
        None,
    )


def test_floater_rate_and_margin_as_published_by_iss() -> None:
    # Газпром нефть 003P-08R: COUPON_BENCHMARK = RREFKEYR, COUPON_BENCHMARK_SPREAD = 1.30 (%).
    bond = security("RU000A107HG1").params

    assert bond.coupon_kind is CouponKind.FLOATER
    assert bond.iss_floater == IssFloater(
        benchmark="RREFKEYR", rate=RateKind.KEY_RATE, margin_bp=Decimal(130)
    )
    assert bond.floater is None


def test_unknown_iss_rate_code_and_missing_margin() -> None:
    candidate = pick("RU000A107HG1", parse_search(load("search_RU000A107HG1")))
    description = load("desc_RU000A107HG1")
    for row in description["description"]["data"]:
        if row[0] == "COUPON_BENCHMARK":
            row[2] = "MOSPRIME3M"
        if row[0] == "COUPON_BENCHMARK_SPREAD":
            row[2] = None

    bond = parse_security(candidate, description, load("bond_RU000A107HG1")).params
    assert bond.iss_floater == IssFloater(
        benchmark="MOSPRIME3M", rate=RateKind.OTHER, margin_bp=None
    )


def test_amortizing_ofz_has_unknown_coupon_kind() -> None:
    ofz = security("SU46020RMFS2").params

    # ISS не сообщает тип купона амортизационной облигации — не угадываем.
    assert ofz.coupon_kind is CouponKind.UNKNOWN
    assert [a.percent for a in ofz.amortizations] == [Decimal(25)] * 4
    assert not any(a.is_maturity for a in ofz.amortizations)


def test_corporate_bond_with_offers_and_amortization() -> None:
    bond = security("RU000A0JTZF1").params

    assert bond.board == "TQCB"
    # Номинал на дату следует из начального номинала и графика: 1000 − 400 = 600.
    assert bond.initial_face_value == Decimal(1000)
    repaid = sum(a.amount or 0 for a in bond.amortizations if a.date <= date(2026, 10, 26))
    assert bond.initial_face_value - repaid == Decimal(600)
    upcoming = [o for o in bond.offers if o.date == date(2026, 11, 3)]
    assert [(o.kind, o.window_start, o.window_end, o.price) for o in upcoming] == [
        (OfferKind.PUT, date(2026, 10, 26), date(2026, 10, 30), Decimal(100))
    ]
    assert {o.iss_type for o in bond.offers} >= {"Оферта", "Оферта (состоялось)"}
    # Купоны после оферты ещё не объявлены.
    assert bond.coupons[-1].rate is None
    assert bond.coupons == tuple(sorted(bond.coupons, key=lambda c: c.date))


def test_offer_placeholder_without_a_date_is_skipped() -> None:
    # У облигаций РЖД ISS первой строкой оферт отдаёт «Оферта/Погашение» с датой 0000-00-00.
    candidate = pick("RU000A0JTZF1", parse_search(load("search_RU000A0JTZF1")))
    bondization = load("bond_RU000A0JTZF1")
    placeholder = list(bondization["offers"]["data"][0])
    columns = bondization["offers"]["columns"]
    placeholder[columns.index("offerdate")] = "0000-00-00"
    placeholder[columns.index("offertype")] = "Оферта/Погашение"
    bondization["offers"]["data"].insert(0, placeholder)

    bond = parse_security(candidate, load("desc_RU000A0JTZF1"), bondization).params
    assert "Оферта/Погашение" not in {o.iss_type for o in bond.offers}
    assert len(bond.offers) == len(bondization["offers"]["data"]) - 1


def test_linker_with_indexed_nominal() -> None:
    linker = security("RU000A10F504")

    params = linker.params
    assert params.name == "ВЭБ.РФ ПБО-002Р-58"
    assert params.coupon_kind is CouponKind.FIXED
    # ISS пишет «Индексированный», но не говорит на какой индекс — уточняет сопровождение.
    assert params.nominal_indexation is NominalIndexation.UNKNOWN
    assert params.initial_face_value == Decimal(1000)
    assert [(c.date, c.rate) for c in params.coupons[:2]] == [
        (date(2026, 11, 19), Decimal("1.85")),
        (date(2027, 5, 26), Decimal("1.85")),
    ]


def test_fund_share() -> None:
    fund = security("LQDT")

    assert fund.kind is InstrumentKind.FUND_SHARE
    assert fund.params.coupon_kind is None
    assert fund.params.coupons == ()
    assert fund.params.currency == "RUB"


def test_search_picks_exact_code_or_isin() -> None:
    candidates = parse_search(load("search_LQDT"))

    assert [c.secid for c in candidates] == ["LQDTM", "LQDT"]
    assert pick("lqdt", candidates).secid == "LQDT"
    assert pick("RU000A1014L8", candidates).secid == "LQDT"


def test_search_without_exact_match_returns_candidates() -> None:
    candidates = parse_search(load("search_LQDT"))

    with pytest.raises(NotFoundInIssError) as caught:
        pick("LQD", candidates)

    assert [c.secid for c in caught.value.candidates] == ["LQDTM", "LQDT"]
    assert parse_search(load("search_none")) == []


def test_unsupported_security_type() -> None:
    candidate = pick("SU26238RMFS4", parse_search(load("search_SU26238RMFS4")))
    description = load("desc_SU26238RMFS4")
    for row in description["description"]["data"]:
        if row[0] == "GROUP":
            row[2] = "stock_shares"

    with pytest.raises(UnsupportedSecurityError):
        parse_security(candidate, description, None)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("valueprc", "много", "not a number"),
        ("coupondate", "вчера", "not a date"),
        ("coupondate", None, "without a date"),
    ],
)
def test_broken_values_are_reported(field: str, value: Any, message: str) -> None:
    candidate = pick("SU26238RMFS4", parse_search(load("search_SU26238RMFS4")))
    bondization = load("bond_SU26238RMFS4")
    column = bondization["coupons"]["columns"].index(field)
    bondization["coupons"]["data"][0][column] = value

    with pytest.raises(IssError, match=message):
        parse_security(candidate, load("desc_SU26238RMFS4"), bondization)


def test_missing_block_is_reported() -> None:
    with pytest.raises(IssError, match="no block"):
        rows({}, "securities")
