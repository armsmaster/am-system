"""Дневной ролл портфеля: состояние дня из вчерашнего и событий дня (design/05).

Чистые функции без базы и сети:

    состояние(D) = ролл(состояние(D − 1), сделки, условия бумаг на D)

- Экономическая позиция и лоты FIFO — по дате сделки (решение 22).
- Бумаги на счёте депо и деньги — по дате расчётов.
- Купон, амортизацию и погашение получает тот, у кого бумага на счёте депо
  на конец даты фиксации; деньги приходят в дату выплаты.

Портфели фонда роллятся вместе (``roll_fund``): перевод бумаг переносит лоты
отправителя получателю (решение 98). Операции фонда — паи и расходы —
проводятся в казначейском портфеле.

Порядок внутри дня: сделки по дате заключения → расчёты по сделкам →
операции фонда и переводы → право на выплаты по бумагам на счёте на конец
дня → выплаты дня → амортизация и погашение.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

ZERO = Decimal(0)
HUNDRED = Decimal(100)
KOPECK = Decimal("0.01")


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class InstrumentKind(StrEnum):
    BOND = "bond"
    FUND_SHARE = "fund_share"  # пай биржевого фонда


@dataclass(frozen=True, slots=True)
class Trade:
    """Действующая версия сделки — то, что нужно роллу."""

    id: UUID
    instrument_id: UUID
    kind: InstrumentKind
    side: Side
    quantity: int
    price: Decimal  # облигация — % от номинала, пай — ₽
    face_value: Decimal | None  # облигация: номинал на дату расчётов
    accrued: Decimal | None  # облигация: НКД на бумагу
    amount: Decimal
    fee: Decimal
    trade_at: datetime
    trade_date: date
    settle_date: date

    @property
    def signed(self) -> int:
        return self.quantity if self.side is Side.BUY else -self.quantity


def trade_amount(
    kind: InstrumentKind,
    quantity: int,
    price: Decimal,
    face_value: Decimal | None,
    accrued: Decimal | None,
) -> Decimal:
    """Сумма сделки до копейки: облигация — количество × (цена × номинал / 100 + НКД),
    пай — количество × цена (решение 86)."""
    if kind is InstrumentKind.BOND:
        if face_value is None:
            raise ValueError("bond trade without face value")
        per_bond = price * face_value / HUNDRED + (accrued or ZERO)
        return (quantity * per_bond).quantize(KOPECK, rounding=ROUND_HALF_UP)
    return (quantity * price).quantize(KOPECK, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class Payment:
    """Выплата по бумаге в одну дату: купон и (или) погашение номинала."""

    pay_date: date
    record_date: date
    has_coupon: bool
    coupon: Decimal | None  # на бумагу; None — сумма ещё не объявлена
    principal: Decimal | None  # погашение номинала на бумагу: 0 — нет, None — не объявлено
    outstanding: Decimal | None  # непогашенный номинал после выплаты
    final: bool  # погашение: позиция закрывается


@dataclass(frozen=True, slots=True)
class Terms:
    """Что роллу нужно знать о бумаге на дату: выплаты по графику."""

    payments: tuple[Payment, ...] = ()

    def paid_on(self, day: date) -> Payment | None:
        return next((p for p in self.payments if p.pay_date == day), None)


# Условия бумаги по версии справочника на дату.
TermsOn = Callable[[UUID, date], Terms]


@dataclass(frozen=True, slots=True)
class Lot:
    """Открытый лот FIFO: часть покупки (или короткой продажи), ещё не закрытая."""

    trade_id: UUID
    opened_at: datetime
    quantity: int  # > 0 — длинный лот, < 0 — короткий
    price: Decimal
    face: Decimal | None  # облигация: номинал на бумагу, на который куплен лот
    accrued: Decimal  # НКД на бумагу при открытии

    @property
    def cost(self) -> Decimal:
        """Стоимость по чистой цене, ₽ (решение 88)."""
        if self.face is None:
            return self.quantity * self.price
        return self.quantity * self.price * self.face / HUNDRED

    @property
    def accrued_paid(self) -> Decimal:
        return self.quantity * self.accrued


@dataclass(frozen=True, slots=True)
class Holding:
    instrument_id: UUID
    kind: InstrumentKind
    trade_quantity: int = 0  # по дате сделки
    settle_quantity: int = 0  # на счёте депо
    lots: tuple[Lot, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.trade_quantity or self.settle_quantity or self.lots)


@dataclass(frozen=True, slots=True)
class Entitlement:
    """Право на выплату: бумаги были на счёте на конец даты фиксации."""

    instrument_id: UUID
    pay_date: date
    record_date: date
    quantity: int


class MovementKind(StrEnum):
    SETTLEMENT = "settlement"  # расчёты по сделке: бумаги и деньги
    FEE = "fee"  # комиссия по сделке
    COUPON = "coupon"
    AMORTIZATION = "amortization"
    REDEMPTION = "redemption"  # погашение
    PAYMENT_UNKNOWN = "payment_unknown"  # выплата сегодня, но сумма не объявлена
    UNIT_ISSUE = "unit_issue"  # паи выданы: паёв больше, требование к пайщикам
    UNIT_REDEMPTION = "unit_redemption"  # паи погашены: паёв меньше, обязательство
    UNIT_SETTLEMENT = "unit_settlement"  # расчёт по паям: деньги
    EXPENSE_ACCRUAL = "expense_accrual"
    EXPENSE_PAYMENT = "expense_payment"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"


@dataclass(frozen=True, slots=True)
class Movement:
    """Движение дня по счёту — для прослеживаемости и лестницы."""

    kind: MovementKind
    instrument_id: UUID | None  # у денежных операций бумаги нет
    trade_id: UUID | None
    quantity: int  # бумаги на счёте депо: + пришли, − ушли
    cash: Decimal  # деньги: + пришли, − ушли
    operation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class State:
    day: date
    cash: Decimal = ZERO
    holdings: Mapping[UUID, Holding] = field(default_factory=dict)
    pending: tuple[Entitlement, ...] = ()
    movements: tuple[Movement, ...] = ()
    # Уровень фонда — в казначейском портфеле (решение 35).
    units: Decimal = ZERO  # паи фонда
    claims: Decimal = ZERO  # требования к пайщикам: паи выданы, деньги не пришли
    obligations: Decimal = ZERO  # обязательства перед пайщиками: паи погашены, деньги не ушли
    expenses: Decimal = ZERO  # начисленные и не выплаченные расходы


class OperationKind(StrEnum):
    UNIT_ISSUE = "unit_issue"
    UNIT_REDEMPTION = "unit_redemption"
    EXPENSE_ACCRUAL = "expense_accrual"
    EXPENSE_PAYMENT = "expense_payment"
    CASH_TRANSFER = "cash_transfer"
    SECURITY_TRANSFER = "security_transfer"


@dataclass(frozen=True, slots=True)
class Operation:
    """Действующая версия операции фонда или перевода — то, что нужно роллу."""

    id: UUID
    kind: OperationKind
    portfolio_id: UUID  # паи и расходы — казначейский портфель; перевод — отправитель
    day: date
    amount: Decimal = ZERO
    units: Decimal = ZERO
    planned: date | None = None  # паи: плановая дата расчёта
    settled: date | None = None  # паи: расчёт отмечен этой датой (решение 42)
    receiver_id: UUID | None = None
    instrument_id: UUID | None = None
    instrument_kind: InstrumentKind | None = None
    quantity: int = 0


def start(first_day: date) -> State:
    """Состояние накануне первого дня истории: ничего нет (решение 9)."""
    return State(day=first_day - timedelta(days=1))


@dataclass(slots=True)
class _Day:
    """Состояние портфеля в течение дня — пока его меняют события дня."""

    day: date
    holdings: dict[UUID, Holding]
    cash: Decimal
    pending: list[Entitlement]
    movements: list[Movement]
    units: Decimal
    claims: Decimal
    obligations: Decimal
    expenses: Decimal


def roll(previous: State, trades: Iterable[Trade], terms_on: TermsOn) -> State:
    """Состояние портфеля на конец следующего дня после ``previous``."""
    return _close(_open(previous, trades), terms_on)


def roll_fund(
    previous: Mapping[UUID, State],
    day: date,
    trades: Mapping[UUID, Sequence[Trade]],
    operations: Sequence[Operation],
    terms_on: TermsOn,
    *,
    projected: bool = False,
) -> dict[UUID, State]:
    """Состояния всех портфелей фонда на конец ``day`` по состояниям на конец предыдущего дня.

    ``projected`` — прогноз для лестницы: расчёт по паям, который ещё не отмечен,
    проходит в плановую дату.
    """
    before = day - timedelta(days=1)
    portfolios = (
        set(previous)
        | set(trades)
        | {o.portfolio_id for o in operations}
        | {o.receiver_id for o in operations if o.receiver_id is not None}
    )
    works = {p: _open(previous.get(p) or State(day=before), trades.get(p, ())) for p in portfolios}
    for operation in sorted(operations, key=lambda o: str(o.id)):
        _OPERATIONS[operation.kind](operation, works, day, projected)
    return {p: _close(work, terms_on) for p, work in works.items()}


def roll_fund_days(
    previous: Mapping[UUID, State],
    first_day: date,
    till: date,
    *,
    trades: Mapping[UUID, Sequence[Trade]],
    operations: Sequence[Operation],
    terms_on: TermsOn,
    projected: bool = False,
) -> list[dict[UUID, State]]:
    """Состояния фонда на каждый календарный день с ``first_day`` по ``till``."""
    days: list[dict[UUID, State]] = []
    current = dict(previous)
    day = first_day
    while day <= till:
        current = roll_fund(current, day, trades, operations, terms_on, projected=projected)
        days.append(current)
        day += timedelta(days=1)
    return days


def _open(previous: State, trades: Iterable[Trade]) -> _Day:
    """Начало дня: сделки по дате заключения и расчёты по сделкам."""
    day = previous.day + timedelta(days=1)
    todays = list(trades)
    work = _Day(
        day=day,
        holdings=dict(previous.holdings),
        cash=previous.cash,
        pending=list(previous.pending),
        movements=[],
        units=previous.units,
        claims=previous.claims,
        obligations=previous.obligations,
        expenses=previous.expenses,
    )
    _trade_day(work.holdings, [t for t in todays if t.trade_date == day])
    work.cash = _settle(
        work.holdings, [t for t in todays if t.settle_date == day], work.cash, work.movements
    )
    return work


def _close(work: _Day, terms_on: TermsOn) -> State:
    """Конец дня: право на выплаты, выплаты, амортизация и погашение."""
    pending = [*work.pending, *_record(work.holdings, work.day, terms_on)]
    pending, received = _payments(pending, work.day, terms_on, work.movements)
    _amortize(work.holdings, work.day, terms_on)
    return State(
        day=work.day,
        cash=work.cash + received,
        holdings={k: v for k, v in work.holdings.items() if not v.empty},
        pending=tuple(pending),
        movements=tuple(work.movements),
        units=work.units,
        claims=work.claims,
        obligations=work.obligations,
        expenses=work.expenses,
    )


def _units(operation: Operation, works: dict[UUID, _Day], day: date, projected: bool) -> None:
    """Выдача и погашение паёв: паи и требование (обязательство) — в дату операции,
    деньги — в дату расчёта, которую отмечает сопровождение (решение 42)."""
    work = works[operation.portfolio_id]
    issue = operation.kind is OperationKind.UNIT_ISSUE
    sign = 1 if issue else -1
    if operation.day == day:
        work.units += sign * operation.units
        if issue:
            work.claims += operation.amount
        else:
            work.obligations += operation.amount
        kind = MovementKind.UNIT_ISSUE if issue else MovementKind.UNIT_REDEMPTION
        work.movements.append(Movement(kind, None, None, 0, ZERO, operation.id))
    settled = operation.settled or (operation.planned if projected else None)
    if settled == day:
        work.cash += sign * operation.amount
        if issue:
            work.claims -= operation.amount
        else:
            work.obligations -= operation.amount
        work.movements.append(
            Movement(
                MovementKind.UNIT_SETTLEMENT, None, None, 0, sign * operation.amount, operation.id
            )
        )


def _expense(operation: Operation, works: dict[UUID, _Day], day: date, _projected: bool) -> None:
    """Начисление расхода — обязательство; выплата — деньги уходят, обязательство меньше."""
    if operation.day != day:
        return
    work = works[operation.portfolio_id]
    if operation.kind is OperationKind.EXPENSE_ACCRUAL:
        work.expenses += operation.amount
        work.movements.append(
            Movement(MovementKind.EXPENSE_ACCRUAL, None, None, 0, ZERO, operation.id)
        )
        return
    work.cash -= operation.amount
    work.expenses -= operation.amount
    work.movements.append(
        Movement(MovementKind.EXPENSE_PAYMENT, None, None, 0, -operation.amount, operation.id)
    )


def _transfer(operation: Operation, works: dict[UUID, _Day], day: date, _projected: bool) -> None:
    """Перевод между портфелями фонда в дату исполнения — по обеим осям сразу (решение 37).
    Бумаги переходят лотами отправителя по FIFO, без финансового результата (решение 98)."""
    if operation.day != day:
        return
    # Получатель, бумага и её вид у перевода есть всегда — проверено при вводе.
    sender, receiver = works[operation.portfolio_id], works[cast(UUID, operation.receiver_id)]
    if operation.kind is OperationKind.CASH_TRANSFER:
        sender.cash -= operation.amount
        receiver.cash += operation.amount
        sender.movements.append(
            Movement(MovementKind.TRANSFER_OUT, None, None, 0, -operation.amount, operation.id)
        )
        receiver.movements.append(
            Movement(MovementKind.TRANSFER_IN, None, None, 0, operation.amount, operation.id)
        )
        return
    instrument, quantity = cast(UUID, operation.instrument_id), operation.quantity
    kind = cast(InstrumentKind, operation.instrument_kind)
    given = sender.holdings.get(instrument) or Holding(instrument, kind)
    taken, left = take(given.lots, quantity)
    sender.holdings[instrument] = replace(
        given,
        trade_quantity=given.trade_quantity - quantity,
        settle_quantity=given.settle_quantity - quantity,
        lots=left,
    )
    got = receiver.holdings.get(instrument) or Holding(instrument, kind)
    receiver.holdings[instrument] = replace(
        got,
        trade_quantity=got.trade_quantity + quantity,
        settle_quantity=got.settle_quantity + quantity,
        lots=tuple(sorted([*got.lots, *taken], key=lambda lot: (lot.opened_at, str(lot.trade_id)))),
    )
    sender.movements.append(
        Movement(MovementKind.TRANSFER_OUT, instrument, None, -quantity, ZERO, operation.id)
    )
    receiver.movements.append(
        Movement(MovementKind.TRANSFER_IN, instrument, None, quantity, ZERO, operation.id)
    )


_OPERATIONS: Mapping[OperationKind, Callable[[Operation, dict[UUID, _Day], date, bool], None]] = {
    OperationKind.UNIT_ISSUE: _units,
    OperationKind.UNIT_REDEMPTION: _units,
    OperationKind.EXPENSE_ACCRUAL: _expense,
    OperationKind.EXPENSE_PAYMENT: _expense,
    OperationKind.CASH_TRANSFER: _transfer,
    OperationKind.SECURITY_TRANSFER: _transfer,
}


def take(lots: tuple[Lot, ...], quantity: int) -> tuple[tuple[Lot, ...], tuple[Lot, ...]]:
    """Перевод забирает ``quantity`` бумаг из длинных лотов с самого раннего:
    (взятые лоты, оставшиеся)."""
    taken: list[Lot] = []
    left = list(lots)
    remaining = quantity
    while remaining and left and left[0].quantity > 0:
        first = left[0]
        if first.quantity <= remaining:
            taken.append(first)
            left.pop(0)
            remaining -= first.quantity
        else:
            taken.append(replace(first, quantity=remaining))
            left[0] = replace(first, quantity=first.quantity - remaining)
            remaining = 0
    return tuple(taken), tuple(left)


def _holding(holdings: dict[UUID, Holding], trade: Trade) -> Holding:
    return holdings.get(trade.instrument_id) or Holding(trade.instrument_id, trade.kind)


def _trade_day(holdings: dict[UUID, Holding], trades: Iterable[Trade]) -> None:
    """Сделки по дате заключения: экономическая позиция и лоты FIFO."""
    for trade in _ordered(trades):
        current = _holding(holdings, trade)
        holdings[trade.instrument_id] = replace(
            current,
            trade_quantity=current.trade_quantity + trade.signed,
            lots=fifo(current.lots, trade),
        )


def _settle(
    holdings: dict[UUID, Holding], trades: Iterable[Trade], cash: Decimal, movements: list[Movement]
) -> Decimal:
    """Расчёты по сделкам: бумаги на счёте депо, деньги, комиссия."""
    for trade in _ordered(trades):
        current = _holding(holdings, trade)
        holdings[trade.instrument_id] = replace(
            current, settle_quantity=current.settle_quantity + trade.signed
        )
        money = trade.amount if trade.side is Side.SELL else -trade.amount
        cash += money
        movements.append(
            Movement(MovementKind.SETTLEMENT, trade.instrument_id, trade.id, trade.signed, money)
        )
        if trade.fee:
            cash -= trade.fee
            movements.append(
                Movement(MovementKind.FEE, trade.instrument_id, trade.id, 0, -trade.fee)
            )
    return cash


def _record(holdings: Mapping[UUID, Holding], day: date, terms_on: TermsOn) -> list[Entitlement]:
    """Дата фиксации: право на выплату — по бумагам на счёте депо на конец дня."""
    return [
        Entitlement(instrument_id, p.pay_date, day, current.settle_quantity)
        for instrument_id, current in holdings.items()
        if current.settle_quantity
        for p in terms_on(instrument_id, day).payments
        if p.record_date == day
    ]


def _payments(
    pending: Iterable[Entitlement], day: date, terms_on: TermsOn, movements: list[Movement]
) -> tuple[list[Entitlement], Decimal]:
    """Выплаты по наступившим правам; сумма не объявлена — право ждёт."""
    still: list[Entitlement] = []
    received = ZERO
    for entitlement in pending:
        if entitlement.pay_date > day:
            still.append(entitlement)
            continue
        payment = terms_on(entitlement.instrument_id, day).paid_on(entitlement.pay_date)
        if payment is None:
            # Выплату убрали из графика новой версией бумаги — права больше нет.
            continue
        paid = _pay(entitlement, payment)
        if paid is None:
            still.append(entitlement)
            movements.append(
                Movement(MovementKind.PAYMENT_UNKNOWN, entitlement.instrument_id, None, 0, ZERO)
            )
            continue
        received += sum((m.cash for m in paid), ZERO)
        movements += paid
    return still, received


def _amortize(holdings: dict[UUID, Holding], day: date, terms_on: TermsOn) -> None:
    """Погашение закрывает позицию; амортизация — лоты стоят от непогашенного номинала."""
    for instrument_id, current in list(holdings.items()):
        payment = terms_on(instrument_id, day).paid_on(day)
        if payment is None:
            continue
        if payment.final:
            holdings[instrument_id] = Holding(instrument_id, current.kind)
        elif payment.outstanding is not None:
            outstanding = payment.outstanding
            holdings[instrument_id] = replace(
                current,
                lots=tuple(
                    replace(lot, face=min(lot.face, outstanding)) if lot.face is not None else lot
                    for lot in current.lots
                ),
            )


def roll_days(
    previous: State, till: date, trades: Sequence[Trade], terms_on: TermsOn
) -> list[State]:
    """Состояния на каждый календарный день после ``previous`` по ``till`` включительно."""
    states: list[State] = []
    current = previous
    while current.day < till:
        current = roll(current, trades, terms_on)
        states.append(current)
    return states


def fifo(lots: tuple[Lot, ...], trade: Trade) -> tuple[Lot, ...]:
    """Сделка закрывает встречные лоты с самого раннего; остаток открывает новый лот."""
    remaining = trade.signed
    result = list(lots)
    while remaining and result and (result[0].quantity > 0) != (remaining > 0):
        first = result[0]
        if abs(first.quantity) <= abs(remaining):
            remaining += first.quantity
            result.pop(0)
        else:
            result[0] = replace(first, quantity=first.quantity + remaining)
            remaining = 0
    if remaining:
        result.append(
            Lot(
                trade_id=trade.id,
                opened_at=trade.trade_at,
                quantity=remaining,
                price=trade.price,
                face=trade.face_value,
                accrued=trade.accrued or ZERO,
            )
        )
    return tuple(result)


def _kopecks(value: Decimal) -> Decimal:
    return value.quantize(KOPECK, rounding=ROUND_HALF_UP)


def _ordered(trades: Iterable[Trade]) -> list[Trade]:
    return sorted(trades, key=lambda t: (t.trade_at, str(t.id)))


def _pay(entitlement: Entitlement, payment: Payment) -> list[Movement] | None:
    """Деньги по праву на выплату; None — сумма купона или погашения не объявлена."""
    if (payment.has_coupon and payment.coupon is None) or payment.principal is None:
        return None
    movements: list[Movement] = []
    quantity = entitlement.quantity
    if payment.coupon is not None:
        movements.append(
            Movement(
                MovementKind.COUPON,
                entitlement.instrument_id,
                None,
                0,
                _kopecks(quantity * payment.coupon),
            )
        )
    if payment.principal:
        kind = MovementKind.REDEMPTION if payment.final else MovementKind.AMORTIZATION
        movements.append(
            Movement(
                kind,
                entitlement.instrument_id,
                None,
                -quantity if payment.final else 0,
                _kopecks(quantity * payment.principal),
            )
        )
    return movements


# --- снапшот в JSON и обратно -----------------------------------------------------


def to_json(state: State) -> dict[str, Any]:
    return {
        "day": state.day.isoformat(),
        "cash": str(state.cash),
        "holdings": [
            {
                "instrument_id": str(h.instrument_id),
                "kind": h.kind.value,
                "trade_quantity": h.trade_quantity,
                "settle_quantity": h.settle_quantity,
                "lots": [
                    {
                        "trade_id": str(lot.trade_id),
                        "opened_at": lot.opened_at.isoformat(),
                        "quantity": lot.quantity,
                        "price": str(lot.price),
                        "face": None if lot.face is None else str(lot.face),
                        "accrued": str(lot.accrued),
                    }
                    for lot in h.lots
                ],
            }
            for h in sorted(state.holdings.values(), key=lambda h: str(h.instrument_id))
        ],
        "pending": [
            {
                "instrument_id": str(e.instrument_id),
                "pay_date": e.pay_date.isoformat(),
                "record_date": e.record_date.isoformat(),
                "quantity": e.quantity,
            }
            for e in state.pending
        ],
        "movements": [
            {
                "kind": m.kind.value,
                "instrument_id": None if m.instrument_id is None else str(m.instrument_id),
                "trade_id": None if m.trade_id is None else str(m.trade_id),
                "operation_id": None if m.operation_id is None else str(m.operation_id),
                "quantity": m.quantity,
                "cash": str(m.cash),
            }
            for m in state.movements
        ],
        "units": str(state.units),
        "claims": str(state.claims),
        "obligations": str(state.obligations),
        "expenses": str(state.expenses),
    }


def from_json(data: Mapping[str, Any]) -> State:
    holdings = {}
    for h in data["holdings"]:
        holding = Holding(
            instrument_id=UUID(h["instrument_id"]),
            kind=InstrumentKind(h["kind"]),
            trade_quantity=h["trade_quantity"],
            settle_quantity=h["settle_quantity"],
            lots=tuple(
                Lot(
                    trade_id=UUID(lot["trade_id"]),
                    opened_at=datetime.fromisoformat(lot["opened_at"]),
                    quantity=lot["quantity"],
                    price=Decimal(lot["price"]),
                    face=None if lot["face"] is None else Decimal(lot["face"]),
                    accrued=Decimal(lot["accrued"]),
                )
                for lot in h["lots"]
            ),
        )
        holdings[holding.instrument_id] = holding
    return State(
        day=date.fromisoformat(data["day"]),
        cash=Decimal(data["cash"]),
        holdings=holdings,
        pending=tuple(
            Entitlement(
                instrument_id=UUID(e["instrument_id"]),
                pay_date=date.fromisoformat(e["pay_date"]),
                record_date=date.fromisoformat(e["record_date"]),
                quantity=e["quantity"],
            )
            for e in data["pending"]
        ),
        movements=tuple(
            Movement(
                kind=MovementKind(m["kind"]),
                instrument_id=_uuid(m["instrument_id"]),
                trade_id=_uuid(m["trade_id"]),
                quantity=m["quantity"],
                cash=Decimal(m["cash"]),
                operation_id=_uuid(m.get("operation_id")),
            )
            for m in data["movements"]
        ),
        units=Decimal(data.get("units", "0")),
        claims=Decimal(data.get("claims", "0")),
        obligations=Decimal(data.get("obligations", "0")),
        expenses=Decimal(data.get("expenses", "0")),
    )


def _uuid(value: str | None) -> UUID | None:
    return None if value is None else UUID(value)
