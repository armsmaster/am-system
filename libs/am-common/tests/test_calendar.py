from __future__ import annotations

from datetime import date, timedelta

import pytest

from am_common.calendar import add_trading_days, is_trading_day, previous_trading_day

# 2026: 4 ноября — праздник (среда); допустим, 1 ноября (воскресенье) — рабочий день.
EXCEPTIONS = {date(2026, 11, 4): False, date(2026, 11, 1): True}


@pytest.mark.parametrize(
    ("day", "trading"),
    [
        (date(2026, 11, 2), True),  # понедельник
        (date(2026, 10, 31), False),  # суббота
        (date(2026, 11, 4), False),  # праздник
        (date(2026, 11, 1), True),  # рабочее воскресенье
    ],
)
def test_trading_days(day: date, trading: bool) -> None:
    assert is_trading_day(day, EXCEPTIONS) is trading


@pytest.mark.parametrize(
    ("day", "count", "expected"),
    [
        (date(2026, 11, 3), 1, date(2026, 11, 5)),  # T+1 через праздник
        (date(2026, 10, 30), 1, date(2026, 11, 1)),  # пятница → рабочее воскресенье
        (date(2026, 11, 2), 0, date(2026, 11, 2)),  # T+0 в торговый день
        (date(2026, 10, 31), 0, date(2026, 11, 1)),  # T+0 в выходной — ближайший торговый
    ],
)
def test_settlement_shift(day: date, count: int, expected: date) -> None:
    assert add_trading_days(day, count, EXCEPTIONS) == expected


def test_shift_errors() -> None:
    with pytest.raises(ValueError, match="negative"):
        add_trading_days(date(2026, 11, 2), -1, {})
    every_day_off = {date(2026, 1, 1) + timedelta(days=n): False for n in range(800)}
    with pytest.raises(ValueError, match="no trading days"):
        add_trading_days(date(2026, 1, 1), 0, every_day_off)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 11, 2), date(2026, 11, 2)),  # торговый день — он сам
        (date(2026, 10, 31), date(2026, 10, 30)),  # суббота — пятница
        (date(2026, 11, 4), date(2026, 11, 3)),  # праздник — накануне
        (date(2026, 11, 1), date(2026, 11, 1)),  # рабочее воскресенье
    ],
)
def test_previous_trading_day(day: date, expected: date) -> None:
    assert previous_trading_day(day, EXCEPTIONS) == expected


def test_previous_trading_day_needs_a_trading_day() -> None:
    every_day_off = {date(2026, 1, 1) - timedelta(days=n): False for n in range(800)}
    with pytest.raises(ValueError, match="no trading days"):
        previous_trading_day(date(2026, 1, 1), every_day_off)
