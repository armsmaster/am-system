"""Правила версий бумаги: версия на дату, слияние с ISS, ручная правка, предупреждения."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from refdata.domain import (
    Amortization,
    Coupon,
    CouponKind,
    FloaterTerms,
    IssFloater,
    NominalIndexation,
    Params,
    RateKind,
    RuleViolationError,
    Version,
    alerts,
    apply_edit,
    changed_fields,
    check_new_version_date,
    issue_terms,
    journal_view,
    merge_from_iss,
    version_on,
)

BASE = Params(
    name="ОФЗ-ПК 29014",
    short_name="ОФЗ 29014",
    board="TQOB",
    currency="RUB",
    initial_face_value=Decimal(1000),
    maturity_date=date(2026, 3, 25),
    coupon_kind=CouponKind.FLOATER,
    coupons=(Coupon(date=date(2025, 12, 24), rate=Decimal("16.45"), amount=Decimal(41)),),
)
TERMS = FloaterTerms(rate=RateKind.RUONIA, margin_bp=Decimal(0), capitalized=True)


def version(
    number: int, valid_from: date | None, params: Params = BASE, **fields: object
) -> Version:
    return Version(
        number=number,
        valid_from=valid_from,
        source="iss",
        author="moex-iss",
        comment=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        params=params,
        **fields,
    )


# --- версия на дату -----------------------------------------------------------


def test_version_on_date() -> None:
    first = version(1, None)
    second = version(2, date(2026, 2, 1))
    same_day_fix = version(3, date(2026, 2, 1))
    versions = [first, second, same_day_fix]

    assert version_on(versions, date(2025, 1, 1)) is first
    assert version_on(versions, date(2026, 1, 31)) is first
    # В один день — побеждает более поздняя версия.
    assert version_on(versions, date(2026, 2, 1)) is same_day_fix
    assert version_on([version(1, date(2026, 5, 1))], date(2026, 1, 1)) is None


def test_new_version_cannot_start_before_the_current_one() -> None:
    latest = version(2, date(2026, 2, 1))

    check_new_version_date(latest, date(2026, 2, 1))
    check_new_version_date(version(1, None), date(2020, 1, 1))
    with pytest.raises(RuleViolationError) as too_early:
        check_new_version_date(latest, date(2026, 1, 31))
    with pytest.raises(RuleViolationError) as missing:
        check_new_version_date(latest, None)

    assert too_early.value.code == "valid_from_too_early"
    assert "01.02.2026" in too_early.value.message
    assert missing.value.code == "valid_from_required"


# --- синхронизация с ISS ------------------------------------------------------


def test_iss_changes_are_taken_except_pinned_and_manual_fields() -> None:
    current = version(
        2,
        date(2026, 1, 1),
        params=BASE.model_copy(update={"floater": TERMS, "short_name": "Флоатер 29014"}),
        pinned=frozenset({"short_name"}),
    )
    from_iss = BASE.model_copy(
        update={"maturity_date": date(2026, 4, 1), "short_name": "ОФЗ 29014"}
    )

    merged = merge_from_iss(current, from_iss)

    assert merged.maturity_date == date(2026, 4, 1)  # из ISS
    assert merged.short_name == "Флоатер 29014"  # закреплено вручную
    assert merged.floater == TERMS  # ISS не знает параметров флоатера
    assert changed_fields(current.params, merged) == ["maturity_date"]


# --- ручная правка ------------------------------------------------------------


def test_manual_edit_pins_changed_fields() -> None:
    current = version(1, None)

    params, pinned = apply_edit(
        current,
        {"floater": TERMS.model_dump(), "coupon_kind": "floater", "short_name": "Новое"},
        unpin=[],
        iss_snapshot=BASE,
    )

    assert params.floater == TERMS
    assert params.short_name == "Новое"
    # floater — только ручное поле, его не закрепляем; coupon_kind не изменился.
    assert pinned == {"short_name"}


def test_unpin_takes_value_from_iss() -> None:
    current = version(
        2,
        date(2026, 1, 1),
        params=BASE.model_copy(update={"short_name": "Ручное"}),
        pinned=frozenset({"short_name"}),
    )

    params, pinned = apply_edit(current, {}, unpin=["short_name"], iss_snapshot=BASE)

    assert params.short_name == "ОФЗ 29014"
    assert pinned == frozenset()


@pytest.mark.parametrize(
    ("changes", "unpin", "snapshot", "code"),
    [
        ({"nonsense": 1}, [], BASE, "unknown_field"),
        ({"short_name": "x"}, ["short_name"], BASE, "edit_and_unpin"),
        ({}, ["short_name"], None, "no_iss_snapshot"),
        ({"initial_face_value": "много"}, [], BASE, "invalid_params"),
    ],
)
def test_bad_edits_are_refused(
    changes: dict[str, object], unpin: list[str], snapshot: Params | None, code: str
) -> None:
    with pytest.raises(RuleViolationError) as caught:
        apply_edit(version(1, None), changes, unpin, snapshot)

    assert caught.value.code == code


# --- предупреждения -----------------------------------------------------------


def test_alerts_tell_what_blocks_calculations() -> None:
    floater_without_terms = alerts(BASE, [], None)
    unknown = alerts(
        BASE.model_copy(
            update={
                "coupon_kind": CouponKind.UNKNOWN,
                "nominal_indexation": NominalIndexation.UNKNOWN,
            }
        ),
        [],
        None,
    )
    complete = alerts(BASE.model_copy(update={"floater": TERMS}), [], BASE)

    assert [a.code for a in floater_without_terms] == ["floater_terms_missing"]
    assert [a.code for a in unknown] == ["coupon_kind_unknown", "indexation_unknown"]
    assert complete == []


def by_iss(benchmark: str, rate: RateKind, margin_bp: str | None) -> Params:
    margin = None if margin_bp is None else Decimal(margin_bp)
    return BASE.model_copy(
        update={"iss_floater": IssFloater(benchmark=benchmark, rate=rate, margin_bp=margin)}
    )


def test_missing_floater_terms_hint_at_iss() -> None:
    [alert] = alerts(by_iss("RUONIA", RateKind.RUONIA, "175.00"), [], None)

    assert alert.message == (
        "Не заполнены параметры флоатера — спред по бумаге не посчитать. По ISS: RUONIA + 175 бп."
    )
    # Маржи в ISS нет — подсказываем только ставку.
    [no_margin] = alerts(by_iss("RUONIA", RateKind.RUONIA, None), [], None)
    assert no_margin.message.endswith("По ISS: RUONIA.")


@pytest.mark.parametrize(
    ("iss", "mine", "message"),
    [
        # Ставка другая.
        (
            ("RREFKEYR", RateKind.KEY_RATE, "130"),
            FloaterTerms(rate=RateKind.RUONIA, margin_bp=Decimal(130), capitalized=False),
            "Параметры флоатера расходятся с ISS: в справочнике — RUONIA + 130 бп, "
            "в ISS — ключевая ставка + 130 бп. Проверьте по проспекту.",
        ),
        # Маржа другая — например, эмитент пересмотрел её на оферте.
        (
            ("RUONIA", RateKind.RUONIA, "120"),
            FloaterTerms(rate=RateKind.RUONIA, margin_bp=Decimal(-15), capitalized=True),
            "Параметры флоатера расходятся с ISS: в справочнике — RUONIA − 15 бп, "
            "в ISS — RUONIA + 120 бп. Проверьте по проспекту.",
        ),
        # Ставку ISS называет незнакомым кодом — сверяем только маржу.
        (
            ("MOSPRIME3M", RateKind.OTHER, "50"),
            FloaterTerms(rate=RateKind.OTHER, margin_bp=Decimal(40), capitalized=False),
            "Параметры флоатера расходятся с ISS: в справочнике — другая ставка + 40 бп, "
            "в ISS — MOSPRIME3M + 50 бп. Проверьте по проспекту.",
        ),
    ],
)
def test_floater_terms_that_differ_from_iss(
    iss: tuple[str, RateKind, str], mine: FloaterTerms, message: str
) -> None:
    params = by_iss(*iss).model_copy(update={"floater": mine})

    assert [a.message for a in alerts(params, [], None)] == [message]


@pytest.mark.parametrize(
    "iss",
    [
        ("RUONIA", RateKind.RUONIA, "0"),
        ("MOSPRIME3M", RateKind.OTHER, "0"),
        ("RUONIA", RateKind.RUONIA, None),
    ],
)
def test_floater_terms_that_agree_with_iss(iss: tuple[str, RateKind, str | None]) -> None:
    # TERMS — RUONIA + 0 бп; маржи в ISS нет — сверять нечего.
    assert alerts(by_iss(*iss).model_copy(update={"floater": TERMS}), [], None) == []


def test_pinned_field_that_differs_from_iss_is_flagged() -> None:
    edited = BASE.model_copy(update={"floater": TERMS, "short_name": "Ручное"})

    flagged = alerts(edited, ["short_name"], BASE)
    agreed = alerts(BASE.model_copy(update={"floater": TERMS}), ["short_name"], BASE)

    assert [a.message for a in flagged] == [
        "Поле «Краткое название» закреплено вручную и расходится с ISS."
    ]
    assert agreed == []


def test_journal_view_is_short_and_readable() -> None:
    view = journal_view(BASE.model_copy(update={"floater": TERMS}))

    assert view["coupons"] == "1 зап."
    assert view["initial_face_value"] == "1000"
    assert view["maturity_date"] == "2026-03-25"
    assert view["coupon_kind"] == "floater"
    assert view["issuer"] is None
    assert view["floater"] == {"rate": "ruonia", "margin_bp": "0", "capitalized": True}


# --- условия выпуска ----------------------------------------------------------

LINKER = Params(
    name="ВЭБ.РФ ПБО-002Р-58",
    short_name="ВЭБ2Р-58",
    board="TQCB",
    currency="RUB",
    initial_face_value=Decimal(1000),
    coupon_kind=CouponKind.FIXED,
    nominal_indexation=NominalIndexation.RUONIA,
    coupons=(
        Coupon(date=date(2026, 11, 19), rate=Decimal("1.85"), amount=Decimal("10.18")),
        Coupon(date=date(2027, 5, 26), rate=Decimal("1.85"), amount=Decimal("10.18")),
    ),
    amortizations=(
        Amortization(
            date=date(2029, 6, 16),
            percent=Decimal("106.51"),
            amount=Decimal("1065.1"),
            is_maturity=True,
        ),
    ),
)


def test_linker_keeps_only_amounts_already_paid() -> None:
    terms = issue_terms(LINKER, date(2026, 11, 19))

    # Купон 19.11 выплачен — его сумма стала фактом. Будущие суммы ISS считает
    # по сегодняшнему номиналу: это прогноз, а не условие выпуска.
    assert [c.amount for c in terms.coupons] == [Decimal("10.18"), None]
    assert [c.rate for c in terms.coupons] == [Decimal("1.85"), Decimal("1.85")]
    [maturity] = terms.amortizations
    assert (maturity.percent, maturity.amount, maturity.is_maturity) == (None, None, True)


def test_unknown_index_is_still_an_indexed_nominal() -> None:
    linker = LINKER.model_copy(update={"nominal_indexation": NominalIndexation.UNKNOWN})

    assert issue_terms(linker, date(2026, 10, 26)).coupons[0].amount is None


def test_bond_with_fixed_nominal_keeps_every_amount() -> None:
    assert issue_terms(BASE, date(2020, 1, 1)) is BASE
