"""Фильтр Калмана «уровень + тренд» (решение 168): эталон из docs/design/06-valuation-nav.md.

Эталонные числа посчитаны независимым прототипом во float с матрицами 2×2.
"""

from decimal import Decimal

import pytest

from am_quant.bond import QuantError
from am_quant.kalman import NAV_SETTINGS, Settings, kalman_trend


def rounded(value: Decimal, places: int) -> str:
    return str(value.quantize(Decimal(1).scaleb(-places)) + 0)


# Стоимость пая по торговым дням: пн–пт, выходные, пн–ср; время — дни от первого.
OBSERVATIONS = [
    (0, "1000.0000"),
    (1, "1000.0410"),
    (2, "1000.0790"),
    (3, "1000.1300"),
    (4, "1000.1580"),
    (7, "1000.2830"),
    (8, "1000.3050"),
    (9, "1000.3480"),
]
# Время, уровень, тренд за день.
GOLDEN = [
    (0, "1000.0000", "0.00000"),
    (1, "1000.0410", "0.04100"),
    (2, "1000.0795", "0.03950"),
    (3, "1000.1268", "0.04281"),
    (4, "1000.1625", "0.04048"),
    (7, "1000.2832", "0.04035"),
    (8, "1000.3138", "0.03879"),
    (9, "1000.3506", "0.03850"),
]


def series(pairs: list[tuple[int, str]]) -> list[tuple[Decimal, Decimal]]:
    return [(Decimal(t), Decimal(v)) for t, v in pairs]


def test_golden_nav_example() -> None:
    found = kalman_trend(series(OBSERVATIONS), NAV_SETTINGS)

    assert [(int(p.time), rounded(p.level, 4), rounded(p.trend, 5)) for p in found.points] == GOLDEN
    assert found.sigma2 is not None
    assert rounded(found.sigma2.sqrt(), 6) == "0.006641"
    # Прогноз на завтра (четверг): уровень + тренд, 68% — ± одно стандартное отклонение.
    tomorrow = found.forecast(Decimal(1))
    assert (int(tomorrow.time), rounded(tomorrow.value, 4)) == (10, "1000.3891")
    assert tomorrow.std is not None
    assert rounded(tomorrow.std, 4) == "0.0085"


# Стоимость пая с начислением по T+1 (решение 169): пн–пт и пн–ср, только торговые дни.
# Время — торговые сессии, начисление — дни от первой даты расчётов: в пятницу +3.
NAV_TIMES = [0, 1, 2, 3, 4, 5, 6, 7]
NAV_ACCRUALS = [Decimal(a) for a in (0, 1, 2, 3, 6, 7, 8, 9)]
NAV_VALUES = [
    "1000.000",
    "1000.042",
    "1000.079",
    "1000.121",
    "1000.240",
    "1000.281",
    "1000.322",
    "1000.358",
]
NAV_GOLDEN = [
    ("1000.0000", "0.00000"),
    ("1000.0420", "0.04200"),
    ("1000.0798", "0.03950"),
    ("1000.1205", "0.04001"),
    ("1000.2401", "0.03991"),
    ("1000.2806", "0.04001"),
    ("1000.3212", "0.04011"),
    ("1000.3600", "0.03993"),
]


def test_golden_accrual_by_settlement_date() -> None:
    found = kalman_trend(
        [(Decimal(t), Decimal(v)) for t, v in zip(NAV_TIMES, NAV_VALUES, strict=True)],
        NAV_SETTINGS,
        NAV_ACCRUALS,
    )

    # Пятничный скачок — три дня начисления: фильтр его ждёт, уровень почти равен цене.
    assert [(rounded(p.level, 4), rounded(p.trend, 5)) for p in found.points] == NAV_GOLDEN
    assert found.sigma2 is not None
    assert rounded(found.sigma2.sqrt(), 6) == "0.001483"
    # Завтра четверг: один день начисления; был бы четверг — до пятницы три.
    thursday = found.forecast(Decimal(1), Decimal(1))
    friday = found.forecast(Decimal(1), Decimal(3))
    assert thursday.std is not None
    assert friday.std is not None
    assert (rounded(thursday.value, 4), rounded(thursday.std, 4)) == ("1000.3999", "0.0019")
    assert (rounded(friday.value, 4), rounded(friday.std, 4)) == ("1000.4798", "0.0021")


def test_straight_line_is_followed_exactly() -> None:
    line = [(Decimal(t), Decimal(100) + Decimal("0.25") * t) for t in (0, 1, 2, 5, 6, 7)]

    found = kalman_trend(line, Settings(Decimal("0.1"), Decimal("0.001")))

    # Диффузный старт — большая, но конечная дисперсия: след на десятом знаке.
    assert abs(found.points[-1].trend - Decimal("0.25")) < Decimal("1e-8")
    assert found.sigma2 is not None
    assert found.sigma2 < Decimal("1e-15")
    assert rounded(found.forecast(Decimal(3)).value, 6) == "102.500000"


def test_short_series_has_no_noise_estimate() -> None:
    single = kalman_trend([(Decimal(0), Decimal(5))], NAV_SETTINGS)
    assert (single.points[0].level, single.sigma2, single.forecast(Decimal(1)).std) == (
        Decimal(5),
        None,
        None,
    )
    pair = kalman_trend(series([(0, "5"), (2, "6")]), NAV_SETTINGS)
    assert (rounded(pair.points[-1].trend, 6), pair.sigma2) == ("0.500000", None)


@pytest.mark.parametrize(
    ("observations", "settings", "message"),
    [
        ([], NAV_SETTINGS, "хотя бы одно"),
        (series([(0, "1"), (0, "2")]), NAV_SETTINGS, "строго расти"),
        (series([(0, "1")]), Settings(Decimal(-1), Decimal(0)), "отрицательными"),
    ],
)
def test_bad_input(
    observations: list[tuple[Decimal, Decimal]], settings: Settings, message: str
) -> None:
    with pytest.raises(QuantError, match=message):
        kalman_trend(observations, settings)


@pytest.mark.parametrize(
    ("accruals", "message"),
    [([Decimal(0)], "на каждое наблюдение"), ([Decimal(2), Decimal(1)], "не может убывать")],
)
def test_bad_accruals(accruals: list[Decimal], message: str) -> None:
    with pytest.raises(QuantError, match=message):
        kalman_trend(series([(0, "1"), (1, "2")]), NAV_SETTINGS, accruals)


def test_forecast_only_ahead() -> None:
    found = kalman_trend(series(OBSERVATIONS), NAV_SETTINGS)
    with pytest.raises(QuantError, match="вперёд"):
        found.forecast(Decimal(0))
    with pytest.raises(QuantError, match="вперёд"):
        found.forecast(Decimal(1), Decimal(-1))
    # Без начисления — уровень, но шум за время копится.
    flat = found.forecast(Decimal(1), Decimal(0))
    assert flat.value == found.points[-1].level
