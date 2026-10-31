"""Дневной ролл портфеля: состояние дня из вчерашнего и событий дня (design/05).

Чистые функции без базы и сети:

    состояние(D) = ролл(состояние(D − 1), сделки, условия бумаг на D)

- Экономическая позиция и лоты FIFO — по дате сделки (решение 22).
- Бумаги на счёте депо и деньги — по дате расчётов.
- Купон, амортизацию и погашение получает тот, у кого бумага на счёте депо
  на конец даты фиксации; деньги приходят в дату выплаты.

Порядок внутри дня: сделки по дате заключения → расчёты → право на выплаты
по бумагам на счёте на конец дня → выплаты дня → амортизация и погашение.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any
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


@dataclass(frozen=True, slots=True)
class Movement:
    """Движение дня по счёту — для прослеживаемости и лестницы."""

    kind: MovementKind
    instrument_id: UUID
    trade_id: UUID | None
    quantity: int  # бумаги на счёте депо: + пришли, − ушли
    cash: Decimal  # деньги: + пришли, − ушли


@dataclass(frozen=True, slots=True)
class State:
    day: date
    cash: Decimal = ZERO
    holdings: Mapping[UUID, Holding] = field(default_factory=dict)
    pending: tuple[Entitlement, ...] = ()
    movements: tuple[Movement, ...] = ()


def start(first_day: date) -> State:
    """Состояние накануне первого дня истории: ничего нет (решение 9)."""
    return State(day=first_day - timedelta(days=1))


def roll(previous: State, trades: Iterable[Trade], terms_on: TermsOn) -> State:
    """Состояние на конец следующего дня после ``previous``."""
    day = previous.day + timedelta(days=1)
    todays = list(trades)
    holdings = dict(previous.holdings)
    movements: list[Movement] = []
    _trade_day(holdings, [t for t in todays if t.trade_date == day])
    cash = _settle(holdings, [t for t in todays if t.settle_date == day], previous.cash, movements)
    pending = [*previous.pending, *_record(holdings, day, terms_on)]
    pending, received = _payments(pending, day, terms_on, movements)
    _amortize(holdings, day, terms_on)
    return State(
        day=day,
        cash=cash + received,
        holdings={k: v for k, v in holdings.items() if not v.empty},
        pending=tuple(pending),
        movements=tuple(movements),
    )


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
                "instrument_id": str(m.instrument_id),
                "trade_id": None if m.trade_id is None else str(m.trade_id),
                "quantity": m.quantity,
                "cash": str(m.cash),
            }
            for m in state.movements
        ],
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
                instrument_id=UUID(m["instrument_id"]),
                trade_id=None if m["trade_id"] is None else UUID(m["trade_id"]),
                quantity=m["quantity"],
                cash=Decimal(m["cash"]),
            )
            for m in data["movements"]
        ),
    )
