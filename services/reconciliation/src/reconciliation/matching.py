"""Сверка дня счёта (design/08): выписка брокера или банка против системы.

Чистые функции: на входе дни счёта из сервиса сделок, выписки и принятые
расхождения, на выходе — строки сверки со статусами. Сверяются сделки дня,
движения денег и остаток денег (решение 118); сравнение точное, до копейки
(решение 120).

Принятое расхождение движения ждёт пары: движения с тем же ключом и обратным
расхождением в другой день — и сводится с ней само (решение 123). Поэтому дни
сверяются подряд: принятое вчера влияет на сегодня.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

ZERO = Decimal(0)

# Виды движений выписки и ключ сопоставления (решение 120).
BY_TRADE = ("settlement", "fee", "repo_first", "repo_second", "repo_contribution")
BY_SECURITY = ("coupon", "amortization", "redemption")
BY_DAY = ("transfer", "unit_settlement", "fee_payment", "expense_payment", "other")
KINDS = BY_TRADE + BY_SECURITY + BY_DAY

# Движение сервиса сделок → вид выписки. Остальные движения деньги не двигают.
SYSTEM_KINDS: Mapping[str, str] = {
    "settlement": "settlement",
    "fee": "fee",
    "repo_first": "repo_first",
    "repo_second": "repo_second",
    "repo_contribution": "repo_contribution",
    "coupon": "coupon",
    "amortization": "amortization",
    "redemption": "redemption",
    "transfer_in": "transfer",
    "transfer_out": "transfer",
    "unit_settlement": "unit_settlement",
    "fee_payment": "fee_payment",
    "expense_payment": "expense_payment",
}


class Status(StrEnum):
    MATCHED = "matched"  # сходится
    MISMATCHED = "mismatched"  # пара есть, поля или суммы разные
    NOT_IN_SYSTEM = "not_in_system"  # есть в выписке, нет в системе
    NOT_IN_STATEMENT = "not_in_statement"  # есть в системе, нет в выписке
    ACCEPTED = "accepted"  # расхождение принято с комментарием
    PAIRED = "paired"  # сошлось с принятой строкой другого дня
    EXPLAINED = "explained"  # остаток: расхождение объясняют принятые строки, ждущие пары


RESOLVED = frozenset({Status.MATCHED, Status.ACCEPTED, Status.PAIRED, Status.EXPLAINED})


# --- что сверяем ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StatementTrade:
    """Сделка из выписки — как её прислал брокер, с бумагой из справочника."""

    external_id: str
    kind: str  # outright | repo
    side: str
    code: str  # SECID или ISIN, как прислали; KSU — РЕПО с КСУ
    instrument_id: UUID | None
    settle_date: date
    quantity: int
    price: Decimal
    accrued: Decimal | None
    amount: Decimal
    fee: Decimal
    repo_rate: Decimal | None = None
    second_date: date | None = None
    second_amount: Decimal | None = None


@dataclass(frozen=True, slots=True)
class StatementMovement:
    kind: str
    amount: Decimal  # + пришли, − ушли
    trade: str | None = None  # номер сделки
    code: str | None = None  # бумага, как прислали
    instrument_id: UUID | None = None
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class Statement:
    id: UUID
    account_id: UUID
    day: date
    opening: Decimal
    closing: Decimal
    trades: tuple[StatementTrade, ...]
    movements: tuple[StatementMovement, ...]
    source: str
    received_by: str
    received_at: datetime


@dataclass(frozen=True, slots=True)
class SystemTrade:
    """Неотменённая сделка дня в системе — как её отдаёт сервис сделок."""

    trade_id: UUID
    version: int
    status: str
    portfolio_code: str
    external_id: str | None
    kind: str
    side: str
    instrument_id: UUID | None
    secid: str
    short_name: str
    settle_date: date
    quantity: int
    price: Decimal
    accrued: Decimal | None
    amount: Decimal
    fee: Decimal
    repo_rate: Decimal | None = None
    second_date: date | None = None
    second_amount: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SystemMovement:
    kind: str  # вид движения сервиса сделок
    cash: Decimal
    portfolio_code: str
    trade_id: UUID | None = None
    external_id: str | None = None
    instrument_id: UUID | None = None
    secid: str | None = None


@dataclass(frozen=True, slots=True)
class SystemDay:
    day: date
    trading: bool
    calculated: bool
    opening: Decimal
    closing: Decimal
    trades: tuple[SystemTrade, ...] = ()
    movements: tuple[SystemMovement, ...] = ()


@dataclass(frozen=True, slots=True)
class Acceptance:
    id: UUID
    day: date
    line: str
    signature: Mapping[str, Any]  # строка, какой её приняли
    comment: str
    accepted_by: str
    accepted_at: datetime


# --- строки сверки --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Difference:
    field: str
    system: str | None
    statement: str | None


@dataclass(frozen=True, slots=True)
class TradeLine:
    line: str
    status: Status
    system: SystemTrade | None
    statement: StatementTrade | None
    differences: tuple[Difference, ...] = ()
    acceptance: Acceptance | None = None

    def signature(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "differences": [[d.field, d.system, d.statement] for d in self.differences],
        }


@dataclass(frozen=True, slots=True, order=True)
class MovementKey:
    kind: str
    trade: str | None = None  # номер сделки; сделка без номера — «id:…»
    instrument_id: UUID | None = None

    @property
    def line(self) -> str:
        return f"movement:{self.kind}:{self.trade or ''}:{self.instrument_id or ''}"


@dataclass(frozen=True, slots=True)
class MovementLine:
    key: MovementKey
    status: Status
    system: Decimal | None  # None — в системе такого движения нет
    statement: Decimal | None  # None — в выписке нет
    secid: str | None = None  # бумага: код из системы или из выписки
    comments: tuple[str, ...] = ()  # комментарии строк выписки
    paired: date | None = None  # день принятой строки, с которой сошлось
    acceptance: Acceptance | None = None

    @property
    def line(self) -> str:
        return self.key.line

    @property
    def discrepancy(self) -> Decimal:
        return (self.statement or ZERO) - (self.system or ZERO)

    def signature(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "system": _text(self.system),
            "statement": _text(self.statement),
        }


@dataclass(frozen=True, slots=True)
class BalanceLine:
    line: str  # balance:opening | balance:closing
    status: Status
    system: Decimal
    statement: Decimal
    expected: Decimal  # сумма принятых расхождений, которые ждут пары
    acceptance: Acceptance | None = None

    @property
    def discrepancy(self) -> Decimal:
        return self.statement - self.system

    def signature(self) -> dict[str, Any]:
        return {"system": _text(self.system), "statement": _text(self.statement)}


@dataclass(frozen=True, slots=True)
class OpenItem:
    """Принятое расхождение движения, которое ждёт пары."""

    day: date
    key: MovementKey
    discrepancy: Decimal
    comment: str
    secid: str | None = None


@dataclass(frozen=True, slots=True)
class DayReconciliation:
    system: SystemDay
    statement: Statement | None
    trades: tuple[TradeLine, ...] = ()
    movements: tuple[MovementLine, ...] = ()
    opening: BalanceLine | None = None
    closing: BalanceLine | None = None
    waiting: tuple[OpenItem, ...] = ()  # ждут пары на конец дня
    lines: Mapping[str, Any] = field(default_factory=dict)  # строка по ключу

    @property
    def day(self) -> date:
        return self.system.day

    @property
    def unresolved(self) -> list[Any]:
        """Строки, которые не сошлись и не приняты."""
        return [line for line in self.lines.values() if line.status not in RESOLVED]


def reconcile(
    days: Sequence[SystemDay],
    statements: Mapping[date, Statement],
    acceptances: Iterable[Acceptance],
    waiting: Sequence[OpenItem] = (),
) -> list[DayReconciliation]:
    """Дни счёта подряд: принятые расхождения переходят в следующие дни, пока не найдут пару.
    ``waiting`` — что ждало пары накануне первого дня: точка отсчёта на закрытом дне."""
    accepted: dict[tuple[date, str], Acceptance] = {}
    for item in sorted(acceptances, key=lambda a: a.accepted_at):
        accepted[(item.day, item.line)] = item  # действует последнее
    carried = list(waiting)
    result: list[DayReconciliation] = []
    for system in days:
        statement = statements.get(system.day)
        if statement is None:
            result.append(DayReconciliation(system, None, waiting=tuple(carried)))
            continue
        mine = {line: a for (day, line), a in accepted.items() if day == system.day}
        before = sum((w.discrepancy for w in carried), ZERO)
        trades = [_accept(t, mine) for t in _trades(system.trades, statement.trades)]
        movements: list[MovementLine] = []
        for found in _movements(system.movements, statement.movements):
            settled, carried = _settle(found, carried, mine)
            movements.append(settled)
        after = sum((w.discrepancy for w in carried), ZERO)
        opening = _accept(
            _balance("balance:opening", system.opening, statement.opening, before), mine
        )
        closing = _accept(
            _balance("balance:closing", system.closing, statement.closing, after), mine
        )
        lines: dict[str, Any] = {t.line: t for t in trades} | {m.line: m for m in movements}
        lines |= {opening.line: opening, closing.line: closing}
        result.append(
            DayReconciliation(
                system,
                statement,
                trades=tuple(trades),
                movements=tuple(movements),
                opening=opening,
                closing=closing,
                waiting=tuple(carried),
                lines=lines,
            )
        )
    return result


# --- сделки ---------------------------------------------------------------------


def _trades(system: Sequence[SystemTrade], statement: Sequence[StatementTrade]) -> list[TradeLine]:
    """Пара — по номеру сделки биржи; у сделки в системе номера нет — по бумаге,
    направлению и цене, если такая сделка одна (решение 120)."""
    by_number = {t.external_id: t for t in system if t.external_id}
    used: set[UUID] = set()
    lines: list[TradeLine] = []
    for theirs in statement:
        ours = by_number.get(theirs.external_id)
        if ours is None:
            candidates = [
                t
                for t in system
                if t.external_id is None
                and t.trade_id not in used
                and (t.kind, t.side, t.instrument_id)
                == (theirs.kind, theirs.side, theirs.instrument_id)
                and t.price == theirs.price
            ]
            ours = candidates[0] if len(candidates) == 1 else None
        line = f"trade:{theirs.external_id}"
        if ours is None:
            lines.append(TradeLine(line, Status.NOT_IN_SYSTEM, None, theirs))
            continue
        used.add(ours.trade_id)
        differences = _differences(ours, theirs)
        status = Status.MISMATCHED if differences else Status.MATCHED
        lines.append(TradeLine(line, status, ours, theirs, differences))
    for ours in system:
        if ours.trade_id not in used:
            line = f"trade:{ours.external_id}" if ours.external_id else f"trade:id:{ours.trade_id}"
            lines.append(TradeLine(line, Status.NOT_IN_STATEMENT, ours, None))
    return lines


def _differences(ours: SystemTrade, theirs: StatementTrade) -> tuple[Difference, ...]:
    """Поля, которые разошлись. Время заключения не сравнивается: в форме и отчёте
    оно может разойтись на минуты."""
    pairs: list[tuple[str, Any, Any]] = [
        ("external_id", ours.external_id, theirs.external_id),
        ("kind", ours.kind, theirs.kind),
        ("side", ours.side, theirs.side),
        ("security", ours.instrument_id, theirs.instrument_id),
        ("quantity", ours.quantity, theirs.quantity),
        ("price", ours.price, theirs.price),
        ("accrued", ours.accrued or ZERO, theirs.accrued or ZERO),
        ("amount", ours.amount, theirs.amount),
        ("fee", ours.fee, theirs.fee),
        ("settle_date", ours.settle_date, theirs.settle_date),
        ("repo_rate", ours.repo_rate, theirs.repo_rate),
        ("second_date", ours.second_date, theirs.second_date),
        ("second_amount", ours.second_amount, theirs.second_amount),
    ]
    # Бумагу показываем кодом: в системе — SECID, в выписке — как прислали.
    shown = {"security": (ours.secid, theirs.code)}
    return tuple(
        Difference(name, *shown.get(name, (_text(a), _text(b)))) for name, a, b in pairs if a != b
    )


# --- движения -------------------------------------------------------------------


def _movements(
    system: Sequence[SystemMovement], statement: Sequence[StatementMovement]
) -> list[MovementLine]:
    """Движения по виду и ключу: суммы с одним ключом складываются. Перевод между
    портфелями одного счёта складывается в ноль и в сверку не попадает."""
    ours: dict[MovementKey, Decimal] = {}
    theirs: dict[MovementKey, Decimal] = {}
    secids: dict[MovementKey, str] = {}
    comments: dict[MovementKey, list[str]] = {}
    for m in system:
        kind = SYSTEM_KINDS.get(m.kind)
        if kind is None:
            continue
        trade = m.external_id or (None if m.trade_id is None else f"id:{m.trade_id}")
        key = _key(kind, trade, m.instrument_id)
        ours[key] = ours.get(key, ZERO) + m.cash
        if m.secid is not None and key.instrument_id is not None:
            secids[key] = m.secid
    for s in statement:
        key = _key(s.kind, s.trade, s.instrument_id)
        theirs[key] = theirs.get(key, ZERO) + s.amount
        if s.code is not None:
            secids.setdefault(key, s.code)
        if s.comment:
            comments.setdefault(key, []).append(s.comment)
    lines: list[MovementLine] = []
    for key in sorted(ours.keys() | theirs.keys(), key=_order):
        system_sum, statement_sum = ours.get(key), theirs.get(key)
        if not system_sum and not statement_sum:
            continue
        if statement_sum is None:
            status = Status.NOT_IN_STATEMENT
        elif system_sum is None:
            status = Status.NOT_IN_SYSTEM
        else:
            status = Status.MATCHED if system_sum == statement_sum else Status.MISMATCHED
        lines.append(
            MovementLine(
                key,
                status,
                system_sum,
                statement_sum,
                secid=secids.get(key),
                comments=tuple(comments.get(key, ())),
            )
        )
    return lines


def _key(kind: str, trade: str | None, instrument_id: UUID | None) -> MovementKey:
    if kind in BY_TRADE:
        return MovementKey(kind, trade)
    if kind in BY_SECURITY:
        # Передача дохода по РЕПО — ещё и с номером сделки.
        return MovementKey(kind, trade, instrument_id)
    return MovementKey(kind)


def _order(key: MovementKey) -> tuple[int, str, str]:
    return (KINDS.index(key.kind), key.trade or "", str(key.instrument_id or ""))


def _settle(
    line: MovementLine, waiting: list[OpenItem], accepted: Mapping[str, Acceptance]
) -> tuple[MovementLine, list[OpenItem]]:
    """Несошедшаяся строка сначала ищет принятую строку другого дня с обратным
    расхождением; не нашла — может быть принята сама и тогда ждёт пары."""
    if line.status is Status.MATCHED:
        return line, waiting
    for item in waiting:
        if item.key == line.key and item.discrepancy == -line.discrepancy:
            rest = [w for w in waiting if w is not item]
            return replace(line, status=Status.PAIRED, paired=item.day), rest
    line = _accept(line, accepted)
    if line.acceptance is None:
        return line, waiting
    opened = OpenItem(
        line.acceptance.day, line.key, line.discrepancy, line.acceptance.comment, line.secid
    )
    return line, [*waiting, opened]


# --- остаток --------------------------------------------------------------------


def _balance(line: str, system: Decimal, statement: Decimal, expected: Decimal) -> BalanceLine:
    """Остаток — контрольная сумма: его расхождение равно сумме принятых расхождений,
    которые ещё ждут пары. Не равно — в выписках не хватает движения или выписки."""
    discrepancy = statement - system
    if discrepancy != expected:
        status = Status.MISMATCHED
    else:
        status = Status.MATCHED if not discrepancy else Status.EXPLAINED
    return BalanceLine(line, status, system, statement, expected)


# --- принятие -------------------------------------------------------------------


def _accept[L: (TradeLine, MovementLine, BalanceLine)](
    line: L, accepted: Mapping[str, Acceptance]
) -> L:
    """Принятие действует, пока строка такая же, какой её приняли (решение 123)."""
    if line.status in RESOLVED:
        return line
    found = accepted.get(line.line)
    if found is None or dict(found.signature) != line.signature():
        return line
    return replace(line, status=Status.ACCEPTED, acceptance=found)


def _text(value: Any) -> str | None:
    """Значение для строки сверки и подписи принятия: число без лишних нулей."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return f"{value.normalize():f}" if value else "0"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
