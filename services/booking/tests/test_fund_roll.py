"""Ролл фонда: паи, расходы, переводы денег и бумаг, прогноз для лестницы (design/05)."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from am_common.clock import MOSCOW
from booking.ladder import project
from booking.roll import (
    Holding,
    InstrumentKind,
    Lot,
    MovementKind,
    Operation,
    OperationKind,
    Payment,
    Side,
    State,
    Terms,
    Trade,
    roll_fund_days,
    take,
    trade_amount,
)
from booking.terms import InstrumentHistory, TermsBook

FIXTURES = Path(__file__).parent / "fixtures" / "refdata"
OFZ = UUID(int=26238)
TREASURY, BONDS = UUID(int=11), UUID(int=12)
BUY_ID, SELL_ID = UUID(int=101), UUID(int=102)


def ofz_terms() -> TermsBook:
    """ОФЗ 26238: купон 35,40 ₽ за 03.06–02.12.2026, фиксация 01.12."""
    params = json.loads((FIXTURES / "SU26238RMFS4.json").read_text(encoding="utf-8"))
    history = InstrumentHistory(OFZ, "bond", "SU26238RMFS4", "ОФЗ 26238", [(None, params)])
    return TermsBook({OFZ: history}, {})


def bond(
    trade_id: UUID, side: str, quantity: int, price: str, accrued: str, *, dates: tuple[date, date]
) -> Trade:
    traded, settled = dates
    return Trade(
        id=trade_id,
        instrument_id=OFZ,
        kind=InstrumentKind.BOND,
        side=Side(side),
        quantity=quantity,
        price=Decimal(price),
        face_value=Decimal(1000),
        accrued=Decimal(accrued),
        amount=trade_amount(
            InstrumentKind.BOND, quantity, Decimal(price), Decimal(1000), Decimal(accrued)
        ),
        fee=Decimal(0),
        trade_at=datetime(traded.year, traded.month, traded.day, 11, tzinfo=MOSCOW),
        trade_date=traded,
        settle_date=settled,
    )


BUY = bond(BUY_ID, "buy", 50_000, "52.50", "35.01", dates=(date(2026, 11, 27), date(2026, 11, 30)))
SELL = bond(
    SELL_ID, "sell", 20_000, "52.60", "35.21", dates=(date(2026, 11, 30), date(2026, 12, 1))
)
TRADES = {BONDS: [BUY, SELL]}


def operation(number: int, kind: OperationKind, day: date, **fields: Any) -> Operation:
    portfolio = fields.pop("portfolio_id", TREASURY)
    return Operation(
        id=UUID(int=200 + number), kind=kind, portfolio_id=portfolio, day=day, **fields
    )


ISSUE = operation(
    1,
    OperationKind.UNIT_ISSUE,
    date(2026, 11, 26),
    units=Decimal(100_000),
    amount=Decimal(100_000_000),
    planned=date(2026, 11, 26),
    settled=date(2026, 11, 26),
)
TO_BONDS = operation(
    2,
    OperationKind.CASH_TRANSFER,
    date(2026, 11, 26),
    amount=Decimal(60_000_000),
    receiver_id=BONDS,
)


def cash(days: list[dict[UUID, State]], portfolio: UUID) -> list[str]:
    return [f"{d[portfolio].day:%d.%m} {d[portfolio].cash:.2f}" for d in days]


def test_example_from_the_methodology() -> None:
    days = roll_fund_days(
        {},
        date(2026, 11, 26),
        date(2026, 12, 2),
        trades=TRADES,
        operations=[ISSUE, TO_BONDS],
        terms_on=ofz_terms(),
    )

    treasury = days[0][TREASURY]
    assert (treasury.cash, treasury.units, treasury.claims) == (
        Decimal(40_000_000),
        Decimal(100_000),
        Decimal(0),
    )
    assert [m.kind for m in treasury.movements] == [
        MovementKind.UNIT_ISSUE,
        MovementKind.UNIT_SETTLEMENT,
        MovementKind.TRANSFER_OUT,
    ]
    assert cash(days, BONDS)[4:] == [
        "30.11 31999500.00",
        "01.12 43223700.00",
        "02.12 44285700.00",
    ]
    assert days[0][BONDS].movements[0].kind is MovementKind.TRANSFER_IN


def test_unit_settlement_waits_for_the_mark() -> None:
    unmarked = operation(
        3,
        OperationKind.UNIT_ISSUE,
        date(2026, 11, 26),
        units=Decimal("10.12345"),
        amount=Decimal(1000),
        planned=date(2026, 11, 27),
    )
    redemption = operation(
        4,
        OperationKind.UNIT_REDEMPTION,
        date(2026, 11, 26),
        units=Decimal(1),
        amount=Decimal(100),
        planned=date(2026, 11, 27),
        settled=date(2026, 11, 30),
    )

    def run(*, projected: bool) -> list[dict[UUID, State]]:
        return roll_fund_days(
            {},
            date(2026, 11, 26),
            date(2026, 11, 30),
            trades={},
            operations=[unmarked, redemption],
            terms_on=ofz_terms(),
            projected=projected,
        )

    actual = run(projected=False)
    assert (actual[0][TREASURY].units, actual[0][TREASURY].obligations) == (
        Decimal("9.12345"),
        Decimal(100),
    )
    # Без отметки расчёта требование висит, денег нет; погашение рассчитано 30.11.
    last = actual[-1][TREASURY]
    assert (last.cash, last.claims, last.obligations) == (Decimal(-100), Decimal(1000), Decimal(0))
    # В прогнозе неотмеченный расчёт проходит в плановую дату.
    projected = run(projected=True)
    assert (projected[1][TREASURY].cash, projected[1][TREASURY].claims) == (
        Decimal(1000),
        Decimal(0),
    )


def test_expenses_accrue_and_get_paid() -> None:
    accrual = operation(
        5, OperationKind.EXPENSE_ACCRUAL, date(2026, 11, 26), amount=Decimal(50_000)
    )
    paid = operation(6, OperationKind.EXPENSE_PAYMENT, date(2026, 11, 27), amount=Decimal(30_000))
    days = roll_fund_days(
        {},
        date(2026, 11, 26),
        date(2026, 11, 27),
        trades={},
        operations=[accrual, paid],
        terms_on=ofz_terms(),
    )

    assert (days[0][TREASURY].expenses, days[0][TREASURY].cash) == (Decimal(50_000), Decimal(0))
    assert (days[1][TREASURY].expenses, days[1][TREASURY].cash) == (
        Decimal(20_000),
        Decimal(-30_000),
    )
    assert [m.cash for m in days[1][TREASURY].movements] == [Decimal(-30_000)]


def test_security_transfer_moves_sender_lots_and_the_coupon_stays() -> None:
    # 02.12 — после даты фиксации 01.12: купон получит отправитель.
    transfer = operation(
        7,
        OperationKind.SECURITY_TRANSFER,
        date(2026, 12, 2),
        portfolio_id=BONDS,
        receiver_id=TREASURY,
        instrument_id=OFZ,
        instrument_kind=InstrumentKind.BOND,
        quantity=10_000,
    )
    days = roll_fund_days(
        {},
        date(2026, 11, 26),
        date(2026, 12, 2),
        trades=TRADES,
        operations=[ISSUE, TO_BONDS, transfer],
        terms_on=ofz_terms(),
    )
    bonds, treasury = days[-1][BONDS], days[-1][TREASURY]

    assert (bonds.holdings[OFZ].settle_quantity, bonds.holdings[OFZ].trade_quantity) == (
        20_000,
        20_000,
    )
    assert bonds.cash == Decimal("44285700.00")
    [lot] = treasury.holdings[OFZ].lots
    # Лот отправителя — с его ценой, датой и НКД: финансового результата нет (решение 98).
    assert (lot.trade_id, lot.quantity, lot.price, lot.accrued) == (
        BUY_ID,
        10_000,
        Decimal("52.50"),
        Decimal("35.01"),
    )
    assert treasury.cash == Decimal(40_000_000)
    assert [(m.kind, m.quantity) for m in treasury.movements] == [
        (MovementKind.TRANSFER_IN, 10_000)
    ]


def test_received_lots_join_in_opening_order() -> None:
    early, late = (
        Lot(UUID(int=n), datetime(2026, 11, d, 11, tzinfo=MOSCOW), 10, Decimal(1), None, Decimal(0))
        for n, d in ((1, 2), (2, 20))
    )
    taken, left = take((early, late), 15)
    assert ([lot.quantity for lot in taken], [lot.quantity for lot in left]) == ([10, 5], [5])
    # Больше, чем есть, и короткий лот не берутся.
    short = Lot(UUID(int=3), early.opened_at, -5, Decimal(1), None, Decimal(0))
    assert take((early, short), 30) == ((early,), (short,))


def terms(*payments: Payment) -> Any:
    return lambda _instrument, _day: Terms(payments)


def test_ladder_from_monday_and_the_deficit() -> None:
    before = roll_fund_days(
        {},
        date(2026, 11, 26),
        date(2026, 11, 29),
        trades=TRADES,
        operations=[ISSUE, TO_BONDS],
        terms_on=ofz_terms(),
    )[-1]
    oversold = bond(
        SELL_ID, "sell", 60_000, "52.60", "35.21", dates=(date(2026, 11, 30), date(2026, 12, 1))
    )

    def ladder(trades: list[Trade]) -> list[Any]:
        return project(
            before,
            date(2026, 11, 30),
            date(2026, 11, 30),
            account={BONDS},
            trades={BONDS: trades},
            operations=[ISSUE, TO_BONDS],
            terms_on=ofz_terms(),
            exceptions={},
        )

    rows = ladder([BUY, SELL])
    assert [(f"{r.day:%d.%m}", f"{r.cash:.2f}", r.securities[OFZ]) for r in rows[:3]] == [
        ("30.11", "31999500.00", 50_000),
        ("01.12", "43223700.00", 30_000),
        ("02.12", "44285700.00", 30_000),
    ]
    # Не меньше 10 торговых дней: по 14.12, без выходных.
    assert (rows[-1].day, len(rows)) == (date(2026, 12, 14), 11)
    assert [m.kind for _, m in rows[0].movements] == [MovementKind.SETTLEMENT]
    assert rows[0].short == []

    short = ladder([BUY, oversold])
    assert (short[1].securities[OFZ], short[1].short) == (-10_000, [OFZ])


def test_ladder_puts_unmarked_units_on_the_planned_date() -> None:
    overdue = operation(
        8,
        OperationKind.UNIT_ISSUE,
        date(2026, 11, 25),
        units=Decimal(10),
        amount=Decimal(1000),
        planned=date(2026, 11, 27),
    )
    late = operation(
        9,
        OperationKind.UNIT_REDEMPTION,
        date(2026, 11, 25),
        units=Decimal(1),
        amount=Decimal(100),
        planned=date(2026, 12, 30),
    )
    rows = project(
        {},
        date(2026, 11, 25),
        date(2026, 11, 30),
        account={TREASURY},
        trades={},
        operations=[overdue, late],
        terms_on=terms(),
        exceptions={date(2026, 12, 7): False},
    )

    # Просроченная плановая дата — сегодня; горизонт — до последнего известного расчёта.
    assert (rows[0].day, rows[0].cash) == (date(2026, 11, 30), Decimal(1000))
    assert (rows[-1].day, rows[-1].cash) == (date(2026, 12, 30), Decimal(900))
    assert date(2026, 12, 7) not in [r.day for r in rows]


def test_ladder_shows_an_unknown_coupon_once() -> None:
    lot = Lot(BUY_ID, datetime(2026, 11, 2, 11, tzinfo=MOSCOW), 10, Decimal(100), None, Decimal(0))
    held = State(
        day=date(2026, 11, 29), holdings={OFZ: Holding(OFZ, InstrumentKind.BOND, 10, 10, (lot,))}
    )
    unknown = Payment(
        pay_date=date(2026, 12, 5),  # суббота
        record_date=date(2026, 12, 4),
        has_coupon=True,
        coupon=None,
        principal=Decimal(0),
        outstanding=None,
        final=False,
    )
    rows = project(
        {BONDS: held},
        date(2026, 11, 30),
        date(2026, 11, 30),
        account={BONDS},
        trades={},
        operations=[],
        terms_on=terms(unknown),
        exceptions={},
    )

    shown = [(r.day, r.trading) for r in rows if r.movements]
    assert shown == [(date(2026, 12, 5), False)]
    assert rows[0].securities == {OFZ: 10}
