"""Торговый календарь Московской биржи.

По умолчанию торговые дни — с понедельника по пятницу. Исключения
(праздники и рабочие выходные) сопровождение заносит раз в год
(решение 57); здесь они передаются словарём «дата → торговый ли день».
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta

# Сдвиг дальше разумного горизонта означает ошибку в календаре, а не реальный запрос.
_MAX_DAYS_SCANNED = 366
_SATURDAY = 5  # date.weekday(): понедельник — 0


def is_trading_day(day: date, exceptions: Mapping[date, bool]) -> bool:
    if day in exceptions:
        return exceptions[day]
    return day.weekday() < _SATURDAY


def previous_trading_day(day: date, exceptions: Mapping[date, bool]) -> date:
    """Последний торговый день не позже ``day``: в субботу — пятница."""
    current = day
    for _ in range(_MAX_DAYS_SCANNED):
        if is_trading_day(current, exceptions):
            return current
        current -= timedelta(days=1)
    raise ValueError("no trading days found — check the calendar exceptions")


def add_trading_days(day: date, count: int, exceptions: Mapping[date, bool]) -> date:
    """Дата через ``count`` торговых дней (T+count).

    ``count = 0`` — сам день, если он торговый, иначе ближайший следующий торговый.
    """
    if count < 0:
        raise ValueError("count must not be negative")
    current = day
    remaining = count
    for _ in range(_MAX_DAYS_SCANNED + count):
        if is_trading_day(current, exceptions):
            if remaining == 0:
                return current
            remaining -= 1
        current += timedelta(days=1)
    raise ValueError("no trading days found — check the calendar exceptions")
