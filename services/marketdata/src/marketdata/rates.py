"""Денежные ставки для расчёта спредов: RUONIA, ключевая ставка, RUSFAR (вопрос 76).

Ключевую ставку и RUSFAR берём из индексов ISS (KEYRATE, RUSFAR); RUONIA
ISS не публикует — её присылает скрипт. Ставка скрипта на ту же дату главнее
ставки ISS. Ставка на дату — последняя известная не позже этой даты.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class RateKind(StrEnum):
    KEY_RATE = "key_rate"
    RUONIA = "ruonia"
    RUSFAR = "rusfar"


RATE_LABELS = {
    RateKind.KEY_RATE: "Ключевая ставка",
    RateKind.RUONIA: "RUONIA",
    RateKind.RUSFAR: "RUSFAR",
}
# Индексы ISS (рынок index): код → ставка.
ISS_INDICES = {RateKind.KEY_RATE: "KEYRATE", RateKind.RUSFAR: "RUSFAR"}
ISS = "iss"


@dataclass(frozen=True, slots=True)
class RatePoint:
    kind: RateKind
    as_of: date
    value: Decimal  # % годовых
    source: str  # iss или источник скрипта
    received_at: datetime


def rate_on(points: Iterable[RatePoint], day: date) -> RatePoint | None:
    """Ставка на дату: последняя дата не позже ``day``; на одну дату скрипт главнее ISS,
    из нескольких значений скрипта — последнее полученное."""
    known = [p for p in points if p.as_of <= day]
    if not known:
        return None
    return max(known, key=lambda p: (p.as_of, p.source != ISS, p.received_at))
