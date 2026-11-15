"""Текущая RUONIA для расчётов: ключевая ставка плюс сглаженный базис (решение 157).

RUONIA публикуется на следующий рабочий день и колеблется день ко дню. Вместо
последнего значения берём оценку R̂ = КС на день цены + Б, где Б — DEMA базиса
b = RUONIA − КС с периодом полураспада полгода:

    b(d)  = RUONIA(d) − КС(d)        по дням публикации RUONIA; между ними b держится
    α     = 1 − 2^(−1/182,5)         каждый календарный день — одно наблюдение
    E1(d) = α·b(d) + (1−α)·E1(d−1),  E2(d) = α·E1(d) + (1−α)·E2(d−1),  Б(d) = 2·E1(d) − E2(d)

Первые E1 и E2 — первый базис истории. Ставки — в % годовых, базис — в п.п.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from am_quant.bond import QuantError
from marketdata.rates import SYSTEM_SOURCES, RateKind, RatePoint, rate_on

HALF_LIFE_DAYS = Decimal("182.5")
ALPHA = 1 - Decimal(2) ** (-1 / HALF_LIFE_DAYS)
# История базиса — 5 лет: через 10 периодов полураспада вес начального значения < 0,1%.
HISTORY_DAYS = 1826


@dataclass(frozen=True, slots=True)
class BasisDay:
    """Календарный день ряда базиса."""

    day: date
    ruonia: Decimal | None  # RUONIA за этот день; None — не публиковалась, базис держится
    key_rate: Decimal  # КС, действовавшая в этот день
    basis: Decimal  # b, п.п.
    ema: Decimal  # E1
    ema_of_ema: Decimal  # E2
    dema: Decimal  # Б = 2·E1 − E2


@dataclass(frozen=True, slots=True)
class Funding:
    """Оценка текущей RUONIA и из чего она сложилась."""

    key_rate: RatePoint  # КС на день цены
    ruonia: RatePoint  # последняя RUONIA, известная до дня цены, — для справки
    days: tuple[BasisDay, ...]  # ряд базиса по календарным дням

    @property
    def basis(self) -> Decimal:
        """Б — DEMA базиса на последний день ряда, п.п."""
        return self.days[-1].dema

    @property
    def value(self) -> Decimal:
        """R̂ = КС + Б, % годовых."""
        return self.key_rate.value + self.basis

    @property
    def since(self) -> date:
        return self.days[0].day

    @property
    def through(self) -> date:
        return self.days[-1].day


def by_date(points: Iterable[RatePoint]) -> dict[date, RatePoint]:
    """Значение ставки на каждую дату: как в rate_on — скрипт главнее ISS и ЦБ, из
    нескольких значений скрипта — последнее полученное."""
    found: dict[date, RatePoint] = {}
    for point in sorted(points, key=lambda p: (p.source not in SYSTEM_SOURCES, p.received_at)):
        found[point.as_of] = point
    return found


def estimate(points: Sequence[RatePoint], day: date) -> Funding:
    """R̂ на день цены ``day`` по истории RUONIA и КС (``points`` — обе ставки).

    Базис — по последний день, за который RUONIA известна до дня цены: RUONIA за
    сам день цены публикуется только назавтра.
    """
    ruonia = {
        d: p
        for d, p in by_date(p for p in points if p.kind is RateKind.RUONIA).items()
        if day - timedelta(days=HISTORY_DAYS) <= d < day
    }
    keys = by_date(p for p in points if p.kind is RateKind.KEY_RATE)
    key_rate = rate_on((p for p in points if p.kind is RateKind.KEY_RATE), day)
    if key_rate is None:
        raise QuantError("rate_missing", f"Нет ключевой ставки на {day:%d.%m.%Y}.")
    # Базис начинается с первой RUONIA, на день которой ключевая ставка уже известна.
    first_key = min(keys)
    published = sorted(d for d in ruonia if d >= first_key)
    if not published:
        raise QuantError(
            "rate_missing",
            f"Нет RUONIA до {day:%d.%m.%Y} — сервис загружает её с сайта Банка России "
            "на следующий рабочий день после 15:00.",
        )
    days: list[BasisDay] = []
    # Первая RUONIA ряда — не раньше первой ключевой ставки: такая дата есть.
    current_key = keys[max(d for d in keys if d <= published[0])].value
    basis = e1 = e2 = Decimal(0)
    d = published[0]
    while d <= published[-1]:
        if d in keys:
            current_key = keys[d].value
        published_today = ruonia.get(d)
        if published_today is not None:
            basis = published_today.value - current_key
        if not days:
            e1 = e2 = basis
        else:
            e1 = ALPHA * basis + (1 - ALPHA) * e1
            e2 = ALPHA * e1 + (1 - ALPHA) * e2
        days.append(
            BasisDay(
                day=d,
                ruonia=None if published_today is None else published_today.value,
                key_rate=current_key,
                basis=basis,
                ema=e1,
                ema_of_ema=e2,
                dema=2 * e1 - e2,
            )
        )
        d += timedelta(days=1)
    return Funding(key_rate=key_rate, ruonia=ruonia[published[-1]], days=tuple(days))
