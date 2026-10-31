"""Условия выпуска из справочника → облигация для am-quant: что берём и чего не хватает."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from am_quant.bond import Coupon, Offer, Principal, QuantError
from marketdata.analytics import BondType, FloaterSpec, bond_terms
from marketdata.rates import RateKind


def params(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "initial_face_value": "1000",
        "issue_date": "2026-01-15",
        "maturity_date": "2027-01-15",
        "coupon_kind": "fixed",
        "nominal_indexation": "none",
        "coupons": [
            {"date": "2026-07-15", "start_date": "2026-01-15", "rate": "10", "amount": "49.86"},
            {"date": "2027-01-15", "start_date": "2026-07-15", "rate": "10", "amount": "50.41"},
        ],
        "amortizations": [
            {"date": "2026-07-15", "percent": "50", "amount": None, "is_maturity": False},
            {"date": "2027-01-15", "percent": None, "amount": "500", "is_maturity": True},
        ],
        "offers": [
            {"date": "2026-10-15", "price": None, "window_end": "2026-10-08"},
            {"date": "2026-12-15", "price": "101.5", "window_end": None},
        ],
        "floater": None,
    }
    return base | changes


def test_fixed_coupon_bond() -> None:
    terms = bond_terms(params())
    assert terms.type is BondType.FIXED
    assert terms.bond.face_value == 1000
    assert terms.bond.maturity == date(2027, 1, 15)
    assert terms.bond.coupons[0] == Coupon(
        date(2026, 1, 15), date(2026, 7, 15), Decimal("49.86"), Decimal("0.1")
    )
    # Амортизация в процентах — от начального номинала.
    assert terms.bond.principal == (
        Principal(date(2026, 7, 15), Decimal(500)),
        Principal(date(2027, 1, 15), Decimal(500)),
    )
    # Цена оферты не указана — 100%.
    assert terms.bond.offers == (
        Offer(date(2026, 10, 15), Decimal(100), date(2026, 10, 8)),
        Offer(date(2026, 12, 15), Decimal("101.5")),
    )


def test_coupon_period_start_from_previous_coupon_or_issue() -> None:
    coupons = [
        {"date": "2026-07-15", "start_date": None, "rate": None, "amount": "49.86"},
        {"date": "2027-01-15", "start_date": None, "rate": None, "amount": "50.41"},
    ]
    starts = [c.start for c in bond_terms(params(coupons=coupons)).bond.coupons]
    assert starts == [date(2026, 1, 15), date(2026, 7, 15)]

    with pytest.raises(QuantError) as error:
        bond_terms(params(coupons=coupons, issue_date=None))
    assert error.value.code == "coupon_start_unknown"


def test_floater_terms() -> None:
    floater = {"rate": "key_rate", "margin_bp": "130", "capitalized": True}
    terms = bond_terms(params(coupon_kind="floater", floater=floater))
    assert terms.type is BondType.FLOATER
    assert terms.floater == FloaterSpec(RateKind.KEY_RATE, Decimal("0.013"), capitalized=True)


def test_linker_has_no_fixed_principal() -> None:
    maturity = [{"date": "2027-01-15", "percent": None, "amount": None, "is_maturity": True}]
    terms = bond_terms(params(nominal_indexation="ruonia", amortizations=maturity))
    assert (terms.type, terms.bond.principal) == (BondType.LINKER, ())


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"nominal_indexation": "unknown"}, "indexation_unknown"),
        ({"coupon_kind": None}, "coupon_kind_unknown"),
        ({"coupon_kind": "unknown"}, "coupon_kind_unknown"),
        ({"maturity_date": None}, "terms_incomplete"),
        ({"initial_face_value": None}, "terms_incomplete"),
        ({"nominal_indexation": "ruonia"}, "linker_amortization"),
        ({"coupon_kind": "floater"}, "floater_terms_missing"),
        (
            {
                "coupon_kind": "floater",
                "floater": {"rate": "other", "margin_bp": "100", "capitalized": False},
            },
            "rate_unsupported",
        ),
        (
            {"amortizations": [{"date": "2027-01-15", "percent": None, "amount": None}]},
            "amortization_unknown",
        ),
    ],
)
def test_what_is_missing(changes: dict[str, Any], code: str) -> None:
    with pytest.raises(QuantError) as error:
        bond_terms(params(**changes))
    assert error.value.code == code
