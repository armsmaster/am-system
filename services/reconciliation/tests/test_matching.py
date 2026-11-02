"""Сверка дня счёта без БД и сети: неделя брокерского счёта из методики шага 8 (design/08)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from reconciliation.matching import (
    ZERO,
    Acceptance,
    DayReconciliation,
    MovementKey,
    Statement,
    StatementMovement,
    StatementTrade,
    Status,
    SystemDay,
    SystemMovement,
    SystemTrade,
    reconcile,
)

OFZ = UUID("00000000-0000-0000-0000-000000026238")
LQDT = UUID("00000000-0000-0000-0000-0000000001d7")
BROKER = UUID("00000000-0000-0000-0000-00000000b002")
BUY, SELL = uuid4(), uuid4()
AT = datetime(2026, 12, 4, 9, 0, tzinfo=UTC)


def d(text: str) -> date:
    return date.fromisoformat(text)


def money(text: str) -> Decimal:
    return Decimal(text)


def ours(trade_id: UUID = BUY, **changes: Any) -> SystemTrade:
    """Покупка 27.11 в системе — пока без комиссии, как её ввели."""
    values: dict[str, Any] = {
        "trade_id": trade_id,
        "version": 1,
        "status": "entered",
        "portfolio_code": "OPIF1-BONDS",
        "external_id": "1000000001",
        "kind": "outright",
        "side": "buy",
        "instrument_id": OFZ,
        "secid": "SU26238RMFS4",
        "short_name": "ОФЗ 26238",
        "settle_date": d("2026-11-30"),
        "quantity": 50_000,
        "price": money("52.500000"),
        "accrued": money("35.01"),
        "amount": money("28000500.00"),
        "fee": money("0.00"),
    }
    return SystemTrade(**(values | changes))


def theirs(**changes: Any) -> StatementTrade:
    """Покупка 27.11 по отчёту брокера: комиссия 2 800,05 ₽."""
    values: dict[str, Any] = {
        "external_id": "1000000001",
        "kind": "outright",
        "side": "buy",
        "code": "SU26238RMFS4",
        "instrument_id": OFZ,
        "settle_date": d("2026-11-30"),
        "quantity": 50_000,
        "price": money("52.50"),
        "accrued": money("35.01"),
        "amount": money("28000500.00"),
        "fee": money("2800.05"),
    }
    return StatementTrade(**(values | changes))


def system(
    day: str,
    opening: str,
    closing: str,
    *movements: SystemMovement,
    trades: tuple[SystemTrade, ...] = (),
) -> SystemDay:
    return SystemDay(d(day), True, True, money(opening), money(closing), trades, movements)


def statement(
    day: str,
    opening: str,
    closing: str,
    *movements: StatementMovement,
    trades: tuple[StatementTrade, ...] = (),
) -> Statement:
    return Statement(
        uuid4(),
        BROKER,
        d(day),
        money(opening),
        money(closing),
        trades,
        movements,
        "broker-x",
        "feed",
        AT,
    )


def moved(kind: str, cash: str, **changes: Any) -> SystemMovement:
    return SystemMovement(kind=kind, cash=money(cash), portfolio_code="OPIF1-BONDS", **changes)


def theirs_moved(kind: str, amount: str, **changes: Any) -> StatementMovement:
    return StatementMovement(kind=kind, amount=money(amount), **changes)


def accepted(
    day: str, line: str, signature: dict[str, Any], comment: str = "Объяснено"
) -> Acceptance:
    return Acceptance(uuid4(), d(day), line, signature, comment, "ops", AT)


def one(
    days: list[SystemDay], statements: list[Statement], acceptances: Sequence[Acceptance] = ()
) -> DayReconciliation:
    return reconcile(days, {s.day: s for s in statements}, acceptances)[-1]


# --- сделки ---------------------------------------------------------------------


def test_fee_missing_in_the_purchase() -> None:
    # 27.11: брокер удержал комиссию 2 800,05 ₽, а в системе её не ввели.
    found = one(
        [system("2026-11-27", "60000000", "60000000", trades=(ours(),))],
        [statement("2026-11-27", "60000000", "60000000", trades=(theirs(),))],
    )

    [line] = found.trades
    assert (line.line, line.status) == ("trade:1000000001", Status.MISMATCHED)
    assert [(x.field, x.system, x.statement) for x in line.differences] == [("fee", "0", "2800.05")]
    # Деньги уйдут в дату расчётов: остаток 27.11 сходится.
    assert found.closing is not None
    assert (found.closing.status, found.closing.discrepancy) == (Status.MATCHED, ZERO)
    assert [x.line for x in found.unresolved] == ["trade:1000000001"]

    fixed = one(
        [system("2026-11-27", "60000000", "60000000", trades=(ours(fee=money("2800.05")),))],
        [statement("2026-11-27", "60000000", "60000000", trades=(theirs(),))],
    )
    assert fixed.trades[0].status is Status.MATCHED
    assert fixed.unresolved == []


def test_sale_entered_for_fewer_bonds() -> None:
    # 30.11: в системе продали 20 000, по отчёту — 25 000.
    sale = ours(
        SELL,
        external_id="1000000002",
        side="sell",
        quantity=20_000,
        price=money("52.6"),
        accrued=money("35.21"),
        amount=money("11224200.00"),
        settle_date=d("2026-12-01"),
    )
    found = one(
        [system("2026-11-30", "60000000", "31996699.95", trades=(sale,))],
        [
            statement(
                "2026-11-30",
                "60000000",
                "31996699.95",
                trades=(
                    theirs(
                        external_id="1000000002",
                        side="sell",
                        quantity=25_000,
                        price=money("52.60"),
                        accrued=money("35.21"),
                        amount=money("14030250.00"),
                        fee=money("0"),
                        settle_date=d("2026-12-01"),
                    ),
                ),
            )
        ],
    )

    assert [(x.field, x.system, x.statement) for x in found.trades[0].differences] == [
        ("quantity", "20000", "25000"),
        ("amount", "11224200", "14030250"),
    ]


def test_trades_without_a_pair_and_a_trade_without_a_number() -> None:
    lost = ours(uuid4(), external_id="1000000009", instrument_id=LQDT, secid="LQDT")
    numberless = ours(external_id=None, fee=money("2800.05"))
    found = one(
        [system("2026-11-27", "0", "0", trades=(numberless, lost))],
        [
            statement(
                "2026-11-27",
                "0",
                "0",
                trades=(theirs(), theirs(external_id="1000000005", side="sell")),
            )
        ],
    )

    by_line = {t.line: t for t in found.trades}
    # Номера в системе нет — пара по бумаге, направлению и цене; номер — расхождение.
    assert [(x.field, x.system, x.statement) for x in by_line["trade:1000000001"].differences] == [
        ("external_id", None, "1000000001")
    ]
    assert by_line["trade:1000000005"].status is Status.NOT_IN_SYSTEM
    assert by_line["trade:1000000009"].status is Status.NOT_IN_STATEMENT


def test_two_numberless_candidates_make_no_pair() -> None:
    found = one(
        [
            system(
                "2026-11-27",
                "0",
                "0",
                trades=(ours(external_id=None), ours(uuid4(), external_id=None)),
            )
        ],
        [statement("2026-11-27", "0", "0", trades=(theirs(),))],
    )

    assert sorted(t.status for t in found.trades) == [
        Status.NOT_IN_STATEMENT,
        Status.NOT_IN_STATEMENT,
        Status.NOT_IN_SYSTEM,
    ]
    assert {t.line for t in found.trades if t.system} == {
        f"trade:id:{t.system.trade_id}" for t in found.trades if t.system
    }


def test_security_and_repo_fields() -> None:
    repo: dict[str, Any] = {"repo_rate": money("14.5"), "second_date": d("2026-12-04")}
    found = one(
        [
            system(
                "2026-11-27",
                "0",
                "0",
                trades=(ours(kind="repo", second_amount=money("28030000"), **repo),),
            )
        ],
        [
            statement(
                "2026-11-27",
                "0",
                "0",
                trades=(
                    theirs(
                        kind="repo",
                        code="LQDT",
                        instrument_id=LQDT,
                        fee=money("0"),
                        settle_date=d("2026-11-27"),
                        second_amount=money("28030100"),
                        **repo,
                    ),
                ),
            )
        ],
    )

    assert [(x.field, x.system, x.statement) for x in found.trades[0].differences] == [
        ("security", "SU26238RMFS4", "LQDT"),
        ("settle_date", "2026-11-30", "2026-11-27"),
        ("second_amount", "28030000", "28030100"),
    ]


# --- движения и остатки ---------------------------------------------------------


def test_purchase_settles_with_its_fee() -> None:
    found = one(
        [
            system(
                "2026-11-30",
                "60000000",
                "31996699.95",
                moved(
                    "settlement",
                    "-28000500",
                    trade_id=BUY,
                    external_id="1000000001",
                    instrument_id=OFZ,
                    secid="SU26238RMFS4",
                ),
                moved(
                    "fee",
                    "-2800.05",
                    trade_id=BUY,
                    external_id="1000000001",
                    instrument_id=OFZ,
                    secid="SU26238RMFS4",
                ),
            )
        ],
        [
            statement(
                "2026-11-30",
                "60000000",
                "31996699.95",
                theirs_moved("settlement", "-28000500.00", trade="1000000001"),
                theirs_moved("fee", "-2800.05", trade="1000000001"),
            )
        ],
    )

    assert [(m.key, m.status, m.discrepancy) for m in found.movements] == [
        (MovementKey("settlement", "1000000001"), Status.MATCHED, ZERO),
        (MovementKey("fee", "1000000001"), Status.MATCHED, ZERO),
    ]
    assert found.unresolved == []


def test_fee_the_system_does_not_know() -> None:
    # Комиссии в сделке нет: брокер списал 2 800,05 ₽, система — нет.
    found = one(
        [
            system(
                "2026-11-30",
                "60000000",
                "31999500",
                moved("settlement", "-28000500", trade_id=BUY, external_id="1000000001"),
            )
        ],
        [
            statement(
                "2026-11-30",
                "60000000",
                "31996699.95",
                theirs_moved("settlement", "-28000500.00", trade="1000000001"),
                theirs_moved("fee", "-2800.05", trade="1000000001"),
            )
        ],
    )

    fee = found.movements[1]
    assert (fee.status, fee.system, fee.statement) == (
        Status.NOT_IN_SYSTEM,
        None,
        money("-2800.05"),
    )
    assert found.closing is not None
    assert (found.closing.status, found.closing.discrepancy, found.closing.expected) == (
        Status.MISMATCHED,
        money("-2800.05"),
        ZERO,
    )


def coupon_week(acceptances: list[Acceptance]) -> list[DayReconciliation]:
    """02.12 купон в системе, брокер зачислил его 03.12."""
    coupon = moved("coupon", "885000", instrument_id=OFZ, secid="SU26238RMFS4")
    return reconcile(
        [
            system("2026-12-02", "46026949.95", "46911949.95", coupon),
            system("2026-12-03", "46911949.95", "46911949.95"),
        ],
        {
            d("2026-12-02"): statement("2026-12-02", "46026949.95", "46026949.95"),
            d("2026-12-03"): statement(
                "2026-12-03",
                "46026949.95",
                "46911949.95",
                theirs_moved("coupon", "885000.00", code="SU26238RMFS4", instrument_id=OFZ),
            ),
        },
        acceptances,
    )


COUPON = f"movement:coupon::{OFZ}"


def test_coupon_credited_a_day_later() -> None:
    tuesday, wednesday = coupon_week([])
    [line] = tuesday.movements
    assert (line.line, line.status, line.discrepancy, line.secid) == (
        COUPON,
        Status.NOT_IN_STATEMENT,
        money("-885000"),
        "SU26238RMFS4",
    )
    assert tuesday.closing is not None
    assert tuesday.closing.status is Status.MISMATCHED
    # Без принятия 03.12 купон у брокера — «нет в системе», а остаток на начало
    # дня расходится: вчерашнее расхождение никто не объяснил.
    assert wednesday.movements[0].status is Status.NOT_IN_SYSTEM
    assert wednesday.opening is not None
    assert wednesday.opening.status is Status.MISMATCHED


def test_accepted_coupon_waits_for_its_pair() -> None:
    signature = {"status": "not_in_statement", "system": "885000", "statement": None}
    tuesday, wednesday = coupon_week(
        [accepted("2026-12-02", COUPON, signature, "Купон ОФЗ 26238 брокер зачислит 03.12")]
    )

    assert tuesday.movements[0].status is Status.ACCEPTED
    assert tuesday.closing is not None
    # Остаток объясняет принятое расхождение, которое ждёт пары.
    assert (tuesday.closing.status, tuesday.closing.expected) == (
        Status.EXPLAINED,
        money("-885000"),
    )
    assert [(w.day, w.discrepancy, w.comment) for w in tuesday.waiting] == [
        (d("2026-12-02"), money("-885000"), "Купон ОФЗ 26238 брокер зачислит 03.12")
    ]
    assert tuesday.unresolved == []
    # 03.12 купон у брокера сводится с принятой строкой 02.12 сам.
    [line] = wednesday.movements
    assert (line.status, line.paired) == (Status.PAIRED, d("2026-12-02"))
    assert wednesday.opening is not None
    assert wednesday.closing is not None
    assert (wednesday.opening.status, wednesday.opening.expected) == (
        Status.EXPLAINED,
        money("-885000"),
    )
    assert wednesday.closing.status is Status.MATCHED
    assert wednesday.waiting == ()
    assert wednesday.unresolved == []


def test_acceptance_lapses_when_the_line_changes() -> None:
    # Приняли расхождение на 880 000 ₽, а теперь оно на 885 000 ₽: принятие не действует.
    signature = {"status": "not_in_statement", "system": "880000", "statement": None}
    tuesday, _ = coupon_week([accepted("2026-12-02", COUPON, signature)])

    assert tuesday.movements[0].status is Status.NOT_IN_STATEMENT
    assert tuesday.movements[0].acceptance is None


def test_last_acceptance_wins() -> None:
    signature = {"status": "not_in_statement", "system": "885000", "statement": None}
    first = accepted("2026-12-02", COUPON, signature, "Первое")
    second = replace(
        accepted("2026-12-02", COUPON, signature, "Второе"),
        accepted_at=datetime(2026, 12, 4, 10, tzinfo=UTC),
    )
    tuesday, _ = coupon_week([second, first])

    acceptance = tuesday.movements[0].acceptance
    assert acceptance is not None
    assert acceptance.comment == "Второе"


def test_bank_day_sums_by_kind() -> None:
    # Банк показал расчёт по паям двумя платежами пайщиков, система — одной операцией.
    found = one(
        [
            system(
                "2026-11-26",
                "0",
                "40000000",
                moved("unit_settlement", "100000000"),
                moved("transfer_out", "-60000000"),
                moved("unit_issue", "0"),
            )
        ],
        [
            statement(
                "2026-11-26",
                "0",
                "40000000",
                theirs_moved("unit_settlement", "70000000", comment="ООО Альфа"),
                theirs_moved("unit_settlement", "30000000", comment="ООО Бета"),
                theirs_moved("transfer", "-60000000"),
            )
        ],
    )

    assert [(m.key.kind, m.status, m.system, m.comments) for m in found.movements] == [
        ("transfer", Status.MATCHED, money("-60000000"), ()),
        ("unit_settlement", Status.MATCHED, money("100000000"), ("ООО Альфа", "ООО Бета")),
    ]


def test_transfer_inside_one_account_is_not_seen() -> None:
    # Перевод между двумя портфелями одного счёта: брокер его не видит.
    found = one(
        [
            system(
                "2026-12-01", "100", "100", moved("transfer_out", "-50"), moved("transfer_in", "50")
            )
        ],
        [statement("2026-12-01", "100", "100")],
    )

    assert found.movements == ()
    assert found.unresolved == []


def test_repo_income_by_trade() -> None:
    # Передача дохода по РЕПО: купон с номером сделки, а не просто по бумаге.
    found = one(
        [
            system(
                "2026-12-02",
                "0",
                "35400",
                moved(
                    "coupon",
                    "35400",
                    instrument_id=OFZ,
                    trade_id=uuid4(),
                    external_id="2000000001",
                    secid="SU26238RMFS4",
                ),
            )
        ],
        [
            statement(
                "2026-12-02",
                "0",
                "35400",
                theirs_moved(
                    "coupon", "35400", code="SU26238RMFS4", instrument_id=OFZ, trade="2000000001"
                ),
            )
        ],
    )

    assert [(m.key, m.status) for m in found.movements] == [
        (MovementKey("coupon", "2000000001", OFZ), Status.MATCHED)
    ]


def test_movement_of_a_numberless_trade_and_a_mismatch() -> None:
    trade_id = uuid4()
    found = one(
        [system("2026-12-01", "0", "100", moved("settlement", "100", trade_id=trade_id))],
        [
            statement(
                "2026-12-01",
                "0",
                "100",
                theirs_moved("expense_payment", "-1"),
                theirs_moved("other", "1", comment="Возврат"),
                theirs_moved("settlement", "100", trade=f"id:{trade_id}"),
            )
        ],
    )
    assert [(m.key.kind, m.key.trade, m.status) for m in found.movements] == [
        ("settlement", f"id:{trade_id}", Status.MATCHED),
        ("expense_payment", None, Status.NOT_IN_SYSTEM),
        ("other", None, Status.NOT_IN_SYSTEM),
    ]

    differs = one(
        [system("2026-12-01", "0", "100", moved("fee_payment", "-100"))],
        [statement("2026-12-01", "0", "-99", theirs_moved("fee_payment", "-99"))],
    )
    assert [(m.status, m.discrepancy) for m in differs.movements] == [
        (Status.MISMATCHED, money("1"))
    ]


def test_day_without_a_statement_carries_what_waits() -> None:
    signature = {"status": "not_in_statement", "system": "885000", "statement": None}
    coupon = moved("coupon", "885000", instrument_id=OFZ)
    days = reconcile(
        [
            system("2026-12-02", "0", "885000", coupon),
            system("2026-12-03", "885000", "885000"),
        ],
        {d("2026-12-02"): statement("2026-12-02", "0", "0")},
        [accepted("2026-12-02", COUPON, signature)],
    )

    assert days[1].statement is None
    assert (days[1].trades, days[1].movements, days[1].closing) == ((), (), None)
    assert [w.day for w in days[1].waiting] == [d("2026-12-02")]


def test_trade_and_balance_lines_can_be_accepted() -> None:
    fee = {"status": "mismatched", "differences": [["fee", "0", "2800.05"]]}
    balance = {"system": "60000000", "statement": "59999999"}
    found = one(
        [system("2026-11-27", "60000000", "60000000", trades=(ours(),))],
        [
            statement(
                "2026-11-27",
                "60000000",
                "59999999",
                theirs_moved("other", "-1"),
                trades=(theirs(),),
            )
        ],
        [
            accepted("2026-11-27", "trade:1000000001", fee),
            accepted("2026-11-27", "balance:closing", balance),
            accepted(
                "2026-11-27",
                "movement:other::",
                {"status": "not_in_system", "system": None, "statement": "-1"},
            ),
        ],
    )

    assert found.trades[0].status is Status.ACCEPTED
    assert found.closing is not None
    # Принятое «прочее» ждёт пары, поэтому остаток объяснён и без принятия.
    assert found.closing.status is Status.EXPLAINED
    assert found.unresolved == []

    wrong = one(
        [system("2026-11-27", "60000000", "60000000")],
        [statement("2026-11-27", "60000000", "59999999", theirs_moved("other", "-1"))],
        [accepted("2026-11-27", "balance:closing", balance)],
    )
    assert wrong.closing is not None
    assert wrong.closing.status is Status.ACCEPTED
    assert wrong.closing.signature() == balance


def test_pair_needs_the_same_key_and_amount() -> None:
    # Ждёт купон 885 000 ₽; назавтра брокер зачислил 880 000 ₽ — пары нет.
    signature = {"status": "not_in_statement", "system": "885000", "statement": None}
    days = reconcile(
        [
            system("2026-12-02", "0", "885000", moved("coupon", "885000", instrument_id=OFZ)),
            system("2026-12-03", "885000", "885000"),
        ],
        {
            d("2026-12-02"): statement("2026-12-02", "0", "0"),
            d("2026-12-03"): statement(
                "2026-12-03", "0", "880000", theirs_moved("coupon", "880000", instrument_id=OFZ)
            ),
        },
        [accepted("2026-12-02", COUPON, signature)],
    )

    [line] = days[1].movements
    assert (line.status, line.paired) == (Status.NOT_IN_SYSTEM, None)
    assert [w.day for w in days[1].waiting] == [d("2026-12-02")]
