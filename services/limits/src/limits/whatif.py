"""Проверка сделки до ввода: лимиты «что если» (design/09, часть 9.4, решение 142).

Сделку добавляем к позициям и ценам на сейчас: покупка — к позиции бумаги, деньги —
к деньгам; прямое РЕПО — деньги и долг, обратное — размещение вместо денег. Покупка
за деньги активы не меняет: деньги стали бумагами.

Проверка — только информация: сделку не запрещает и в журнал ничего не пишет.
Показываем лишь то, что сделка меняет: показатели фонда, долю группы её бумаги,
статус проверки и нарушения из самой сделки.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID

from limits.engine import Attributes, CheckResult, Deal, Figures, Group, Position, Status, Usage

ZERO = Decimal(0)
HUNDRED = Decimal(100)
FOUR = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class Trade:
    side: str  # buy | sell; у РЕПО buy — обратное, sell — прямое
    amount: Decimal  # сумма сделки, у РЕПО — первой части
    instrument_id: UUID | None = None  # у РЕПО нет: бумаги остаются у фонда
    secid: str = ""
    quantity: int = 0


@dataclass(frozen=True, slots=True)
class Change:
    """Как сделка меняет одну проверку."""

    check: CheckResult  # проверка после сделки
    group: str | None  # группа бумаги сделки; у показателя и правил — нет
    before: Decimal | None
    after: Decimal | None
    before_status: Status
    after_status: Status
    reasons: list[str]  # нарушения сделки и пустые данные её бумаги


def apply(
    trade: Trade,
    figures: Figures,
    positions: Sequence[Position],
    deals: Sequence[Deal],
    attributes: Attributes,
) -> tuple[Figures, list[Position], list[Deal]]:
    """Состав СЧА, позиции и сделки дня — как будто сделку уже ввели."""
    if trade.instrument_id is None:
        # РЕПО: меняются деньги и долг или размещение.
        if trade.side == "sell":
            # Прямое РЕПО: фонд привлекает деньги, бумаги остаются в позиции.
            moved = replace(
                figures,
                cash=figures.cash + trade.amount,
                repo_borrowed=figures.repo_borrowed + trade.amount,
            )
        else:
            moved = replace(
                figures,
                cash=figures.cash - trade.amount,
                repo_placed=figures.repo_placed + trade.amount,
            )
        return moved, list(positions), list(deals)
    signed = trade.amount if trade.side == "buy" else -trade.amount
    quantity = trade.quantity if trade.side == "buy" else -trade.quantity
    held = list(positions)
    index = next((i for i, p in enumerate(held) if p.instrument_id == trade.instrument_id), None)
    if index is None:
        paper = attributes.papers.get(trade.instrument_id)
        # Новой бумаги в позициях нет — её дюрации тоже: проверка дюрации скажет «нет данных».
        held.append(
            Position(
                trade.instrument_id,
                trade.secid,
                "bond" if paper is None else paper.kind,
                quantity,
                signed,
                None,
            )
        )
    else:
        found = held[index]
        held[index] = replace(found, quantity=found.quantity + quantity, value=found.value + signed)
    deal = Deal(trade.instrument_id, trade.secid, trade.side, trade.amount)
    return replace(figures, cash=figures.cash - signed), held, [*deals, deal]


def compare(
    before: Usage, after: Usage, trade: Trade, attributes: Attributes, warn_at: Decimal
) -> list[Change]:
    """Проверки, которые сделка меняет, — в порядке набора."""
    keys = _keys(trade, attributes)
    changes: list[Change] = []
    for old, new in zip(before.checks, after.checks, strict=True):
        found = _change(old, new, trade, keys, warn_at)
        if found is not None:
            changes.append(found)
    return changes


def _change(
    old: CheckResult, new: CheckResult, trade: Trade, keys: set[str], warn_at: Decimal
) -> Change | None:
    if new.type == "metric":
        if _same(old.value, new.value) and old.status is new.status:
            return None
        return _whole(old, new, _missing(new, trade))
    if new.type == "share":
        return _share(old, new, trade, keys, warn_at)
    # Новые нарушения бумаги сделки: сама сделка или позиция после неё, например короткая.
    reasons = [
        p.reason for p in new.violations if p.secid == trade.secid and p not in old.violations
    ]
    if not reasons and old.status is new.status:
        return None
    return _whole(old, new, reasons + _missing(new, trade))


def _share(
    old: CheckResult, new: CheckResult, trade: Trade, keys: set[str], warn_at: Decimal
) -> Change | None:
    """Доля группы бумаги сделки; без бумаги — только если сменился статус проверки."""
    was, now = _group(old.groups, keys), _group(new.groups, keys)
    group = now or was
    if group is not None and not _same(_value(was), _value(now)):
        return Change(
            check=new,
            group=group.name,
            before=_value(was),
            after=_value(now),
            before_status=_status(_value(was), new.maximum, warn_at),
            after_status=_status(_value(now), new.maximum, warn_at),
            reasons=_missing(new, trade),
        )
    if old.status is new.status:
        return None
    return Change(
        check=new,
        group=new.groups[0].name if new.groups else None,
        before=old.value,
        after=new.value,
        before_status=old.status,
        after_status=new.status,
        reasons=_missing(new, trade),
    )


def _whole(old: CheckResult, new: CheckResult, reasons: list[str]) -> Change:
    return Change(new, None, old.value, new.value, old.status, new.status, reasons)


def _keys(trade: Trade, attributes: Attributes) -> set[str]:
    """Ключи групп, куда попадает бумага сделки: все бумаги, эмитент, выпуск."""
    if trade.instrument_id is None:
        return set()
    paper = attributes.papers.get(trade.instrument_id)
    keys = {"all", str(trade.instrument_id)}
    if paper is not None and paper.issuer_id is not None:
        keys.add(str(paper.issuer_id))
    return keys


def _group(groups: Sequence[Group], keys: set[str]) -> Group | None:
    # Проверка делит бумаги одним способом — бумаге подходит не больше одной группы.
    return next((g for g in groups if g.key in keys), None)


def _value(group: Group | None) -> Decimal:
    return ZERO if group is None else group.value


def _status(value: Decimal, maximum: Decimal | None, warn_at: Decimal) -> Status:
    """Статус группы, как у проверки: сверх лимита — нарушение, с порога — близко."""
    limit = maximum or ZERO  # у доли порог есть всегда
    if value > limit:
        return Status.BREACH
    if limit > 0 and value / limit * HUNDRED >= warn_at:
        return Status.WARNING
    return Status.OK


def _missing(check: CheckResult, trade: Trade) -> list[str]:
    return [p.reason for p in check.missing if trade.secid and p.secid == trade.secid]


def _same(left: Decimal | None, right: Decimal | None) -> bool:
    if left is None or right is None:
        return left is right
    return left.quantize(FOUR) == right.quantize(FOUR)
