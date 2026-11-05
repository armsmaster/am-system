"""Расчёт лимитов без сети и базы: «нет данных», нарушения правил, рейтинговая группа."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from limits.engine import (
    Attributes,
    Classifier,
    Deal,
    Figures,
    Issuer,
    Paper,
    Position,
    Rated,
    Status,
    evaluate,
)
from limits.model import SetBody

ZERO = Decimal(0)
SENIORITY, SENIOR, SUB = uuid4(), uuid4(), uuid4()
KIND, BANK = uuid4(), uuid4()
NCC, YES = uuid4(), uuid4()
GOOD, LOW, NAMELESS, QUIET = uuid4(), uuid4(), uuid4(), uuid4()
OFZ, CORP, ORPHAN, STRANGER, SUB_BOND, SILENT = (uuid4() for _ in range(6))


def paper(
    instrument_id: UUID,
    secid: str,
    issuer: UUID | None,
    *,
    iss_type: str | None = "exchange_bond",
    classes: dict[UUID, UUID] | None = None,
    ratings: Rated | None = None,
) -> Paper:
    return Paper(
        id=instrument_id,
        secid=secid,
        kind="bond",
        iss_type=iss_type,
        issuer_id=issuer,
        coupon_kind="fixed",
        currency="RUB",
        classes={SENIORITY: SENIOR, NCC: YES} if classes is None else classes,
        ratings=ratings or Rated("unrated"),
    )


ATTRIBUTES = Attributes(
    papers={
        OFZ: paper(OFZ, "OFZ", None, iss_type="ofz_bond", classes={}),
        CORP: paper(CORP, "CORP", GOOD),
        ORPHAN: paper(ORPHAN, "ORPHAN", None, iss_type=None, classes={}),
        STRANGER: paper(STRANGER, "STRANGER", NAMELESS, ratings=Rated("rated", {"acra": "BBB"})),
        SUB_BOND: paper(SUB_BOND, "SUB", LOW, classes={SENIORITY: SUB}),
        SILENT: paper(SILENT, "SILENT", QUIET),
    },
    issuers={
        GOOD: Issuer(GOOD, "Хороший", {}, Rated("rated", {"acra": "AAA", "moodys": "AAA"})),
        LOW: Issuer(LOW, "Низкий", {KIND: BANK}, Rated("rated", {"acra": "A", "nkr": "AA"})),
        QUIET: Issuer(QUIET, "Без рейтинга", {}, Rated("unknown")),
    },
    classifiers={
        SENIORITY: Classifier(
            SENIORITY, "Очерёдность", "instrument", {SENIOR: "старший", SUB: "субординированная"}
        ),
        KIND: Classifier(KIND, "Тип эмитента", "issuer", {BANK: "банк"}),
        NCC: Classifier(NCC, "НКЦ", "instrument", {YES: "да"}),
    },
    grades=("AAA", "AA", "A", "BBB"),
)


def figures(nav: str = "100", cash: str = "0") -> Figures:
    return Figures(
        cash=Decimal(cash),
        receivable=ZERO,
        payable=ZERO,
        claims=ZERO,
        obligations=ZERO,
        expenses=ZERO,
        fees_owed=ZERO,
        repo_placed=ZERO,
        repo_borrowed=ZERO,
        nav=Decimal(nav),
    )


def position(
    instrument_id: UUID, secid: str, value: str, quantity: int = 1, duration: str | None = "1"
) -> Position:
    return Position(
        instrument_id,
        secid,
        "bond",
        quantity,
        Decimal(value),
        None if duration is None else Decimal(duration),
    )


def cond(attribute: str, *values: Any, negate: bool = False) -> dict[str, Any]:
    return {"attribute": attribute, "values": [str(v) for v in values], "negate": negate}


def body(
    *checks: dict[str, Any], pick: str = "highest", rules: list[dict[str, Any]] | None = None
) -> SetBody:
    return SetBody.model_validate(
        {
            "document": {"name": "Политика"},
            "settings": {
                "ratings": {
                    "agencies": ["acra", "nkr"],
                    "pick": pick,
                    "groups": {"AAA": "AAA", "AA": "AA"},
                    "rules": rules or [{"filter": [], "source": "instrument_or_issuer"}],
                },
                "warn_at": "90",
            },
            "limits": [
                {"code": f"L{n}", "name": f"Лимит {n}", "checks": [c]} for n, c in enumerate(checks)
            ],
        }
    )


def run(
    set_body: SetBody, positions: list[Position], deals: list[Deal] | None = None, nav: str = "100"
) -> list[Any]:
    return evaluate(set_body, figures(nav), positions, deals or [], ATTRIBUTES).checks


def test_metrics_without_data() -> None:
    leverage = {"type": "metric", "metric": "leverage", "max": "4"}
    duration = {"type": "metric", "metric": "duration", "max": "2.5"}

    # СЧА не больше нуля — плеча нет; облигация без дюрации — «нет данных».
    lev, dur = run(body(leverage, duration), [position(CORP, "CORP", "50", duration=None)], nav="0")
    assert (lev.status, lev.value, lev.missing[0].reason) == (
        Status.NO_DATA,
        None,
        "СЧА не больше нуля",
    )
    assert (dur.status, dur.missing[0].secid) == (Status.NO_DATA, "CORP")
    # Активов нет — дюрации нет.
    [empty] = run(body(duration), [])
    assert (empty.value, empty.missing[0].reason) == (None, "активов нет")


def test_metric_below_its_minimum() -> None:
    [found] = run(
        body({"type": "metric", "metric": "duration", "min": "2"}), [position(CORP, "CORP", "50")]
    )
    assert (found.status, found.usage) == (Status.BREACH, None)


def test_share_with_missing_data() -> None:
    check = {
        "type": "share",
        "filter": [cond(f"class:{SENIORITY}", SENIOR)],
        "group": "issuer",
        "max": "100",
    }
    held = [
        position(CORP, "CORP", "5"),
        position(ORPHAN, "ORPHAN", "1"),  # очерёдность не задана
        position(uuid4(), "GHOST", "1"),  # бумаги нет в справочнике
    ]
    [found] = run(body(check), held)
    assert (found.status, found.value) == (Status.NO_DATA, Decimal("5") / 7 * 100)
    assert [(m.secid, m.reason) for m in found.missing] == [
        ("ORPHAN", "не задано «Очерёдность»"),
        ("GHOST", "бумаги нет в справочнике"),
    ]


def test_share_by_issuer_needs_the_issuer() -> None:
    check = {"type": "share", "filter": [cond("kind", "bond")], "group": "issuer", "max": "50"}
    [found] = run(
        body(check), [position(ORPHAN, "ORPHAN", "1"), position(STRANGER, "STRANGER", "1")]
    )
    # Эмитента нет в справочнике — в имени группы его код.
    assert [g.name for g in found.groups] == [str(NAMELESS)]
    assert found.missing[0].reason.startswith("эмитент неизвестен")


def test_share_without_assets() -> None:
    check = {"type": "share", "filter": [], "group": "all", "max": "10"}
    [found] = run(body(check), [position(CORP, "CORP", "-5", quantity=-1)])
    assert (found.value, found.status) == (ZERO, Status.OK)


def test_share_counts_the_day_deals() -> None:
    """Вклад сделок дня в группу: по нему журнал предлагает вид нарушения."""
    check = {
        "type": "share",
        "filter": [cond(f"class:{SENIORITY}", SENIOR)],
        "group": "issuer",
        "max": "50",
        "exclude_issuers": [str(QUIET)],
    }
    deals = [
        Deal(CORP, "CORP", "buy", Decimal(20)),
        Deal(CORP, "CORP", "sell", Decimal(5)),
        Deal(SILENT, "SILENT", "buy", Decimal(10)),  # эмитент исключён
        Deal(ORPHAN, "ORPHAN", "buy", Decimal(1)),  # эмитента нет — группы нет
        Deal(SUB_BOND, "SUB", "buy", Decimal(3)),  # не под фильтром
        Deal(uuid4(), "GHOST", "buy", Decimal(1)),  # бумаги нет в справочнике
    ]
    [found] = run(
        body(check), [position(CORP, "CORP", "60"), position(SILENT, "SILENT", "40")], deals
    )
    assert [(g.name, g.value, g.status, g.traded) for g in found.groups] == [
        ("Хороший", Decimal(60), Status.BREACH, Decimal(15))
    ]


def test_eligible_positions_and_deals() -> None:
    check = {
        "type": "eligible",
        "rules": [
            {"name": "ОФЗ", "filter": [cond("iss_type", "ofz_bond")], "shorts": True},
            {"name": "старший долг", "filter": [cond(f"class:{SENIORITY}", SENIOR)]},
        ],
    }
    held = [
        position(OFZ, "OFZ", "-3", quantity=-3),  # короткая ОФЗ — можно
        position(CORP, "CORP", "-1", quantity=-1),  # короткая корпоративная — нельзя
        position(SUB_BOND, "SUB", "1"),  # ни под одно правило
        position(ORPHAN, "ORPHAN", "1"),  # признаков нет
    ]
    deals = [
        Deal(SUB_BOND, "SUB", "buy"),
        Deal(uuid4(), "GHOST", "sell"),
        Deal(CORP, "CORP", "buy"),
    ]
    [found] = run(body(check), held, deals)

    assert (found.status, found.value) == (Status.BREACH, Decimal(3))
    assert [(v.secid, v.reason, v.by_deal) for v in found.violations] == [
        ("CORP", "короткая позиция: правило «старший долг» её не разрешает", False),
        ("SUB", "позиция не подходит ни под одно правило", False),
        ("SUB", "сделка с бумагой не по правилам", True),
    ]
    assert [(m.secid, m.reason) for m in found.missing] == [
        ("ORPHAN", "тип бумаги ISS неизвестен — сверьте бумагу с MOEX ISS"),
        ("ORPHAN", "не задано «Очерёдность»"),
        ("GHOST", "бумаги нет в справочнике"),
    ]


def test_purchase_condition() -> None:
    check = {
        "type": "purchase",
        "filter": [cond("kind", "bond")],
        "condition": [cond(f"class:{NCC}", YES), cond(f"class:{SENIORITY}", SUB, negate=True)],
    }
    deals = [
        Deal(CORP, "CORP", "buy"),  # выполнено
        Deal(SUB_BOND, "SUB", "buy"),  # НКЦ да, но субординированная
        Deal(ORPHAN, "ORPHAN", "buy"),  # признаков нет
        Deal(OFZ, "OFZ", "sell"),  # продажа — не покупка
        Deal(uuid4(), "GHOST", "buy"),
    ]
    [found] = run(body(check), [], deals)

    assert [(v.secid, v.reason, v.by_deal) for v in found.violations] == [
        ("SUB", "покупка: не выполнено «НКЦ»: да; «Очерёдность»: не субординированная", True)
    ]
    # У ORPHAN не задано ни «НКЦ», ни «Очерёдность».
    assert [m.secid for m in found.missing] == ["ORPHAN", "ORPHAN", "GHOST"]
    assert found.status is Status.BREACH


def test_purchase_filter_without_data() -> None:
    check = {
        "type": "purchase",
        "filter": [cond("iss_type", "exchange_bond")],
        "condition": [cond("kind", "bond")],
    }
    [found] = run(body(check), [], [Deal(ORPHAN, "ORPHAN", "buy"), Deal(OFZ, "OFZ", "buy")])
    assert (found.status, [m.secid for m in found.missing]) == (Status.NO_DATA, ["ORPHAN"])


@pytest.mark.parametrize(
    ("instrument", "rules", "pick", "group"),
    [
        # Выпуск без рейтинга — рейтинг эмитента; moodys не принимаем.
        (CORP, None, "highest", "AAA"),
        # Из двух агентств — наивысший или наинизший.
        (SUB_BOND, None, "highest", "AA"),
        (SUB_BOND, None, "lowest", "none"),
        # Принятых агентств нет — группы нет; ступени нет в шкале — не в счёт.
        (STRANGER, None, "highest", "none"),
        # Ни одно правило не подошло — группы нет.
        (CORP, [{"filter": [cond("kind", "fund_share")], "source": "issuer"}], "highest", "none"),
    ],
)
def test_rating_group(instrument: UUID, rules: Any, pick: str, group: str) -> None:
    check = {
        "type": "share",
        "filter": [cond("rating_group", group)],
        "group": "instrument",
        "max": "100",
    }
    [found] = run(body(check, pick=pick, rules=rules), [position(instrument, "X", "1")])
    assert [g.key for g in found.groups] == [str(instrument)]


@pytest.mark.parametrize(
    ("instrument", "rules", "reason"),
    [
        (
            ORPHAN,
            [{"filter": [], "source": "issuer"}],
            "эмитент неизвестен — сверьте бумагу с MOEX ISS",
        ),
        (
            ORPHAN,
            [{"filter": [cond(f"class:{SENIORITY}", SUB)], "source": "issuer"}],
            "не задано «Очерёдность»",
        ),
        (CORP, [{"filter": [], "source": "instrument"}], None),
        (
            STRANGER,
            [{"filter": [], "source": "issuer"}],
            "эмитент неизвестен — сверьте бумагу с MOEX ISS",
        ),
        (SILENT, [{"filter": [], "source": "issuer"}], "рейтинг эмитента не вводили"),
    ],
)
def test_rating_group_without_data(instrument: UUID, rules: Any, reason: str | None) -> None:
    check = {"type": "share", "filter": [cond("rating_group", "AAA")], "group": "all", "max": "100"}
    [found] = run(body(check, rules=rules), [position(instrument, "X", "1")])
    if reason is None:
        # Рейтинг выпуска «рейтинга нет» — группы нет, это не «нет данных».
        assert (found.status, found.groups) == (Status.OK, [])
    else:
        assert [m.reason for m in found.missing] == [reason]


def test_unknown_issue_rating_is_missing_data() -> None:
    unknown = Attributes(
        papers={CORP: paper(CORP, "CORP", GOOD, ratings=Rated("unknown"))},
        issuers=ATTRIBUTES.issuers,
        classifiers=ATTRIBUTES.classifiers,
        grades=ATTRIBUTES.grades,
    )
    check = {"type": "share", "filter": [cond("rating_group", "AAA")], "group": "all", "max": "100"}
    [found] = evaluate(body(check), figures(), [position(CORP, "CORP", "1")], [], unknown).checks
    assert [m.reason for m in found.missing] == ["рейтинг выпуска не вводили"]


def test_issuer_classes() -> None:
    bank = f"class:{KIND}"
    check = {"type": "share", "filter": [cond(bank, BANK)], "group": "issuer", "max": "100"}
    held = [position(SUB_BOND, "SUB", "1"), position(CORP, "CORP", "1"), position(OFZ, "OFZ", "1")]
    [found] = run(body(check), held)
    assert [g.name for g in found.groups] == ["Низкий"]
    assert [(m.secid, m.reason) for m in found.missing] == [
        ("CORP", "у эмитента не задано «Тип эмитента»"),
        ("OFZ", "эмитент неизвестен — сверьте бумагу с MOEX ISS"),
    ]


def test_unknown_classifier_and_plain_facts() -> None:
    lost = uuid4()
    check = {
        "type": "purchase",
        "filter": [
            cond("coupon_kind", "fixed"),
            cond("currency", "RUB"),
            cond("instrument", SUB_BOND),
            cond("issuer", LOW),
        ],
        "condition": [cond(f"class:{lost}", "x")],
    }
    [found] = run(body(check), [], [Deal(SUB_BOND, "SUB", "buy")])
    assert found.missing[0].reason == "классификатора набора нет в справочнике"


def test_describe_names_attributes_and_values() -> None:
    lost, other = uuid4(), uuid4()
    check = {
        "type": "purchase",
        "filter": [],
        "condition": [
            cond("iss_type", "ofz_bond"),
            cond(f"class:{SENIORITY}", other),
            cond(f"class:{lost}", "x", negate=True),
        ],
    }
    # Условие не выполнено на первом же признаке, описание — целиком.
    [found] = run(body(check), [], [Deal(CORP, "CORP", "buy")])
    assert found.violations[0].reason == (
        f"покупка: не выполнено тип ISS: ofz_bond; «Очерёдность»: {other}; class:{lost}: не x"
    )


def test_warning_and_missing_data_order() -> None:
    check = {
        "type": "share",
        "filter": [cond("iss_type", "exchange_bond")],
        "group": "all",
        "max": "10",
    }
    held = [position(CORP, "CORP", "9.5"), position(OFZ, "OFZ", "90")]

    # 9,5% из 10% — предупреждение.
    [warning] = run(body(check), [*held, position(OFZ, "OFZ", "0.5")])
    assert (warning.status, warning.usage) == (Status.WARNING, Decimal(95))
    # То же, но у одной бумаги нет типа ISS: «нет данных» важнее предупреждения.
    [no_data] = run(body(check), [*held, position(ORPHAN, "ORPHAN", "0.5")])
    assert (no_data.status, no_data.usage) == (Status.NO_DATA, Decimal(95))
