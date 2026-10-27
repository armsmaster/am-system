"""DEMA: эталонная таблица из docs/design/03-marketdata.md и правила сглаживания."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from am_common.clock import MOSCOW
from marketdata.dema import Bar, HalfLife, alpha, dema

START = datetime(2026, 10, 27, 10, 0, tzinfo=MOSCOW)
H60 = HalfLife(since=START, minutes=Decimal(60))


def bars(*closes: str, timeframe: int = 10, start: datetime = START) -> list[Bar]:
    return [
        Bar(
            start=start + timedelta(minutes=timeframe * i),
            timeframe=timeframe,
            open=Decimal(c),
            high=Decimal(c),
            low=Decimal(c),
            close=Decimal(c),
        )
        for i, c in enumerate(closes)
    ]


# Выброс 99,10 в 10:20 при рыночном уровне около 99,70; H = 60 минут, бары по 10 минут.
REFERENCE = [
    ("99.70", "99.7000", "99.7000", "99.7000"),
    ("99.72", "99.7022", "99.7002", "99.7041"),
    ("99.10", "99.6365", "99.6933", "99.5797"),
    ("99.71", "99.6445", "99.6880", "99.6010"),
    ("99.73", "99.6538", "99.6842", "99.6234"),
    ("99.74", "99.6632", "99.6819", "99.6445"),
    ("99.74", "99.6716", "99.6808", "99.6624"),
    ("99.75", "99.6802", "99.6807", "99.6796"),
    ("99.75", "99.6878", "99.6815", "99.6940"),
    ("99.76", "99.6957", "99.6831", "99.7083"),
]


def four(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001")))


def test_reference_table_from_the_methodology() -> None:
    points = dema(bars(*(row[0] for row in REFERENCE)), [H60])

    assert [(four(p.ema), four(p.ema_of_ema), four(p.value)) for p in points] == [
        row[1:] for row in REFERENCE
    ]
    # Значение относится к концу бара.
    assert points[0].time == START + timedelta(minutes=10)


@pytest.mark.parametrize(
    ("timeframe", "half_life", "expected"),
    [(10, 60, "0.1091"), (30, 60, "0.2929"), (60, 60, "0.5000"), (10, 120, "0.0561")],
)
def test_alpha_depends_on_bar_length_and_half_life(
    timeframe: int, half_life: int, expected: str
) -> None:
    assert four(alpha(timeframe, Decimal(half_life))) == expected


def test_same_half_life_smooths_comparably_on_any_timeframe() -> None:
    # Час из шести 10-минутных баров по 101 после уровня 100 и один часовой бар по 101.
    ten = dema(bars("100", *["101"] * 6), [H60])[-1]
    hour = dema(bars("100", "101", timeframe=60), [H60])[-1]

    assert (
        ten.ema.quantize(Decimal("0.01")) == hour.ema.quantize(Decimal("0.01")) == Decimal("100.50")
    )


def test_breaks_in_trading_do_not_age_the_average() -> None:
    evening = bars("100", "101")
    # Следующий бар — утром, через 14 часов: вес у него тот же, что у соседнего бара.
    morning = bars("102", start=evening[-1].end + timedelta(hours=14))
    together = dema([*evening, *morning], [H60])
    in_a_row = dema(bars("100", "101", "102"), [H60])

    assert [p.value for p in together] == [p.value for p in in_a_row]


def test_new_half_life_applies_from_its_moment_without_a_jump() -> None:
    series = bars("100", "100", "110", "110")
    slower = HalfLife(since=series[2].end, minutes=Decimal(600))
    points = dema(series, [H60, slower])
    fast_only = dema(series, [H60])

    # До смены — как раньше; с бара, закрытого после смены, — медленнее.
    assert points[:2] == fast_only[:2]
    assert points[2].value < fast_only[2].value


def test_history_before_the_first_setting_warms_up_the_average() -> None:
    series = bars("100", "101", "102")
    late = HalfLife(since=series[-1].end, minutes=Decimal(60))

    assert dema(series, [late]) == dema(series, [H60])


def test_nothing_without_settings() -> None:
    assert dema(bars("100"), []) == []
