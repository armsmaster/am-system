"""Оценка текущей RUONIA: ключевая ставка плюс DEMA базиса RUONIA − КС (решение 157)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from am_common.clock import MOSCOW
from am_quant.bond import QuantError
from marketdata.funding import ALPHA, HISTORY_DAYS, estimate
from marketdata.rates import RateKind, RatePoint

DAY = date(2026, 10, 28)  # день цены
RECEIVED = datetime(2026, 10, 28, 9, 0, tzinfo=MOSCOW)


def point(kind: RateKind, day: date, value: str, source: str = "iss", minute: int = 0) -> RatePoint:
    received = RECEIVED + timedelta(minutes=minute)
    return RatePoint(kind, day, Decimal(value), source, received)


def key(day: date, value: str) -> RatePoint:
    return point(RateKind.KEY_RATE, day, value)


def ruonia(day: date, value: str, source: str = "cbr-ws", minute: int = 0) -> RatePoint:
    return point(RateKind.RUONIA, day, value, source, minute)


def test_one_day_of_history() -> None:
    found = estimate([key(date(2026, 10, 26), "14"), ruonia(date(2026, 10, 27), "13.9")], DAY)

    # b = 13,90 − 14,00 = −0,10 п.п.; DEMA одного дня — сам базис.
    assert (found.basis, found.value) == (Decimal("-0.1"), Decimal("13.9"))
    assert (found.since, found.through) == (date(2026, 10, 27), date(2026, 10, 27))
    assert (found.key_rate.as_of, found.ruonia.as_of) == (date(2026, 10, 26), date(2026, 10, 27))


def test_basis_is_held_between_publications_and_follows_the_key_rate() -> None:
    fri, sat, sun, mon = (date(2026, 10, 23) + timedelta(days=n) for n in range(4))
    found = estimate(
        [
            key(date(2026, 10, 20), "14"),
            key(sun, "15"),  # КС сменилась в воскресенье
            ruonia(fri, "13.80"),
            ruonia(mon, "14.85"),
        ],
        DAY,
    )

    # Пт: b = −0,20; Сб и Вс RUONIA нет — b держится; Пн: 14,85 − 15 = −0,15.
    assert [(d.day, d.ruonia, d.key_rate, d.basis) for d in found.days] == [
        (fri, Decimal("13.80"), Decimal(14), Decimal("-0.20")),
        (sat, None, Decimal(14), Decimal("-0.20")),
        (sun, None, Decimal(15), Decimal("-0.20")),
        (mon, Decimal("14.85"), Decimal(15), Decimal("-0.15")),
    ]
    e1 = e2 = Decimal("-0.20")
    for b in (Decimal("-0.20"), Decimal("-0.20"), Decimal("-0.15")):
        e1 = ALPHA * b + (1 - ALPHA) * e1
        e2 = ALPHA * e1 + (1 - ALPHA) * e2
    assert found.basis == 2 * e1 - e2
    # Оценка — КС на день цены (15%) плюс базис.
    assert found.value == Decimal(15) + 2 * e1 - e2


def test_half_life_is_half_a_year() -> None:
    start = DAY - timedelta(days=366)
    points = [key(start, "10"), ruonia(start, "10")]  # b = 0 в первый день
    points += [ruonia(start + timedelta(days=n), "11") for n in range(1, 366)]  # дальше b = 1

    found = estimate(points, DAY)

    # Через два периода полураспада (365 дней) E1 прошла 3/4 пути от 0 к 1.
    assert abs(found.days[-1].ema - Decimal("0.75")) < Decimal("1e-20")
    # DEMA снимает запаздывание: ближе к новому базису, чем E1 (после скачка — с перелётом).
    assert abs(1 - found.basis) < abs(1 - found.days[-1].ema)


def test_history_window_and_sources() -> None:
    old = DAY - timedelta(days=HISTORY_DAYS + 1)
    found = estimate(
        [
            key(old - timedelta(days=10), "14"),
            ruonia(old, "1"),  # старше 5 лет — не в ряду
            ruonia(date(2026, 10, 26), "13.95"),
            # На одну дату скрипт главнее ЦБ, из значений скрипта — последнее полученное.
            ruonia(date(2026, 10, 26), "13.80", source="script", minute=1),
            ruonia(date(2026, 10, 26), "13.85", source="script", minute=2),
            ruonia(DAY, "20"),  # за сам день цены ещё не опубликована
        ],
        DAY,
    )

    assert [(d.day, d.ruonia) for d in found.days] == [(date(2026, 10, 26), Decimal("13.85"))]
    assert found.ruonia.source == "script"


def test_ruonia_before_the_first_key_rate_is_skipped() -> None:
    found = estimate(
        [
            ruonia(date(2026, 10, 22), "1"),
            key(date(2026, 10, 23), "14"),
            ruonia(date(2026, 10, 23), "13.9"),
        ],
        DAY,
    )

    assert found.since == date(2026, 10, 23)
    assert found.basis == Decimal("-0.1")


def test_what_is_missing() -> None:
    with pytest.raises(QuantError) as no_key:
        estimate([ruonia(date(2026, 10, 27), "13.9")], DAY)
    assert no_key.value.code == "rate_missing"
    assert "ключевой ставки" in no_key.value.message

    with pytest.raises(QuantError) as no_ruonia:
        estimate([key(date(2026, 10, 26), "14"), ruonia(DAY, "13.9")], DAY)
    assert no_ruonia.value.code == "rate_missing"
    assert "Нет RUONIA до 28.10.2026" in no_ruonia.value.message
