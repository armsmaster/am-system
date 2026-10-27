"""НКД и номинал на дату — на настоящих точках ISS (октябрь 2026)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from marketdata.accrual import CouponPeriod, OnDate, accrued_on, face_value_on

# ОФЗ 26238: купонный период 03.06–02.12.2026, купон 35,40 ₽.
OFZ = [
    CouponPeriod(date(2025, 12, 3), date(2026, 6, 3), Decimal("35.40")),
    CouponPeriod(date(2026, 6, 3), date(2026, 12, 2), Decimal("35.40")),
]
# История торгов — НКД на дату торгов; данные дня 27.10 — на дату расчётов 28.10.
OFZ_POINTS = {
    date(2026, 10, 23): Decimal("27.62"),
    date(2026, 10, 26): Decimal("28.20"),
    date(2026, 10, 28): Decimal("28.59"),
}


def test_iss_point_as_is() -> None:
    assert accrued_on(date(2026, 10, 26), OFZ_POINTS, OFZ) == OnDate(
        Decimal("28.20"), "iss", "точка ISS"
    )


def test_day_without_point_from_the_start_of_the_coupon_period() -> None:
    # 28,2033 + 35,40 / 182 = 28,3978 — не 28,20 + 0,1945 = 28,39.
    result = accrued_on(date(2026, 10, 27), OFZ_POINTS, OFZ)

    assert result is not None
    assert (result.value, result.method) == (Decimal("28.40"), "period_start")


def test_weekend_between_points() -> None:
    result = accrued_on(date(2026, 10, 25), OFZ_POINTS, OFZ)

    assert result is not None
    assert result.value == Decimal("28.01")


def test_other_convention_is_counted_from_the_previous_iss_point() -> None:
    # Купон по другой конвенции: точки ISS расходятся с расчётом от начала периода.
    points = {date(2026, 10, 26): Decimal("29.00")}

    result = accrued_on(date(2026, 10, 27), points, OFZ)

    assert result is not None
    assert (result.value, result.method) == (Decimal("29.19"), "previous_point")
    assert result.note == "от точки ISS за 26.10.2026 по дневному начислению купона"


def test_coupon_date_starts_a_new_period() -> None:
    result = accrued_on(date(2026, 6, 3), {}, OFZ)

    assert result is not None
    assert result.value == Decimal("0.00")


# Линкер ВЭБ.РФ: сумма текущего купона неизвестна, НКД — между точками ISS.
VEB = [CouponPeriod(date(2026, 5, 15), date(2026, 11, 19), None)]


def test_unknown_coupon_amount_is_interpolated_between_points() -> None:
    points = {date(2026, 5, 15): Decimal("0"), date(2026, 5, 18): Decimal("0.15")}

    result = accrued_on(date(2026, 5, 16), points, VEB)

    assert result is not None
    assert (result.value, result.method) == (Decimal("0.05"), "linear")


def test_unknown_coupon_amount_extrapolated_by_the_last_two_points() -> None:
    points = {date(2026, 5, 18): Decimal("0.15"), date(2026, 5, 19): Decimal("0.20")}

    result = accrued_on(date(2026, 5, 20), points, VEB)

    assert result is not None
    assert result.value == Decimal("0.25")
    assert result.note == "сумма купона неизвестна — по темпу двух последних точек ISS"


def test_nothing_to_count_from() -> None:
    # Нет купонного периода на дату, а у линкера — меньше двух точек.
    assert accrued_on(date(2030, 1, 1), OFZ_POINTS, OFZ) is None
    assert accrued_on(date(2026, 5, 20), {date(2026, 5, 18): Decimal("0.15")}, VEB) is None


def test_face_value_grows_every_day_between_points() -> None:
    points = {date(2026, 5, 15): Decimal("1000.00"), date(2026, 5, 18): Decimal("1001.16")}

    assert face_value_on(date(2026, 5, 16), points) == OnDate(
        Decimal("1000.39"), "geometric", "между точками ISS с постоянным дневным темпом"
    )
    assert face_value_on(date(2026, 5, 17), points) is not None
    assert face_value_on(date(2026, 5, 18), points) == OnDate(
        Decimal("1001.16"), "iss", "точка ISS"
    )


def test_face_value_after_the_last_point_and_before_the_first() -> None:
    points = {date(2026, 5, 18): Decimal("1001.16")}

    held = face_value_on(date(2026, 5, 19), points)
    assert held is not None
    assert (held.value, held.method) == (Decimal("1001.16"), "held")
    assert face_value_on(date(2026, 5, 1), points) is None
