"""Время по Москве: в нём работает биржа, по нему считаются торговые даты."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def moscow_now() -> datetime:
    return datetime.now(MOSCOW)
