"""Дневной ролл на сквозном примере методики (docs/design/05-booking.md) и правилах лотов."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from am_common.clock import MOSCOW
from booking.roll import (
    Holding,
    InstrumentKind,
    Lot,
    MovementKind,
    Payment,
    Side,
    State,
    Terms,
    Trade,
    fifo,
    from_json,
    roll,
    roll_days,
    start,
    to_json,
    trade_amount,
)
from booking.terms import InstrumentHistory, TermsBook

FIXTURES = Path(__file__).parent / "fixtures" / "refdata"
OFZ = UUID(int=26238)
FUND = UUID(int=1)
BUY_ID, SELL_ID = UUID(int=101), UUID(int=102)


def ofz_terms() -> TermsBook:
    """ОФЗ 26238: купон 35,40 ₽ за 03.06–02.12.2026, фиксация 01.12."""
    params = json.loads((FIXTURES / "SU26238RMFS4.json").read_text(encoding="utf-8"))
    history = InstrumentHistory(OFZ, "bond", "SU26238RMFS4", "ОФЗ 26238", [(None, params)])
    return TermsBook({OFZ: history}, {})


def bond(
    side: str,
    quantity: int,
    price: str,
    accrued: str,
    dates: tuple[date, date],
    *,
    trade_id: UUID | None = None,
    hour: int = 11,
    fee: str = "0",
) -> Trade:
    traded, settled = dates
    amount = trade_amount(
        InstrumentKind.BOND, quantity, Decimal(price), Decimal(1000), Decimal(accrued)
    )
    return Trade(
        id=trade_id or UUID(int=quantity + hour),
        instrument_id=OFZ,
        kind=InstrumentKind.BOND,
        side=Side(side),
        quantity=quantity,
        price=Decimal(price),
        face_value=Decimal(1000),
        accrued=Decimal(accrued),
        amount=amount,
        fee=Decimal(fee),
        trade_at=datetime(traded.year, traded.month, traded.day, hour, tzinfo=MOSCOW),
        trade_date=traded,
        settle_date=settled,
    )


BUY = bond(
    "buy", 50_000, "52.50", "35.01", (date(2026, 11, 27), date(2026, 11, 30)), trade_id=BUY_ID
)
SELL = bond(
    "sell", 20_000, "52.60", "35.21", (date(2026, 11, 30), date(2026, 12, 1)), trade_id=SELL_ID
)
# Перевод 60 млн ₽ из казначейского портфеля — часть 5.2; здесь он — деньги на начало.
OPENING = State(day=date(2026, 11, 26), cash=Decimal(60_000_000))


def table(states: list[State]) -> list[tuple[str, int, int, str]]:
    """День, экономическая позиция, расчётная позиция, деньги."""
    rows = []
    for s in states:
        held = s.holdings.get(OFZ)
        rows.append(
            (
                f"{s.day:%d.%m}",
                0 if held is None else held.trade_quantity,
                0 if held is None else held.settle_quantity,
                f"{s.cash:.2f}",
            )
        )
    return rows


def test_amounts_from_the_methodology() -> None:
    # НКД на дату расчётов: 35,40 / 182 × 180 = 35,01 ₽ (на 30.11), × 181 = 35,21 ₽ (на 01.12).
    assert BUY.amount == Decimal("28000500.00")
    assert SELL.amount == Decimal("11224200.00")
    assert trade_amount(InstrumentKind.FUND_SHARE, 3, Decimal("1.234567"), None, None) == Decimal(
        "3.70"
    )
    with pytest.raises(ValueError, match="face value"):
        trade_amount(InstrumentKind.BOND, 1, Decimal(100), None, None)


def test_positions_on_two_dates_and_the_coupon() -> None:
    states = roll_days(OPENING, date(2026, 12, 3), [BUY, SELL], ofz_terms())

    assert table(states) == [
        ("27.11", 50_000, 0, "60000000.00"),
        ("28.11", 50_000, 0, "60000000.00"),
        ("29.11", 50_000, 0, "60000000.00"),
        ("30.11", 30_000, 50_000, "31999500.00"),
        ("01.12", 30_000, 30_000, "43223700.00"),
        ("02.12", 30_000, 30_000, "44285700.00"),
        ("03.12", 30_000, 30_000, "44285700.00"),
    ]
    by_day = {s.day: s for s in states}
    # Купон: бумаги на счёте на конец даты фиксации 01.12 — 30 000 × 35,40.
    assert [e.quantity for e in by_day[date(2026, 12, 1)].pending] == [30_000]
    [coupon] = by_day[date(2026, 12, 2)].movements
    assert (coupon.kind, coupon.cash) == (MovementKind.COUPON, Decimal("1062000.00"))
    assert by_day[date(2026, 12, 2)].pending == ()


def test_lots_after_the_sale() -> None:
    states = roll_days(OPENING, date(2026, 11, 30), [BUY, SELL], ofz_terms())
    [lot] = states[-1].holdings[OFZ].lots

    assert (lot.trade_id, lot.quantity) == (BUY_ID, 30_000)
    assert lot.cost == Decimal("15750000.00")
    assert lot.accrued_paid == Decimal("1050300.00")


def test_recalculation_after_the_sale_is_corrected() -> None:
    # 03.12 выяснилось: продали 25 000. Пересчёт с 30.11 по состоянию на 29.11.
    states = roll_days(OPENING, date(2026, 12, 3), [BUY, SELL], ofz_terms())
    before = next(s for s in states if s.day == date(2026, 11, 29))
    corrected = bond(
        "sell", 25_000, "52.60", "35.21", (date(2026, 11, 30), date(2026, 12, 1)), trade_id=SELL_ID
    )

    redone = roll_days(before, date(2026, 12, 3), [BUY, corrected], ofz_terms())

    assert table(redone) == [
        ("30.11", 25_000, 50_000, "31999500.00"),
        ("01.12", 25_000, 25_000, "46029750.00"),
        ("02.12", 25_000, 25_000, "46914750.00"),
        ("03.12", 25_000, 25_000, "46914750.00"),
    ]


def test_coupon_goes_by_the_account_not_by_the_trade() -> None:
    # Продажа в день фиксации 01.12 с расчётами 02.12: на конец 01.12 бумаги ещё у нас.
    late_sale = bond("sell", 50_000, "52.60", "35.21", (date(2026, 12, 1), date(2026, 12, 2)))
    states = roll_days(OPENING, date(2026, 12, 2), [BUY, late_sale], ofz_terms())
    last = states[-1]

    assert OFZ not in last.holdings  # продано и рассчитано
    assert [m.kind for m in last.movements] == [MovementKind.SETTLEMENT, MovementKind.COUPON]
    assert last.movements[1].cash == Decimal("1770000.00")  # 50 000 × 35,40


def test_fifo_closes_the_earliest_lot_first() -> None:
    # Пример методики: докупили 10 000 по 52,30% 01.12, продали 35 000 03.12.
    more = bond("buy", 10_000, "52.30", "35.21", (date(2026, 12, 1), date(2026, 12, 2)))
    sale = bond("sell", 35_000, "52.70", "0.39", (date(2026, 12, 3), date(2026, 12, 4)))
    states = roll_days(OPENING, date(2026, 12, 3), [BUY, SELL, more, sale], ofz_terms())
    [lot] = states[-1].holdings[OFZ].lots

    assert (lot.trade_id, lot.quantity, lot.price) == (more.id, 5_000, Decimal("52.30"))
    assert lot.cost == Decimal("2615000.00")


def test_short_lot_is_closed_by_the_next_buy() -> None:
    short = bond("sell", 1_000, "52.60", "35.01", (date(2026, 11, 27), date(2026, 11, 30)), hour=10)
    lots = fifo((), short)
    assert [lot.quantity for lot in lots] == [-1_000]
    # Покупка 50 000: сначала закрывает короткий лот, остаток — новый лот.
    after = fifo(lots, BUY)
    assert [(lot.trade_id, lot.quantity) for lot in after] == [(BUY_ID, 49_000)]
    # Покупка меньше короткого лота уменьшает его.
    small = bond("buy", 400, "52.50", "35.01", (date(2026, 11, 27), date(2026, 11, 30)))
    assert [lot.quantity for lot in fifo(lots, small)] == [-600]


def test_fee_is_paid_on_settlement() -> None:
    with_fee = replace(BUY, fee=Decimal("1400.00"))
    states = roll_days(OPENING, date(2026, 11, 30), [with_fee], ofz_terms())

    assert states[-1].cash == Decimal("31998100.00")
    assert [m.kind for m in states[-1].movements] == [MovementKind.SETTLEMENT, MovementKind.FEE]


ISSUE = date(2026, 1, 15)


def terms(*payments: Payment) -> Any:
    return lambda _instrument, _day: Terms(payments)


def payment(day: date, **fields: Any) -> Payment:
    values: dict[str, Any] = {
        "pay_date": day,
        "record_date": day.replace(day=day.day - 1),
        "has_coupon": True,
        "coupon": Decimal(10),
        "principal": Decimal(0),
        "outstanding": None,
        "final": False,
    }
    return Payment(**(values | fields))


def held(quantity: int) -> State:
    lot = Lot(
        BUY_ID,
        datetime(2026, 1, 15, 11, tzinfo=MOSCOW),
        quantity,
        Decimal(100),
        Decimal(1000),
        Decimal(0),
    )
    return State(
        day=date(2026, 3, 1),
        holdings={
            OFZ: replace(
                Holding(OFZ, InstrumentKind.BOND),
                trade_quantity=quantity,
                settle_quantity=quantity,
                lots=(lot,),
            )
        },
    )


def test_amortization_pays_principal_and_lowers_lot_face() -> None:
    plan = terms(
        payment(date(2026, 3, 4), principal=Decimal(250), outstanding=Decimal(750)),
    )
    states = roll_days(held(100), date(2026, 3, 4), [], plan)
    last = states[-1]

    assert [(m.kind, m.cash) for m in last.movements] == [
        (MovementKind.COUPON, Decimal("1000.00")),
        (MovementKind.AMORTIZATION, Decimal("25000.00")),
    ]
    assert last.holdings[OFZ].lots[0].face == Decimal(750)
    assert last.holdings[OFZ].settle_quantity == 100  # количество не меняется


def test_redemption_closes_the_position() -> None:
    plan = terms(payment(date(2026, 3, 4), principal=Decimal(1000), final=True))
    last = roll_days(held(100), date(2026, 3, 4), [], plan)[-1]

    assert OFZ not in last.holdings
    assert [(m.kind, m.quantity, m.cash) for m in last.movements] == [
        (MovementKind.COUPON, 0, Decimal("1000.00")),
        (MovementKind.REDEMPTION, -100, Decimal("100000.00")),
    ]


def test_unknown_coupon_waits_until_announced() -> None:
    unknown = payment(date(2026, 3, 4), coupon=None)
    waited = roll_days(held(100), date(2026, 3, 5), [], terms(unknown))

    assert [m.kind for m in waited[-2].movements] == [MovementKind.PAYMENT_UNKNOWN]
    assert waited[-1].cash == 0
    assert len(waited[-1].pending) == 1
    # Купон объявили — деньги приходят в первый день, когда сумма известна.
    paid = roll(waited[-1], [], terms(payment(date(2026, 3, 4))))
    assert paid.cash == Decimal("1000.00")
    assert paid.pending == ()


def test_payment_removed_from_schedule_drops_the_right() -> None:
    record_only = roll_days(held(100), date(2026, 3, 3), [], terms(payment(date(2026, 3, 4))))
    assert len(record_only[-1].pending) == 1

    nothing = roll(record_only[-1], [], terms())
    assert (nothing.pending, nothing.cash) == ((), Decimal(0))


def test_fund_share_lot_and_json_round_trip() -> None:
    share = Trade(
        id=UUID(int=7),
        instrument_id=FUND,
        kind=InstrumentKind.FUND_SHARE,
        side=Side.BUY,
        quantity=1_000,
        price=Decimal("1.5"),
        face_value=None,
        accrued=None,
        amount=Decimal("1500.00"),
        fee=Decimal(0),
        trade_at=datetime(2026, 11, 27, 12, tzinfo=MOSCOW),
        trade_date=date(2026, 11, 27),
        settle_date=date(2026, 11, 27),
    )
    states = roll_days(
        start(date(2026, 11, 27)), date(2026, 12, 1), [share, BUY, SELL], ofz_terms()
    )

    assert states[0].holdings[FUND].lots[0].cost == Decimal("1500.0")
    for state in states:
        assert from_json(json.loads(json.dumps(to_json(state)))) == state


def test_amortization_on_a_day_without_a_coupon() -> None:
    plan = terms(
        payment(
            date(2026, 3, 4),
            has_coupon=False,
            coupon=None,
            principal=Decimal(500),
            outstanding=Decimal(500),
        )
    )
    last = roll_days(held(10), date(2026, 3, 4), [], plan)[-1]

    assert [(m.kind, m.cash) for m in last.movements] == [
        (MovementKind.AMORTIZATION, Decimal("5000.00"))
    ]
