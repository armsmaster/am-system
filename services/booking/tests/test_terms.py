"""Выплаты из условий выпуска справочника и условия бумаги на дату."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from booking.roll import Payment
from booking.terms import NO_TERMS, InstrumentHistory, TermsBook, payments

FIXTURES = Path(__file__).parent / "fixtures" / "refdata"
BOND = UUID(int=1)
# 04.11.2026 — праздник: торговый день перед выплатой 05.11 — 03.11.
HOLIDAY = {date(2026, 11, 4): False}


def params(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "initial_face_value": "1000",
        "maturity_date": "2027-05-05",
        "nominal_indexation": "none",
        "coupons": [
            {"date": "2026-11-05", "record_date": None, "amount": "40"},
            {"date": "2027-05-05", "record_date": "2027-05-04", "amount": None},
        ],
        "amortizations": [
            {"date": "2026-11-05", "percent": "25", "amount": None, "is_maturity": False},
            {"date": "2027-05-05", "percent": None, "amount": "750", "is_maturity": True},
        ],
    }
    return base | changes


def test_coupons_and_principal_by_payment_date() -> None:
    first, last = payments(params(), HOLIDAY)

    assert first == Payment(
        pay_date=date(2026, 11, 5),
        record_date=date(2026, 11, 3),  # даты фиксации нет — торговый день перед выплатой
        has_coupon=True,
        coupon=Decimal(40),
        principal=Decimal(250),  # 25% от 1000
        outstanding=Decimal(750),
        final=False,
    )
    assert last == Payment(
        pay_date=date(2027, 5, 5),
        record_date=date(2027, 5, 4),
        has_coupon=True,
        coupon=None,  # сумма купона ещё не объявлена
        principal=Decimal(750),
        outstanding=None,
        final=True,
    )


def test_maturity_without_a_schedule_row_repays_the_rest() -> None:
    [coupon, maturity] = payments(params(amortizations=[]), {})

    assert (coupon.principal, coupon.outstanding) == (Decimal(0), None)
    assert (maturity.principal, maturity.final) == (Decimal(1000), True)


def test_unknown_amounts() -> None:
    # Линкер: суммы погашения неизвестны до дня выплаты, номинал не считаем.
    linker = payments(
        params(
            nominal_indexation="ruonia",
            amortizations=[{"date": "2027-05-05", "percent": None, "amount": None}],
        ),
        {},
    )
    assert [(p.principal, p.outstanding) for p in linker] == [(Decimal(0), None), (None, None)]
    # Без начального номинала остаток не посчитать.
    no_face = payments(params(initial_face_value=None, amortizations=[]), {})
    assert no_face[-1].principal is None


def test_ofz_from_refdata() -> None:
    ofz = json.loads((FIXTURES / "SU26238RMFS4.json").read_text(encoding="utf-8"))
    schedule = payments(ofz, {})

    december = next(p for p in schedule if p.pay_date == date(2026, 12, 2))
    assert (december.record_date, december.coupon) == (date(2026, 12, 1), Decimal("35.4"))
    assert schedule[-1].final


def test_terms_on_a_date_follow_versions() -> None:
    announced = params(
        coupons=[{"date": "2027-05-05", "record_date": "2027-05-04", "amount": "41"}]
    )
    history = InstrumentHistory(
        BOND,
        "bond",
        "RU000TEST",
        "Тест",
        [(None, params()), (date(2027, 4, 20), announced)],
    )
    book = TermsBook({BOND: history}, HOLIDAY)

    assert book(BOND, date(2027, 4, 19)).payments[-1].coupon is None
    assert book(BOND, date(2027, 4, 20)).payments[-1].coupon == Decimal(41)
    assert book(BOND, date(2027, 4, 21)) is book(BOND, date(2027, 4, 20))  # из кэша
    assert book(UUID(int=2), date(2027, 1, 1)) is NO_TERMS
    # До первой версии условий нет.
    later = InstrumentHistory(BOND, "bond", "X", "X", [(date(2027, 1, 1), params())])
    assert TermsBook({BOND: later}, {})(BOND, date(2026, 12, 31)) is NO_TERMS
