"""Оценка и результат дня в ролле: сквозной пример методики шага 6 (design/06) и правила."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from am_common.clock import MOSCOW
from booking.roll import (
    Contribution,
    Entitlement,
    FeeRate,
    Flag,
    Holding,
    InstrumentKind,
    Lot,
    Mark,
    Operation,
    OperationKind,
    Payment,
    Repo,
    Side,
    State,
    Terms,
    Trade,
    from_json,
    remark,
    roll,
    roll_fund_days,
    to_json,
    trade_amount,
)
from booking.terms import InstrumentHistory, TermsBook

FIXTURES = Path(__file__).parent / "fixtures" / "refdata"
OFZ = UUID(int=26238)
SHARE = UUID(int=777)
TREASURY, BONDS = UUID(int=11), UUID(int=12)
BUY_ID, SELL_ID = UUID(int=101), UUID(int=102)

# Цены для оценки на конец дня (design/06, сквозной пример); выходные — цена пятницы.
PRICES = {
    date(2026, 11, 26): None,
    date(2026, 11, 27): "52.55",
    date(2026, 11, 28): "52.55",
    date(2026, 11, 29): "52.55",
    date(2026, 11, 30): "52.62",
    date(2026, 12, 1): "52.58",
    date(2026, 12, 2): "52.70",
    date(2026, 12, 3): "52.66",
}
# Расчёты T+1 и НКД на них: в субботу и воскресенье — понедельник.
SETTLE = {
    date(2026, 11, 26): (date(2026, 11, 27), "34.43"),
    date(2026, 11, 27): (date(2026, 11, 30), "35.01"),
    date(2026, 11, 28): (date(2026, 11, 30), "35.01"),
    date(2026, 11, 29): (date(2026, 11, 30), "35.01"),
    date(2026, 11, 30): (date(2026, 12, 1), "35.21"),
    date(2026, 12, 1): (date(2026, 12, 2), "0.00"),
    date(2026, 12, 2): (date(2026, 12, 3), "0.19"),
    date(2026, 12, 3): (date(2026, 12, 4), "0.39"),
}


def marks(_instrument: UUID, day: date) -> Mark:
    price = PRICES[day]
    settle, accrued = SETTLE[day]
    return Mark(
        price=None if price is None else Decimal(price),
        settle=settle,
        accrued=Decimal(accrued),
        face=Decimal(1000),
    )


def ofz_terms() -> TermsBook:
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
ISSUE = Operation(
    id=UUID(int=201),
    kind=OperationKind.UNIT_ISSUE,
    portfolio_id=TREASURY,
    day=date(2026, 11, 26),
    units=Decimal(100_000),
    amount=Decimal(100_000_000),
    planned=date(2026, 11, 26),
    settled=date(2026, 11, 26),
)
TO_BONDS = Operation(
    id=UUID(int=202),
    kind=OperationKind.CASH_TRANSFER,
    portfolio_id=TREASURY,
    day=date(2026, 11, 26),
    amount=Decimal(60_000_000),
    receiver_id=BONDS,
)


def week(**changes: Any) -> list[dict[UUID, State]]:
    arguments: dict[str, Any] = {
        "trades": {BONDS: [BUY, SELL]},
        "operations": [ISSUE, TO_BONDS],
        "terms_on": ofz_terms(),
        "marks_on": marks,
    } | changes
    return roll_fund_days({}, date(2026, 11, 26), date(2026, 12, 3), **arguments)


def balanced(days: list[dict[UUID, State]]) -> None:
    """Части результата каждый день сходятся с изменением стоимости за вычетом притоков."""
    for portfolio in days[0]:
        value = Decimal(0)
        for states in days:
            result = states[portfolio].result
            assert result is not None
            assert result.value - value - result.flows == result.total, (portfolio, states)
            value = result.value


def test_week_from_the_methodology() -> None:
    days = week()
    balanced(days)

    def fund(day: dict[UUID, State]) -> tuple[Decimal, ...]:
        parts = [s.result for s in day.values() if s.result is not None]
        return tuple(
            sum((getattr(r, name) for r in parts), Decimal(0))
            for name in ("revaluation", "realized", "coupon", "total")
        )

    # Без вознаграждения УК (часть 6.2) — части результата из таблицы методики.
    expected = [
        (0, 0, 0, 0),
        (25_000, 0, 0, 25_000),
        (0, 0, 0, 0),
        (0, 0, 0, 0),
        (11_000, 20_000, 10_000, 41_000),
        (-12_000, 0, 5_700, -6_300),
        (36_000, 0, 5_700, 41_700),
        (-12_000, 0, 6_000, -6_000),
    ]
    assert [fund(d) for d in days] == [tuple(Decimal(v) for v in row) for row in expected]
    friday = days[1][BONDS]
    assert friday.result is not None
    [ofz] = friday.valuations.values()
    # Покупка по рынку: НКД в оценке — на понедельник, как заплатили продавцу.
    assert (ofz.clean, ofz.accrued_value, ofz.coupon, friday.result.payable) == (
        Decimal("26275000.00"),
        Decimal("1750500.00"),
        Decimal("0.00"),
        Decimal("28000500.00"),
    )
    assert friday.result.value == Decimal("60025000.00")
    monday = days[4][BONDS]
    assert monday.result is not None
    assert (monday.result.receivable, monday.valuations[OFZ].unrealized) == (
        Decimal("11224200.00"),
        Decimal("36000.000000"),
    )
    # Дата фиксации: купон — к получению, НКД на 02.12 уже без него.
    record = days[5][BONDS].valuations[OFZ]
    assert (record.coupon_due, record.accrued_value) == (Decimal("1062000.00"), Decimal("0.00"))
    total = sum((s.result.value for s in days[-1].values() if s.result is not None), Decimal(0))
    assert total == Decimal("100095400.00")
    # Казначейский портфель: приток — паи; перевод — отток к портфелю облигаций.
    first = days[0][TREASURY].result
    assert first is not None
    assert (first.flows, first.value) == (Decimal("40000000"), Decimal("40000000"))


def test_record_date_earlier_than_the_day_before_payment() -> None:
    # Дата фиксации за три дня до выплаты: НКД на T+1 ещё содержит купон — вычитаем.
    params = json.loads((FIXTURES / "SU26238RMFS4.json").read_text(encoding="utf-8"))
    history = InstrumentHistory(OFZ, "bond", "SU26238RMFS4", "ОФЗ 26238", [(None, params)])
    book = TermsBook({OFZ: history}, {})

    def early(instrument: UUID, day: date) -> Terms:
        terms = book(instrument, day)
        return Terms(
            tuple(
                replace(p, record_date=date(2026, 11, 30)) if p.pay_date == date(2026, 12, 2) else p
                for p in terms.payments
            )
        )

    days = week(terms_on=early)
    balanced(days)
    # Фиксация в понедельник 30.11: купон получим за 50 000 бумаг на счёте, включая
    # проданные 20 000 — они уйдут только 01.12. НКД на вторник ещё с купоном — вычитаем.
    monday = days[4][BONDS].valuations[OFZ]
    assert (monday.accrued, monday.coupon_due) == (Decimal("-0.19"), Decimal("1770000.00"))


def test_fifo_realized_with_two_lots() -> None:
    # Методика: докупили 10 000 по 52,30% 01.12, продали 35 000 03.12 по 52,66%.
    more = bond(
        UUID(int=103), "buy", 10_000, "52.30", "0", dates=(date(2026, 12, 1), date(2026, 12, 2))
    )
    sale = bond(
        UUID(int=104), "sell", 35_000, "52.66", "0.39", dates=(date(2026, 12, 3), date(2026, 12, 4))
    )
    days = week(trades={BONDS: [BUY, SELL, more, sale]})
    balanced(days)
    # FIFO закрыл 30 000 по 52,50% и 5 000 по 52,30%: стоимость 18 365 000 ₽.
    last = days[-1][BONDS].valuations[OFZ]
    assert last.realized == Decimal(35_000) * Decimal("526.6") - Decimal(18_365_000)
    assert (last.quantity, last.cost) == (5_000, Decimal("2615000.0000"))


def test_fee_is_an_expense_on_the_trade_date() -> None:
    days = week(trades={BONDS: [replace(BUY, fee=Decimal("1400.50")), SELL]})
    balanced(days)
    friday = days[1][BONDS].result
    assert friday is not None
    assert (friday.fees, friday.payable) == (Decimal("-1400.50"), Decimal("28001900.50"))
    # В дату расчётов комиссия уходит деньгами — результат не меняется.
    monday = days[4][BONDS].result
    assert monday is not None
    assert (monday.fees, monday.payable) == (Decimal(0), Decimal(0))


def test_security_transfer_is_a_flow_not_a_result() -> None:
    moved = Operation(
        id=UUID(int=203),
        kind=OperationKind.SECURITY_TRANSFER,
        portfolio_id=BONDS,
        day=date(2026, 12, 2),
        receiver_id=TREASURY,
        instrument_id=OFZ,
        instrument_kind=InstrumentKind.BOND,
        quantity=10_000,
    )
    days = week(operations=[ISSUE, TO_BONDS, moved])
    balanced(days)
    sender, receiver = days[6][BONDS].result, days[6][TREASURY].result
    assert sender is not None
    assert receiver is not None
    # 10 000 × (52,70% × 1000 / 100 + 0,19) — приток получателю и отток отправителю.
    assert (receiver.flows, sender.flows) == (Decimal("5271900.00"), Decimal("-5271900.00"))
    assert receiver.total == Decimal("0.00")
    assert days[6][TREASURY].valuations[OFZ].unrealized == Decimal("20000.000000")


def test_expenses_and_units() -> None:
    accrual = Operation(
        id=UUID(int=204),
        kind=OperationKind.EXPENSE_ACCRUAL,
        portfolio_id=TREASURY,
        day=date(2026, 11, 27),
        amount=Decimal(50_000),
    )
    paid = replace(
        accrual, id=UUID(int=205), kind=OperationKind.EXPENSE_PAYMENT, day=date(2026, 11, 30)
    )
    redemption = replace(
        ISSUE,
        id=UUID(int=206),
        kind=OperationKind.UNIT_REDEMPTION,
        day=date(2026, 11, 30),
        units=Decimal(10),
        amount=Decimal(10_000),
        settled=None,
    )
    days = week(operations=[ISSUE, TO_BONDS, accrual, paid, redemption])
    balanced(days)
    friday, monday = days[1][TREASURY].result, days[4][TREASURY].result
    assert friday is not None
    assert monday is not None
    assert friday.expenses == Decimal(-50_000)
    # Выплата расхода и погашение паёв — не результат: отток и деньги против обязательства.
    assert (monday.expenses, monday.flows, monday.total) == (
        Decimal(0),
        Decimal(-10_000),
        Decimal(0),
    )


def share_trade(quantity: int) -> Trade:
    return Trade(
        id=UUID(int=301),
        instrument_id=SHARE,
        kind=InstrumentKind.FUND_SHARE,
        side=Side.BUY,
        quantity=quantity,
        price=Decimal("1.5"),
        face_value=None,
        accrued=None,
        amount=Decimal("1.5") * quantity,
        fee=Decimal(0),
        trade_at=datetime(2026, 11, 27, 12, tzinfo=MOSCOW),
        trade_date=date(2026, 11, 27),
        settle_date=date(2026, 11, 27),
    )


@pytest.mark.parametrize(
    ("mark", "flags", "clean"),
    [
        (Mark(price=None), (Flag.NO_PRICE,), "1500.00"),  # по цене покупки (решение 103)
        (Mark(price=Decimal("1.6"), stale=True), (Flag.STALE_PRICE,), "1600.00"),
    ],
)
def test_share_without_price_or_with_a_stale_one(
    mark: Mark, flags: tuple[Flag, ...], clean: str
) -> None:
    state = roll(
        State(day=date(2026, 11, 26)),
        [share_trade(1000)],
        lambda _i, _d: Terms(),
        lambda _i, _d: mark,
    )
    valuation = state.valuations[SHARE]
    assert (valuation.flags, valuation.clean, valuation.accrued_value) == (
        flags,
        Decimal(clean),
        Decimal(0),
    )
    assert state.result is not None
    assert state.result.flags == flags


def test_bond_without_accrued_or_face() -> None:
    held = State(
        day=date(2026, 3, 1),
        holdings={
            OFZ: Holding(
                OFZ,
                InstrumentKind.BOND,
                100,
                100,
                (
                    Lot(
                        BUY_ID,
                        datetime(2026, 1, 15, tzinfo=MOSCOW),
                        100,
                        Decimal(98),
                        Decimal(1000),
                        Decimal(0),
                    ),
                ),
            )
        },
    )
    state = roll(held, [], lambda _i, _d: Terms(), lambda _i, _d: Mark(price=Decimal(99)))
    valuation = state.valuations[OFZ]
    # Номинал — из лотов, НКД — 0: оценка помечена неполной.
    assert (valuation.flags, valuation.clean) == (
        (Flag.NO_FACE, Flag.NO_ACCRUED),
        Decimal("99000.00"),
    )


def payment(**fields: Any) -> Payment:
    values: dict[str, Any] = {
        "pay_date": date(2026, 3, 4),
        "record_date": date(2026, 3, 3),
        "has_coupon": True,
        "coupon": Decimal(10),
        "principal": Decimal(0),
        "outstanding": None,
        "final": False,
    }
    return Payment(**(values | fields))


def held(quantity: int, price: str = "98") -> State:
    lot = Lot(
        BUY_ID,
        datetime(2026, 1, 15, tzinfo=MOSCOW),
        quantity,
        Decimal(price),
        Decimal(1000),
        Decimal(0),
    )
    return State(
        day=date(2026, 3, 1),
        holdings={OFZ: Holding(OFZ, InstrumentKind.BOND, quantity, quantity, (lot,))},
    )


def days_from(start: State, till: date, terms: Terms, face: str = "1000") -> list[State]:
    """Дни до ``till`` по цене 98%, расчёты T+1 — завтра; номинал отметки — для линкера."""
    states, current = [], start
    while current.day < till:
        prices = lambda _i, d: Mark(  # noqa: E731
            price=Decimal(98), settle=d + timedelta(days=1), accrued=Decimal(0), face=Decimal(face)
        )
        current = roll(current, [], lambda _i, _d: terms, prices)
        states.append(current)
    # Части результата сходятся с изменением стоимости каждый день, кроме первого.
    for before, after in pairwise(states):
        assert after.result is not None
        assert before.result is not None
        assert after.result.value - before.result.value == after.result.total
    return states


def parts(state: State) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    v = state.valuations[OFZ]
    return v.clean, v.principal_due, v.revaluation, v.realized


AMORTIZATION = payment(coupon=Decimal(0), principal=Decimal(250), outstanding=Decimal(750))


def test_amortization_after_the_record_date_is_due_at_par() -> None:
    # 100 бумаг по 98%: 02.03 — номинал 1 000; 03.03 — дата фиксации, расчёты T+1 04.03 —
    # в дату выплаты 250 ₽ номинала; 04.03 — выплата.
    plan = Terms((AMORTIZATION,), face=Decimal(1000))
    states = days_from(held(100), date(2026, 3, 4), plan)

    assert [parts(s) for s in states] == [
        (Decimal("98000.00"), Decimal(0), Decimal("98000.00"), Decimal(0)),
        # Номинал на T+1 — 750: бумага стоит 100 × 98% × 750, а 250 ₽ номинала — требование
        # к эмитенту по 100%. Погашенная часть больше не зависит от цены: +100 × 250 × 2%.
        (Decimal("73500.00"), Decimal("25000.00"), Decimal("500.00"), Decimal(0)),
        # Деньги пришли, требование закрылось; реализованный — от цены лота, переоценка — назад.
        (Decimal("73500.00"), Decimal(0), Decimal("-500.00"), Decimal("500.00")),
    ]
    assert states[-1].holdings[OFZ].lots[0].face == Decimal(750)


def test_record_date_two_days_before_the_amortization() -> None:
    # Фиксация 02.03, выплата 04.03: номинал на T+1 03.03 ещё 1 000, но 250 ₽ из него
    # покупатель уже не получит — они в требовании, а не в бумаге.
    plan = Terms((replace(AMORTIZATION, record_date=date(2026, 3, 2)),), face=Decimal(1000))
    states = days_from(held(100), date(2026, 3, 3), plan)
    assert parts(states[-1])[:2] == (Decimal("73500.00"), Decimal("25000.00"))


def test_redemption_after_the_record_date_is_due_at_par() -> None:
    plan = Terms((payment(coupon=Decimal(0), principal=Decimal(1000), final=True),))
    states = days_from(held(10), date(2026, 3, 4), plan)

    assert [parts(s) for s in states[1:]] == [
        # После даты фиксации погашения бумагу не продать с этим погашением: она — 0,
        # весь номинал — к получению.
        (Decimal(0), Decimal("10000.00"), Decimal("200.00"), Decimal(0)),
        (Decimal(0), Decimal(0), Decimal("-200.00"), Decimal("200.00")),
    ]
    redeemed = states[-1].valuations[OFZ]
    assert (redeemed.quantity, redeemed.cost) == (0, Decimal(0))


def test_unknown_amortization_is_flagged() -> None:
    # Сумма погашения не объявлена: номинал по графику не посчитать — номинал отметки.
    plan = Terms((replace(AMORTIZATION, principal=None),), face=Decimal(1000))
    states = days_from(held(10), date(2026, 3, 3), plan, face="990")
    valuation = states[-1].valuations[OFZ]
    assert (valuation.flags, valuation.face) == ((Flag.PRINCIPAL_UNKNOWN,), Decimal(990))
    assert plan.outstanding_on(date(2026, 3, 4)) is None


def test_unknown_coupon_is_flagged() -> None:
    plan = Terms((payment(coupon=None),))
    states = days_from(held(10), date(2026, 3, 3), plan)
    assert states[-1].valuations[OFZ].flags == (Flag.COUPON_UNKNOWN,)


def test_json_round_trip_keeps_the_valuation() -> None:
    days = week()
    for state in days[4].values():
        assert from_json(to_json(state)) == state
    old = to_json(days[4][BONDS])
    # Снапшот до решения 149: без погашений к получению и номинала в оценке.
    for line in old["valuations"]:
        del line["principal_due"], line["face"]
    assert [(v.principal_due, v.face) for v in from_json(old).valuations.values()] == [
        (Decimal(0), None)
    ]
    del old["valuations"], old["result"]
    assert from_json(old).result is None


def test_pending_without_payment_in_terms() -> None:
    # Выплату убрали из графика, а право осталось: купон к получению — 0.
    state = State(
        day=date(2026, 3, 1),
        pending=(Entitlement(OFZ, date(2026, 3, 9), date(2026, 3, 1), 10),),
    )
    after = roll(state, [], lambda _i, _d: Terms(), lambda _i, _d: Mark(price=Decimal(98)))
    assert after.valuations == {}


def test_today_at_the_current_price() -> None:
    today = week()[-1][BONDS]

    def now(price: str | None, *, stale: bool = False) -> Any:
        return lambda _i, _d: Mark(
            price=None if price is None else Decimal(price), stale=stale, face=Decimal(1000)
        )

    higher = remark(today, now("52.70"))
    assert higher.result is not None
    assert today.result is not None
    assert higher.result.value - today.result.value == Decimal("12000.00")
    assert higher.valuations[OFZ].revaluation == Decimal(0)
    # Текущей цены нет — оценка как была; устаревшая — с пометкой.
    assert remark(today, now(None)) == today
    stale = remark(today, now("52.66", stale=True))
    assert stale.valuations[OFZ].flags == (Flag.STALE_PRICE,)
    assert stale.result is not None
    assert stale.result.flags == (Flag.STALE_PRICE,)
    unvalued = State(day=date(2026, 12, 3))
    assert remark(unvalued, now("52.70")) is unvalued
    # Снапшот до решения 149 без номинала в оценке — номинал текущей отметки.
    old = replace(today, valuations={OFZ: replace(today.valuations[OFZ], face=None)})
    assert remark(old, now("52.70")).valuations[OFZ].clean == higher.valuations[OFZ].clean


def test_two_bonds_traded_the_same_day() -> None:
    share = replace(
        share_trade(1000), trade_date=date(2026, 11, 27), settle_date=date(2026, 11, 27)
    )
    days = week(trades={BONDS: [BUY, SELL, share]})
    balanced(days)
    assert set(days[1][BONDS].valuations) == {OFZ, SHARE}


MANAGEMENT = FeeRate("management", Decimal("1.5"), date(2026, 11, 26), TREASURY)


def nav(day: dict[UUID, State]) -> Decimal:
    return sum((s.result.value for s in day.values() if s.result is not None), Decimal(0))


def test_nav_and_management_fee_from_the_methodology() -> None:
    days = week(rates=[MANAGEMENT])
    balanced(days)

    # СЧА и вознаграждение УК 1,5% годовых по дням — таблицы методики (design/06).
    assert [nav(d) for d in days] == [
        Decimal(v)
        for v in (
            "99995890.41",
            "100016779.96",
            "100012669.68",
            "100008559.57",
            "100045447.94",
            "100035036.74",
            "100072624.00",
            "100062511.67",
        )
    ]
    assert [d[TREASURY].fees_today["management"] for d in days] == [
        Decimal(v)
        for v in (
            "4109.59",
            "4110.45",
            "4110.28",
            "4110.11",
            "4111.63",
            "4111.20",
            "4112.74",
            "4112.33",
        )
    ]
    last = days[-1][TREASURY]
    assert last.fees_due == {
        "management": {"2026-11": Decimal("20552.06"), "2026-12": Decimal("12336.27")}
    }
    # Стоимость пая: СЧА / паи — 1 000,63 ₽ на 03.12.
    assert (nav(days[-1]) / last.units).quantize(Decimal("0.01")) == Decimal("1000.63")


def test_fee_payment_closes_the_period_with_a_correction() -> None:
    paid = Operation(
        id=UUID(int=207),
        kind=OperationKind.FEE_PAYMENT,
        portfolio_id=TREASURY,
        day=date(2026, 12, 3),
        amount=Decimal("20550.00"),
        fee_kind="management",
        period_end=date(2026, 11, 30),
    )
    days = week(rates=[MANAGEMENT], operations=[ISSUE, TO_BONDS, paid])
    balanced(days)
    treasury = days[-1][TREASURY]
    assert treasury.result is not None
    # Начислено за ноябрь 20 552,06 ₽, заплатили 20 550,00 ₽: корректировка −2,06 ₽ расхода.
    assert (treasury.result.charges, treasury.result.correction) == (
        Decimal("-4112.33"),
        Decimal("2.06"),
    )
    assert treasury.fees_due == {"management": {"2026-12": Decimal("12336.27")}}
    assert treasury.cash == Decimal("40000000") - Decimal("20550.00")


def test_rate_change_and_a_zero_rate() -> None:
    lower = FeeRate("management", Decimal("0"), date(2026, 11, 28), TREASURY)
    specdep = FeeRate("specdep", Decimal("0.1"), date(2026, 12, 1), BONDS)
    days = week(rates=[MANAGEMENT, lower, specdep])
    balanced(days)
    # С 28.11 ставка 0 — вознаграждение не начисляется; спецдепозитарий — с 01.12.
    assert [dict(d[TREASURY].fees_today) for d in days[1:3]] == [
        {"management": Decimal("4110.45")},
        {},
    ]
    # 0,1% × (100 059 700 − 8 220,04 вознаграждения за 26–27.11) / 365.
    assert days[5][BONDS].fees_today == {"specdep": Decimal("274.11")}


def test_fee_goes_to_a_treasury_without_its_own_activity() -> None:
    # Начисление в портфель, которого в ролле ещё нет: он появляется с обязательством.
    elsewhere = FeeRate("other", Decimal("1"), date(2026, 11, 26), UUID(int=13))
    days = week(rates=[elsewhere])
    balanced([{k: v for k, v in d.items() if k != UUID(int=13)} for d in days])
    first = days[0][UUID(int=13)]
    assert first.result is not None
    assert (first.fees_owed, first.result.value) == (Decimal("2739.73"), Decimal("-2739.73"))


# --- РЕПО (design/07): та же неделя, сделки А и Б ---------------------------------

REPO_A, REPO_B = UUID(int=301), UUID(int=302)


def repo(repo_id: UUID, *, direct: bool, dates: tuple[date, date, date], **fields: Any) -> Repo:
    traded, first, second = dates
    values: dict[str, Any] = {
        "id": repo_id,
        "instrument_id": None,
        "kind": None,
        "direct": direct,
        "quantity": 0,
        "trade_at": datetime(traded.year, traded.month, traded.day, 11, 5, tzinfo=MOSCOW),
        "trade_date": traded,
        "first_date": first,
        "second_date": second,
    } | fields
    return Repo(**values)


# А: казначейство размещает 30 млн в обратное РЕПО с КСУ на выходные под 14,20%.
KSU_REPO = repo(
    REPO_A,
    direct=False,
    dates=(date(2026, 11, 27), date(2026, 11, 27), date(2026, 11, 30)),
    first_amount=Decimal("30000000.00"),
    second_amount=Decimal("30035013.70"),
)
# Б: портфель облигаций привлекает деньги под 10 000 ОФЗ на 3 дня под 14,50%.
OFZ_REPO = repo(
    REPO_B,
    direct=True,
    dates=(date(2026, 11, 30), date(2026, 11, 30), date(2026, 12, 3)),
    instrument_id=OFZ,
    kind=InstrumentKind.BOND,
    quantity=10_000,
    first_amount=Decimal("5610100.00"),
    second_amount=Decimal("5616786.01"),
)
REPOS = {TREASURY: [KSU_REPO], BONDS: [OFZ_REPO]}


def amounts(*values: str) -> list[Decimal]:
    return [Decimal(v) for v in values]


def part(day: dict[UUID, State], name: str) -> Decimal:
    return sum((getattr(s.result, name) for s in day.values() if s.result is not None), Decimal(0))


def coupons(state: State) -> list[tuple[UUID | None, Decimal]]:
    return sorted(
        ((m.trade_id, m.cash) for m in state.movements if m.kind.value == "coupon"),
        key=lambda item: item[1],
    )


def test_week_with_repo_from_the_methodology() -> None:
    days = week(rates=[MANAGEMENT], repos=REPOS)
    balanced(days)
    # СЧА и вознаграждение по дням (design/07) — независимый прототип.
    assert [nav(d) for d in days] == amounts(
        "99995890.41",
        "100016779.96",
        "100024340.43",
        "100031900.60",
        "100080458.77",
        "100067817.55",
        "100103174.88",
        "100090832.72",
    )
    assert [d[TREASURY].fees_today["management"] for d in days] == amounts(
        "4109.59", "4110.45", "4110.76", "4111.07", "4113.06", "4112.55", "4114.00", "4113.49"
    )
    assert [part(d, "repo") for d in days] == amounts(
        "0", "0", "11671.23", "11671.24", "11671.23", "-2228.67", "-2228.67", "-2228.67"
    )
    # Переоценка и купонный доход — как без РЕПО.
    totals = [sum((part(d, n) for d in days), Decimal(0)) for n in ("revaluation", "coupon")]
    assert totals == amounts("48000", "27400")
    # Деньги и бумаги на счёте портфеля облигаций — лестница из методики.
    bonds = [d[BONDS] for d in days[4:]]
    assert [s.cash for s in bonds] == amounts(
        "37609600.00", "48833800.00", "49895800.00", "44279013.99"
    )
    assert [s.holdings[OFZ].settle_quantity for s in bonds] == [40_000, 20_000, 20_000, 30_000]
    assert [s.holdings[OFZ].trade_quantity for s in bonds] == [30_000] * 4
    # 02.12 купон: 20 000 бумаг с эмитента и 10 000 — передачей дохода от НКЦ.
    assert coupons(bonds[2]) == [(REPO_B, Decimal("354000.00")), (None, Decimal("708000.00"))]
    assert bonds[1].valuations[OFZ].coupon_due == Decimal("1062000.00")
    assert days[4][TREASURY].cash == Decimal("40035013.70")


def test_repo_positions_carry_principal_and_interest() -> None:
    days = week(repos=REPOS)
    (placed,) = days[3][TREASURY].repos
    assert (placed.direct, placed.principal, placed.accrued, placed.value) == (
        False,
        Decimal("30000000.00"),
        Decimal("23342.47"),
        Decimal("30023342.47"),
    )
    (borrowed,) = days[5][BONDS].repos
    assert (borrowed.quantity, borrowed.principal, borrowed.accrued, borrowed.value) == (
        10_000,
        Decimal("5610100.00"),
        Decimal("2228.67"),
        Decimal("-5612328.67"),
    )
    # Вторая часть закрыла сделки: в позициях их нет.
    assert days[4][TREASURY].repos == ()
    assert days[7][BONDS].repos == ()
    state = days[5][BONDS]
    assert from_json(json.loads(json.dumps(to_json(state)))) == state


def test_money_contribution_shifts_debt_and_interest() -> None:
    paid = Contribution(UUID(int=401), date(2026, 12, 2), cash=Decimal("-20000.00"))
    changed = replace(OFZ_REPO, second_amount=Decimal("5596778.06"), contributions=(paid,))
    days = week(repos={BONDS: [changed]})
    balanced(days)
    assert [part(d, "repo") for d in days[5:]] == amounts("-2228.67", "-2228.67", "-2220.72")
    (position,) = days[6][BONDS].repos
    assert (position.principal, position.accrued) == (Decimal("5590100.00"), Decimal("4457.34"))
    assert days[6][BONDS].cash - days[5][BONDS].cash == Decimal("1042000.00")


def test_securities_contribution_returns_with_the_second_leg() -> None:
    # 01.12 фонд довносит 1 000 ОФЗ: на счёте их нет, доход за них — передачей от НКЦ.
    more = Contribution(UUID(int=402), date(2026, 12, 1), quantity=-1_000)
    days = week(repos={BONDS: [replace(OFZ_REPO, contributions=(more,))]})
    balanced(days)
    assert [d[BONDS].holdings[OFZ].settle_quantity for d in days[5:]] == [19_000, 19_000, 30_000]
    assert coupons(days[6][BONDS]) == [
        (REPO_B, Decimal("389400.00")),
        (None, Decimal("672600.00")),
    ]


def test_reverse_repo_with_bonds_passes_the_coupon_on() -> None:
    # Обратное РЕПО с ОФЗ через дату фиксации: купон от эмитента уходит НКЦ.
    received = repo(
        UUID(int=303),
        direct=False,
        dates=(date(2026, 11, 30), date(2026, 11, 30), date(2026, 12, 3)),
        instrument_id=OFZ,
        kind=InstrumentKind.BOND,
        quantity=5_000,
        first_amount=Decimal("2805050.00"),
        second_amount=Decimal("2808393.00"),
    )
    days = week(repos={TREASURY: [received]})
    balanced(days)
    treasury = days[6][TREASURY]
    assert coupons(treasury) == [
        (UUID(int=303), Decimal("-177000.00")),
        (None, Decimal("177000.00")),
    ]
    assert OFZ not in treasury.valuations
    assert sum((part(d, "repo") for d in days), Decimal(0)) == Decimal("3343.00")


def test_direct_ksu_repo_with_a_fee_paid_at_the_first_leg() -> None:
    # Прямое РЕПО с КСУ, первая часть на следующий торговый день; комиссия — расход
    # в день сделки, деньги за неё — в первой части.
    borrowed = repo(
        UUID(int=304),
        direct=True,
        dates=(date(2026, 11, 27), date(2026, 11, 30), date(2026, 12, 1)),
        first_amount=Decimal("1000000.00"),
        second_amount=Decimal("1000397.26"),
        fee=Decimal("50.00"),
    )
    days = week(repos={TREASURY: [borrowed]})
    balanced(days)
    friday, tuesday = days[1][TREASURY].result, days[5][TREASURY].result
    assert friday is not None
    assert tuesday is not None
    assert (friday.fees, friday.payable) == (Decimal("-50.00"), Decimal("50.00"))
    assert days[4][TREASURY].cash - days[3][TREASURY].cash == Decimal("999950.00")
    assert tuesday.repo == Decimal("-397.26")


def test_repo_returned_on_the_first_day_accrues_evenly() -> None:
    # Взнос в день первой части вернул весь долг: проценты делятся по ночам поровну.
    back = Contribution(UUID(int=403), date(2026, 11, 30), cash=Decimal("-1000000.00"))
    empty = repo(
        UUID(int=305),
        direct=True,
        dates=(date(2026, 11, 30), date(2026, 11, 30), date(2026, 12, 2)),
        first_amount=Decimal("1000000.00"),
        second_amount=Decimal("10.00"),
        contributions=(back,),
    )
    assert [empty.accrued(date(2026, 12, d)) for d in (1, 2)] == amounts("-5.00", "-10.00")


LINKER = UUID(int=10504)


@pytest.mark.parametrize(
    ("traded", "settled", "face"),
    [
        (date(2026, 11, 9), date(2026, 11, 10), "1071.26"),
        # Пятница: расчёты в понедельник, номинал — с индексацией за выходные.
        (date(2026, 11, 6), date(2026, 11, 9), "1070.85"),
    ],
    ids=["monday", "friday"],
)
def test_linker_bought_at_the_market_has_no_result_on_the_trade_date(
    traded: date, settled: date, face: str
) -> None:
    # ВЭБ2Р-58: номинал индексируется на RUONIA и растёт каждый день. Сделка по цене оценки
    # стоит столько же, сколько бумага в оценке: номинал у обеих — на дату расчётов.
    buy = Trade(
        id=BUY_ID,
        instrument_id=LINKER,
        kind=InstrumentKind.BOND,
        side=Side.BUY,
        quantity=10_000,
        price=Decimal("99.75"),
        face_value=Decimal(face),
        accrued=Decimal(0),
        amount=trade_amount(
            InstrumentKind.BOND, 10_000, Decimal("99.75"), Decimal(face), Decimal(0)
        ),
        fee=Decimal(0),
        trade_at=datetime(traded.year, traded.month, traded.day, 11, tzinfo=MOSCOW),
        trade_date=traded,
        settle_date=settled,
    )
    terms = Terms(face=Decimal(1000), indexed=True)
    # Отметка: номинал из ряда ISS на дату расчётов (сервис цен, решение 149).
    mark = Mark(price=Decimal("99.75"), settle=settled, accrued=Decimal(0), face=Decimal(face))
    state = roll(
        State(day=traded - timedelta(days=1)), [buy], lambda _i, _d: terms, lambda _i, _d: mark
    )

    valuation = state.valuations[LINKER]
    assert (valuation.clean, valuation.face, valuation.revaluation) == (
        buy.amount,
        Decimal(face),
        Decimal(0),
    )
    assert terms.outstanding_on(settled) is None


# --- выплаты линкера до объявления суммы (решение 150) ------------------------------

LINKER_COUPON = Payment(
    pay_date=date(2026, 11, 19),
    record_date=date(2026, 11, 18),
    has_coupon=True,
    coupon=None,  # сумму справочник узнает в день выплаты
    principal=Decimal(0),
    outstanding=None,
    final=False,
    rate=Decimal("1.85"),
    start=date(2026, 5, 15),
)
# Номинал из ряда ISS на дату расчётов T+1 и НКД на неё.
LINKER_MARKS = {
    date(2026, 11, 17): (date(2026, 11, 18), "1074.54", "10.13"),
    date(2026, 11, 18): (date(2026, 11, 19), "1074.95", "0.00"),
    date(2026, 11, 19): (date(2026, 11, 20), "1075.36", "0.05"),
}


def constant[T](value: T) -> Callable[[UUID, date], T]:
    return lambda _i, _d: value


def linker_days(plan: Terms, *announced: Terms, start: date = date(2026, 11, 16)) -> list[State]:
    """20 000 бумаг ВЭБ2Р-58 по 99,75% с ``start``; ``announced`` — условия по дням дальше."""
    lot = Lot(BUY_ID, datetime(2026, 10, 27, tzinfo=MOSCOW), 20_000, Decimal("99.75"),
              Decimal("1065.92"), Decimal(0))  # fmt: skip
    current = State(
        day=start, holdings={LINKER: Holding(LINKER, InstrumentKind.BOND, 20_000, 20_000, (lot,))}
    )
    states = []
    for terms in (plan, plan, *announced):
        settle, face, accrued = LINKER_MARKS[current.day + timedelta(days=1)]
        mark = Mark(
            price=Decimal("99.75"), settle=settle, accrued=Decimal(accrued), face=Decimal(face)
        )
        current = roll(current, [], constant(terms), constant(mark))
        states.append(current)
    return states


def test_linker_coupon_is_estimated_until_announced() -> None:
    plan = Terms((LINKER_COUPON,), face=Decimal(1000), indexed=True)
    paid = Terms(
        (replace(LINKER_COUPON, coupon=Decimal("10.24")),), face=Decimal(1000), indexed=True
    )
    before, record, payday = linker_days(plan, paid)

    assert before.valuations[LINKER].accrued_value == Decimal("202600.00")
    # Вечер 18.11: НКД на 19.11 — уже по новому периоду, а купон — к получению по оценке
    # ISS: 1 074,95 × 1,85% × 188 / 365 = 10,24 ₽ на бумагу. Провала в стоимости нет.
    valuation = record.valuations[LINKER]
    assert (valuation.accrued_value, valuation.coupon_due, valuation.flags) == (
        Decimal(0),
        Decimal("204800.00"),
        (Flag.DUE_ESTIMATED,),
    )
    # 19.11 справочник узнал сумму — купон пришёл деньгами, требование закрылось.
    assert (payday.valuations[LINKER].coupon_due, payday.valuations[LINKER].flags) == (
        Decimal(0),
        (),
    )
    assert payday.cash == Decimal("204800.00")


def test_linker_coupon_with_an_earlier_record_date() -> None:
    # Фиксация 17.11: НКД на 18.11 ещё содержит купон — оценку из него вычитаем.
    plan = Terms((replace(LINKER_COUPON, record_date=date(2026, 11, 17)),), face=Decimal(1000),
                 indexed=True)  # fmt: skip
    [record, _] = linker_days(plan)
    valuation = record.valuations[LINKER]
    estimate = Decimal("10.24")  # 1 074,54 × 1,85% × 188 / 365
    assert valuation.accrued == Decimal("10.13") - estimate
    assert valuation.coupon_due == 20_000 * estimate


def test_floater_coupon_is_not_estimated() -> None:
    # Флоатер: ставка будущего купона неизвестна — купон не оцениваем (решение 14).
    plan = Terms((replace(LINKER_COUPON, record_date=date(2026, 11, 17)),), face=Decimal(1000))
    [record, _] = linker_days(plan)
    valuation = record.valuations[LINKER]
    assert (valuation.accrued, valuation.coupon_due, valuation.flags) == (
        Decimal("10.13"),
        Decimal(0),
        (Flag.COUPON_UNKNOWN,),
    )


def test_linker_redemption_is_estimated_until_announced() -> None:
    maturity = replace(LINKER_COUPON, has_coupon=False, rate=None, start=None, principal=None,
                       final=True)  # fmt: skip
    plan = Terms((maturity,), face=Decimal(1000), indexed=True)
    [_, record] = linker_days(plan)
    # После даты фиксации погашения бумага — 0, к получению — номинал на 19.11 из ISS.
    valuation = record.valuations[LINKER]
    assert (valuation.clean, valuation.principal_due, valuation.flags) == (
        Decimal(0),
        Decimal("21499000.00"),
        (Flag.DUE_ESTIMATED,),
    )
