"""Цена для оценки: ручная цена, правило, устаревшая цена, два источника."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from am_common.clock import MOSCOW
from marketdata.prices import (
    ISS,
    MANUAL,
    Choice,
    ManualPrice,
    Observation,
    PriceType,
    Rule,
    RuleViolationError,
    choose_price,
    latest,
    spread_bp,
)

TUE = date(2026, 10, 27)
AT = datetime(2026, 10, 27, 11, 5, tzinfo=MOSCOW)
VWAP_RULE = Rule()
VWAP_THEN_DEMA = Rule(
    types=(PriceType.VWAP, PriceType.DEMA), dema_timeframe=10, dema_half_life=Decimal(60)
)


def seen(kind: PriceType, value: str, at: datetime, source: str = "quik") -> Observation:
    return Observation(type=kind, source=source, time=at, value=Decimal(value))


def choose(
    rule: Rule = VWAP_RULE,
    *found: Observation,
    manual: ManualPrice | None = None,
    at: datetime = AT,
    trading_day: date = TUE,
) -> Choice | None:
    return choose_price(
        at=at,
        rule=rule,
        latest_by_type={o.type: o for o in found},
        manual=manual,
        trading_day=trading_day,
    )


def test_first_type_of_the_rule_with_a_price_today() -> None:
    vwap = seen(PriceType.VWAP, "51.823", datetime(2026, 10, 27, 11, 0, tzinfo=MOSCOW))

    choice = choose(VWAP_RULE, vwap)

    assert choice == Choice(Decimal("51.823"), "vwap", "quik", vwap.time, False, "по правилу: VWAP")


def test_next_type_when_the_first_has_nothing_today() -> None:
    yesterday = seen(PriceType.VWAP, "99.70", datetime(2026, 10, 26, 23, 59, tzinfo=MOSCOW), ISS)
    smoothed = seen(PriceType.DEMA, "99.6445", datetime(2026, 10, 27, 11, 0, tzinfo=MOSCOW))

    choice = choose(VWAP_THEN_DEMA, yesterday, smoothed)

    assert choice is not None
    assert (choice.type, choice.value, choice.stale) == ("dema", Decimal("99.6445"), False)
    assert choice.reason == "за сегодня нет: VWAP — берём DEMA"


def test_stale_first_type_when_nothing_today() -> None:
    yesterday = seen(PriceType.VWAP, "99.70", datetime(2026, 10, 26, 23, 59, tzinfo=MOSCOW), ISS)

    choice = choose(VWAP_THEN_DEMA, yesterday)

    assert choice is not None
    assert (choice.type, choice.source, choice.stale) == ("vwap", ISS, True)
    assert choice.reason == "за сегодня цен нет — последняя известная VWAP от 26.10.2026"


def test_friday_price_is_current_on_saturday() -> None:
    friday = seen(PriceType.VWAP, "99.70", datetime(2026, 10, 30, 23, 59, tzinfo=MOSCOW))
    saturday = datetime(2026, 10, 31, 12, 0, tzinfo=MOSCOW)

    choice = choose(VWAP_RULE, friday, at=saturday, trading_day=date(2026, 10, 30))

    assert choice is not None
    assert choice.stale is False


def test_manual_price_wins_until_the_end_of_the_day() -> None:
    vwap = seen(PriceType.VWAP, "99.70", datetime(2026, 10, 27, 11, 0, tzinfo=MOSCOW))
    manual = ManualPrice(
        value=Decimal("99.50"),
        entered_at=datetime(2026, 10, 27, 10, 0, tzinfo=MOSCOW),
        author="trader",
        comment="котировка брокера",
    )

    choice = choose(VWAP_RULE, vwap, manual=manual)
    assert choice is not None
    assert (choice.type, choice.source, choice.value) == (MANUAL, MANUAL, Decimal("99.50"))
    assert choice.reason == "ручная цена до конца дня: котировка брокера"

    # Следующий день — снова по правилу.
    next_day = datetime(2026, 10, 28, 10, 0, tzinfo=MOSCOW)
    later = choose(VWAP_RULE, vwap, manual=manual, at=next_day, trading_day=date(2026, 10, 28))
    assert later is not None
    assert later.type == "vwap"
    # До ввода ручная цена ещё не действует.
    early = seen(PriceType.VWAP, "99.65", datetime(2026, 10, 27, 8, 30, tzinfo=MOSCOW))
    before = choose(VWAP_RULE, early, manual=manual, at=datetime(2026, 10, 27, 9, 0, tzinfo=MOSCOW))
    assert before is not None
    assert before.type == "vwap"


def test_no_price_at_all() -> None:
    assert choose(VWAP_RULE) is None
    # Цена позже момента оценки ещё неизвестна.
    future = seen(PriceType.VWAP, "99.70", datetime(2026, 10, 27, 12, 0, tzinfo=MOSCOW))
    assert choose(VWAP_RULE, future) is None


def test_latest_prefers_later_time_then_the_script() -> None:
    at = datetime(2026, 10, 27, 11, 0, tzinfo=MOSCOW)
    script = seen(PriceType.VWAP, "51.82", at)
    iss = seen(PriceType.VWAP, "51.80", at, ISS)
    later_iss = seen(PriceType.VWAP, "51.85", datetime(2026, 10, 27, 23, 59, tzinfo=MOSCOW), ISS)

    assert latest([iss, script]) == script
    assert latest([script, later_iss]) == later_iss
    assert latest([]) is None


def test_spread_in_basis_points_of_the_valuation_price() -> None:
    assert spread_bp([Decimal("99.61"), Decimal("99.78"), Decimal("99.70")], Decimal("99.70")) == (
        Decimal("17.1")
    )
    assert spread_bp([Decimal("99.70")], Decimal("99.70")) is None


@pytest.mark.parametrize(
    ("rule", "code"),
    [
        (Rule(types=()), "no_price_types"),
        (Rule(types=(PriceType.VWAP, PriceType.VWAP)), "duplicate_price_type"),
        (Rule(types=(PriceType.DEMA,)), "dema_settings_required"),
        (Rule(dema_timeframe=10), "dema_settings_required"),
        (Rule(dema_timeframe=241, dema_half_life=Decimal(60)), "bad_timeframe"),
        (Rule(dema_timeframe=10, dema_half_life=Decimal(0)), "bad_half_life"),
        (Rule(spread_threshold_bp=-1), "bad_threshold"),
    ],
)
def test_bad_rules_are_refused(rule: Rule, code: str) -> None:
    with pytest.raises(RuleViolationError) as caught:
        rule.check()
    assert caught.value.code == code


def test_good_rules_pass() -> None:
    VWAP_RULE.check()
    VWAP_THEN_DEMA.check()
