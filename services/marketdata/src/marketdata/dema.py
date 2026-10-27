"""DEMA по ценам закрытия OHLC-баров (решения 66 и 69).

    α   = 1 − 2^(−Δ / H)        H — период полураспада, Δ — длина бара, минуты
    E1ᵢ = α · Cᵢ  + (1 − α) · E1ᵢ₋₁
    E2ᵢ = α · E1ᵢ + (1 − α) · E2ᵢ₋₁
    DEMAᵢ = 2 · E1ᵢ − E2ᵢ

Первые E1 и E2 равны первому закрытию. Значение относится к концу бара.
Перерывы в торгах среднюю не «старят»: α зависит только от длины бара.
Период полураспада меняется со временем: каждый бар считается с тем H,
который действовал на конец бара; накопленные E1 и E2 сохраняются.

Пример с эталонными числами — docs/design/03-marketdata.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Bar:
    start: datetime
    timeframe: int  # минуты
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.timeframe)


@dataclass(frozen=True, slots=True)
class HalfLife:
    """Период полураспада, действующий с момента ``since``."""

    since: datetime
    minutes: Decimal


@dataclass(frozen=True, slots=True)
class DemaPoint:
    time: datetime  # конец бара
    value: Decimal
    ema: Decimal  # E1
    ema_of_ema: Decimal  # E2


def alpha(timeframe: int, half_life: Decimal) -> Decimal:
    return 1 - Decimal(2) ** (-Decimal(timeframe) / half_life)


def dema(bars: Sequence[Bar], half_lives: Sequence[HalfLife]) -> list[DemaPoint]:
    """DEMA по барам одного таймфрейма, отсортированным по времени.

    Бары раньше первой настройки H считаются с первой настройкой: история баров
    прогревает среднюю, даже если DEMA включили позже.
    """
    if not half_lives:
        return []
    ordered = sorted(half_lives, key=lambda h: h.since)
    alphas: dict[tuple[int, Decimal], Decimal] = {}
    points: list[DemaPoint] = []
    e1 = e2 = Decimal(0)
    for index, bar in enumerate(bars):
        half_life = _half_life_at(ordered, bar.end)
        key = (bar.timeframe, half_life)
        a = alphas.get(key)
        if a is None:
            a = alphas[key] = alpha(bar.timeframe, half_life)
        if index == 0:
            e1 = e2 = bar.close
        else:
            e1 = a * bar.close + (1 - a) * e1
            e2 = a * e1 + (1 - a) * e2
        points.append(DemaPoint(time=bar.end, value=2 * e1 - e2, ema=e1, ema_of_ema=e2))
    return points


def _half_life_at(ordered: Sequence[HalfLife], moment: datetime) -> Decimal:
    current = ordered[0].minutes
    for setting in ordered:
        if setting.since > moment:
            break
        current = setting.minutes
    return current
