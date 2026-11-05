"""«Что если» без сети и базы: новая бумага, РЕПО, какие проверки сделка меняет."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from limits.engine import (
    Attributes,
    CheckResult,
    Figures,
    Group,
    Paper,
    Problem,
    Rated,
    Status,
    Totals,
    Usage,
)
from limits.whatif import Trade, apply, compare

ZERO = Decimal(0)
BOND, ORPHAN, ISSUER = uuid4(), uuid4(), uuid4()
WARN = Decimal(90)


def paper(paper_id: UUID, secid: str, issuer: UUID | None) -> Paper:
    return Paper(
        paper_id, secid, "bond", "exchange_bond", issuer, "fixed", "RUB", {}, Rated("unrated")
    )


ATTRIBUTES = Attributes(
    papers={BOND: paper(BOND, "BOND", ISSUER), ORPHAN: paper(ORPHAN, "ORPHAN", None)},
    issuers={},
    classifiers={},
    grades=("AAA",),
)
FIGURES = Figures(*([ZERO] * 9), nav=Decimal(100))  # type: ignore[misc]


def check(kind: str = "share", status: Status = Status.OK, **fields: object) -> CheckResult:
    found: dict[str, object] = {
        "code": "L",
        "name": "Лимит",
        "clause": None,
        "label": None,
        "type": kind,
        "status": status,
        "value": None,
        "maximum": Decimal(10),
        "minimum": None,
        "usage": None,
    }
    return CheckResult(**(found | fields))  # type: ignore[arg-type]


def usage(*checks: CheckResult) -> Usage:
    return Usage(Totals(ZERO, ZERO, ZERO, None, None), list(checks))


def test_a_new_bond_has_no_duration_yet() -> None:
    buy = Trade("buy", Decimal(5), BOND, "BOND", 5)
    figures, positions, deals = apply(buy, FIGURES, [], [], ATTRIBUTES)

    [position] = positions
    assert (position.kind, position.value, position.quantity, position.duration) == (
        "bond",
        Decimal(5),
        5,
        None,
    )
    assert (figures.cash, [d.secid for d in deals]) == (Decimal(-5), ["BOND"])
    # Бумаги нет в справочнике — считаем облигацией: справочник скажет, чего не хватает.
    ghost = Trade("buy", Decimal(1), uuid4(), "GHOST", 1)
    assert apply(ghost, FIGURES, [], [], ATTRIBUTES)[1][0].kind == "bond"


def test_what_the_trade_changes() -> None:
    sell = Trade("sell", Decimal(5), ORPHAN, "ORPHAN", 5)
    duration = check("metric", value=None, maximum=Decimal(2))
    was = [Group(str(ORPHAN), "ORPHAN", Decimal("9.5"), Status.OK)]
    groups = check(groups=was), check()
    nothing = check(status=Status.WARNING), check(status=Status.BREACH)
    zero = check(groups=was, maximum=ZERO), check(maximum=ZERO)
    missing = Problem("ORPHAN", "нет дюрации: нет цены или условий выпуска")
    before = usage(duration, groups[0], nothing[0], zero[0], check("eligible"))
    after = usage(
        check("metric", value=None, maximum=Decimal(2), status=Status.NO_DATA, missing=[missing]),
        groups[1],
        nothing[1],
        zero[1],
        check("eligible"),
    )

    found = compare(before, after, sell, ATTRIBUTES, WARN)

    # Дюрация стала «нет данных» — с причиной по бумаге сделки.
    assert [(c.check.type, c.group, c.before, c.after) for c in found] == [
        ("metric", None, None, None),
        ("share", "ORPHAN", Decimal("9.5"), ZERO),
        ("share", None, None, None),
        ("share", "ORPHAN", Decimal("9.5"), ZERO),
    ]
    assert found[0].reasons == ["нет дюрации: нет цены или условий выпуска"]
    # Близко к лимиту — с 90%; при лимите 0% любая доля — нарушение.
    assert [(c.before_status, c.after_status) for c in found[1:]] == [
        (Status.WARNING, Status.OK),
        (Status.WARNING, Status.BREACH),
        (Status.BREACH, Status.OK),
    ]
