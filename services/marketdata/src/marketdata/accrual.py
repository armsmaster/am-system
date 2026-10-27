"""НКД и номинал на дату по точкам MOEX ISS (решения 16, 60, 63, 73).

Точка ISS — значение на конкретную дату: в истории торгов — на дату торгов,
в данных дня — на дату расчётов. На дату без точки значение досчитываем:

- НКД при известной сумме купона — от начала купонного периода по дневному
  начислению (сумма купона / дней в периоде), если такой расчёт сходится
  с точками ISS этого периода до полукопейки. Не сходится (у бумаги другая
  конвенция) — от ближайшей предыдущей точки ISS по тому же начислению.
- НКД при неизвестной сумме купона (флоатер, линкер) — линейно между
  точками ISS того же периода.
- Номинал — с постоянным дневным темпом между точками: индекс начисляется
  и в выходные.

Округляем до копейки один раз, в конце: округлённые точки ISS между собой
не складываем.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

KOPECK = Decimal("0.01")
HALF_KOPECK = Decimal("0.005")


@dataclass(frozen=True, slots=True)
class CouponPeriod:
    start: date
    end: date  # дата выплаты
    amount: Decimal | None  # None — сумма ещё неизвестна

    @property
    def days(self) -> int:
        return (self.end - self.start).days


@dataclass(frozen=True, slots=True)
class OnDate:
    value: Decimal
    method: str  # iss | period_start | previous_point | linear | geometric | held
    note: str


def accrued_on(
    day: date, points: Mapping[date, Decimal], periods: Sequence[CouponPeriod]
) -> OnDate | None:
    """НКД на дату. ``None`` — досчитать не из чего."""
    if day in points:
        return OnDate(points[day], "iss", "точка ISS")
    period = next((p for p in periods if p.start <= day < p.end), None)
    if period is None:
        return None
    inside = sorted((d, v) for d, v in points.items() if period.start <= d < period.end)
    if period.amount is None:
        return _between_points(day, inside)
    return _by_daily_accrual(day, period, period.amount, inside)


def _by_daily_accrual(
    day: date, period: CouponPeriod, amount: Decimal, inside: Sequence[tuple[date, Decimal]]
) -> OnDate:
    per_day = amount / period.days

    def from_start(d: date) -> Decimal:
        return per_day * (d - period.start).days

    before = [(d, v) for d, v in inside if d < day]
    if all(abs(from_start(d) - v) <= HALF_KOPECK for d, v in inside) or not before:
        return OnDate(
            _kopecks(from_start(day)),
            "period_start",
            "от начала купонного периода: сумма купона / дней в периоде",
        )
    d, v = before[-1]
    return OnDate(
        _kopecks(v + per_day * (day - d).days),
        "previous_point",
        f"от точки ISS за {d:%d.%m.%Y} по дневному начислению купона",
    )


def _between_points(day: date, inside: Sequence[tuple[date, Decimal]]) -> OnDate | None:
    before = [(d, v) for d, v in inside if d < day]
    after = [(d, v) for d, v in inside if d > day]
    if before and after:
        (d1, v1), (d2, v2) = before[-1], after[0]
        value = v1 + (v2 - v1) * (day - d1).days / (d2 - d1).days
        return OnDate(_kopecks(value), "linear", "сумма купона неизвестна — между точками ISS")
    if len(before) >= 2:  # noqa: PLR2004 — темп начисления виден по двум последним точкам
        (d1, v1), (d2, v2) = before[-2], before[-1]
        value = v2 + (v2 - v1) * (day - d2).days / (d2 - d1).days
        return OnDate(
            _kopecks(value), "linear", "сумма купона неизвестна — по темпу двух последних точек ISS"
        )
    return None


def face_value_on(day: date, points: Mapping[date, Decimal]) -> OnDate | None:
    """Номинал на дату. ``None`` — точек раньше даты нет."""
    if day in points:
        return OnDate(points[day], "iss", "точка ISS")
    before = max((d for d in points if d < day), default=None)
    after = min((d for d in points if d > day), default=None)
    if before is None:
        return None
    if after is None:
        return OnDate(points[before], "held", f"последняя точка ISS за {before:%d.%m.%Y}")
    n1, n2 = points[before], points[after]
    share = Decimal((day - before).days) / Decimal((after - before).days)
    return OnDate(
        _kopecks(n1 * (n2 / n1) ** share),
        "geometric",
        "между точками ISS с постоянным дневным темпом",
    )


def _kopecks(value: Decimal) -> Decimal:
    return value.quantize(KOPECK, rounding=ROUND_HALF_UP)
