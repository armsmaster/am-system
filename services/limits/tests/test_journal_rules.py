"""Правила журнала без базы: нарушения дня и их вид, месяцы, продление, просрочка."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from limits.engine import CheckResult, Group, Problem, Status, Totals, Usage
from limits.errors import RuleViolationError
from limits.journal import BreachRecord, add_months, findings, month_bounds, overlaps

ZERO = Decimal(0)


def check(kind: str, status: Status = Status.BREACH, **fields: object) -> CheckResult:
    found: dict[str, object] = {
        "code": "L",
        "name": "Лимит",
        "clause": None,
        "label": None,
        "type": kind,
        "status": status,
        "value": None,
        "maximum": None,
        "minimum": None,
        "usage": None,
    }
    return CheckResult(**(found | fields))  # type: ignore[arg-type]


def usage(*checks: CheckResult) -> Usage:
    return Usage(Totals(ZERO, ZERO, ZERO, None, None), list(checks))


def test_metric_is_active_when_the_fund_traded() -> None:
    leverage = check("metric", value=Decimal("4.2"), maximum=Decimal(4))
    within = check("metric", status=Status.WARNING, value=Decimal("3.9"), maximum=Decimal(4))

    [active] = findings(usage(leverage, within), traded_any=True)
    [passive] = findings(usage(leverage), traded_any=False)

    assert (active.key, active.name, active.kind, active.label) == ("value", "фонд", "active", "")
    assert passive.kind == "passive"


def test_share_kind_depends_on_what_the_deals_added() -> None:
    groups = [
        Group(
            "a", "Эмитент А", Decimal("10.18"), Status.BREACH, Decimal("0.54")
        ),  # без сделки 9,64
        Group("g", "Эмитент Г", Decimal("10.5"), Status.BREACH, Decimal("0.11")),  # и без неё сверх
        Group("v", "Эмитент В", Decimal("10.3"), Status.BREACH),  # сделок нет
        Group("b", "Эмитент Б", Decimal("1"), Status.OK, Decimal("1")),
    ]
    a, g, v = findings(
        usage(check("share", label="на эмитента", maximum=Decimal(10), groups=groups)), False
    )
    assert [(f.key, f.kind, f.traded) for f in (a, g, v)] == [
        ("a", "active", True),
        ("g", "passive", True),
        ("v", "passive", False),
    ]


def test_rules_are_grouped_by_paper() -> None:
    violations = [
        Problem("SUB", "позиция не подходит ни под одно правило"),
        Problem("SUB", "сделка с бумагой не по правилам", by_deal=True),
        Problem("OLD", "позиция не подходит ни под одно правило"),
    ]
    sub, old = findings(usage(check("eligible", violations=violations)), traded_any=True)
    assert (sub.key, sub.kind, sub.value) == ("SUB", "active", None)
    assert sub.reason == "позиция не подходит ни под одно правило; сделка с бумагой не по правилам"
    assert (old.key, old.kind) == ("OLD", "passive")


def test_months() -> None:
    assert month_bounds("2026-11") == (date(2026, 11, 1), date(2026, 11, 30))
    assert month_bounds("2028-02") == (date(2028, 2, 1), date(2028, 2, 29))
    for wrong in ("2026-13", "ноябрь"):
        with pytest.raises(RuleViolationError):
            month_bounds(wrong)


def test_extension_limit_in_months() -> None:
    assert add_months(date(2026, 11, 12), 3) == date(2027, 2, 12)
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)
    assert add_months(date(2026, 1, 15), 0) == date(2026, 1, 15)


def record(first: date, eliminated: date | None, deadline: date) -> BreachRecord:
    return BreachRecord(
        id=uuid4(),
        fund_id=uuid4(),
        limit_code="Л4",
        label="на эмитента",
        check_type="share",
        key="g",
        name="Эмитент Г",
        reason=None,
        first_day=first,
        last_day=first,
        detected_on=first,
        value=None,
        peak=None,
        maximum=None,
        minimum=None,
        suggested="passive",
        kind="passive",
        kind_by=None,
        deadline=deadline,
        status="open" if eliminated is None else "eliminated",
        eliminated_on=eliminated,
        notes=[],
        extensions=[],
        created_at=datetime(2026, 11, 13, tzinfo=UTC),
    )


def test_overdue_and_month_overlap() -> None:
    november = (date(2026, 11, 1), date(2026, 11, 30))
    late = record(date(2026, 10, 20), None, date(2026, 10, 21))
    closed = record(date(2026, 10, 20), date(2026, 11, 1), date(2026, 10, 21))
    december = record(date(2026, 12, 1), None, date(2026, 12, 2))

    assert (late.state(date(2026, 10, 21)), late.state(date(2026, 10, 22))) == ("open", "overdue")
    assert closed.state(date(2026, 12, 1)) == "eliminated"
    # Устранено 1.11: в ноябре нарушения уже не было.
    assert [overlaps(r, *november) for r in (late, closed, december)] == [True, False, False]
