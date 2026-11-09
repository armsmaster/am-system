"""Какая ставка действует на дату: последняя дата, на одну дату скрипт главнее ISS и ЦБ."""

from datetime import date, datetime
from decimal import Decimal

from am_common.clock import MOSCOW
from marketdata.rates import CBR, ISS, RateKind, RatePoint, rate_on

EARLY = datetime(2026, 10, 27, 9, 0, tzinfo=MOSCOW)
LATE = datetime(2026, 10, 27, 10, 0, tzinfo=MOSCOW)


def point(day: int, value: str, source: str = ISS, received: datetime = EARLY) -> RatePoint:
    return RatePoint(RateKind.KEY_RATE, date(2026, 10, day), Decimal(value), source, received)


def test_latest_date_not_after_the_day() -> None:
    points = [point(23, "14"), point(26, "14.5"), point(28, "15")]
    assert rate_on(points, date(2026, 10, 27)) == points[1]
    assert rate_on(points, date(2026, 10, 22)) is None


def test_script_wins_over_iss_and_the_last_received_wins() -> None:
    iss, first, second = point(26, "14"), point(26, "14.1", "cbr"), point(26, "14.2", "cbr", LATE)
    assert rate_on([second, iss, first], date(2026, 10, 26)) == second
    # Более поздняя дата ISS главнее более ранней даты скрипта.
    assert rate_on([first, point(27, "13.9")], date(2026, 10, 27)) == point(27, "13.9")


def test_script_wins_over_the_bank_of_russia() -> None:
    # Скрипт с кодом «cbr» — это скрипт: код «cbr-ws» у загрузки самого сервиса.
    bank, script = point(26, "13.72", CBR, LATE), point(26, "13.7", "cbr")
    assert rate_on([bank, script], date(2026, 10, 26)) == script
