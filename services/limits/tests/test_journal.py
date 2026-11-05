"""Журнал нарушений через API: неделя примера методики, вид, сроки, продление, отчёт (design/09)."""

from __future__ import annotations

import json
import time
from datetime import date, datetime
from types import ModuleType
from typing import TYPE_CHECKING, Any

import pytest
import redis

from am_common.clock import MOSCOW
from limits.settings import LimitsSettings

if TYPE_CHECKING:
    from conftest import Api, World

pytestmark = pytest.mark.integration

NOVEMBER = [date(2026, 11, d) for d in range(1, 31)]


@pytest.fixture
def week(world: World, model: ModuleType) -> World:
    """Неделя примера: 13.11 покупка Эмитента А на 15,0, 16.11 продажа, 17.11 продажа Г на 6,0."""
    world.not_calculated |= {d for d in NOVEMBER if d.weekday() >= model.SATURDAY}
    world.changes[date(2026, 11, 13)] = {"A-FLOAT": ("285", "0.05")}
    world.cash[date(2026, 11, 13)] = "10"
    world.deals[date(2026, 11, 13)] = [model.deal("A-FLOAT")]
    world.deals[date(2026, 11, 16)] = [model.deal("A-FLOAT", "sell")]
    world.changes[date(2026, 11, 17)] = {"G-FLOAT": ("69", "0.05")}
    world.cash[date(2026, 11, 17)] = "31"
    world.deals[date(2026, 11, 17)] = [model.deal("G-FLOAT", "sell", "6")]
    return world


def at(world: World, moment: str) -> None:
    world.now = datetime.fromisoformat(moment).replace(tzinfo=MOSCOW)


def save(api: Api, model: ModuleType, fund: Any = None) -> None:
    body = {"valid_from": "2026-11-01", "comment": "Политика версии 1.0", "body": model.model_set()}
    api.ok("POST", f"/funds/{fund or model.FUND}/sets", body)


def check(api: Api, model: ModuleType, day: str) -> dict[str, Any]:
    return api.ok("GET", f"/funds/{model.FUND}/days/{day}")  # type: ignore[no-any-return]


def journal(api: Api, model: ModuleType, status: str | None = None) -> list[dict[str, Any]]:
    query = "" if status is None else f"&status={status}"
    return api.ok("GET", f"/breaches?fund_id={model.FUND}{query}")  # type: ignore[no-any-return]


def brief(found: dict[str, Any]) -> tuple[Any, ...]:
    """Строка журнала: лимит, группа, день, выявлено, вид, срок, статус, устранено."""
    return (
        f"{found['limit_code']} {found['label']}".strip(),
        found["name"],
        found["first_day"],
        found["detected_on"],
        found["kind"],
        found["deadline"],
        found["state"],
        found["eliminated_on"],
    )


def by_name(found: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(b for b in found if b["name"] == name)


G_OPEN = ("Л4 на эмитента", "Эмитент Г", "2026-11-12", "2026-11-12", "passive", "2026-12-10")
A_OPEN = ("Л3 на эмитента", "Эмитент А", "2026-11-13", "2026-11-13", "active", "2026-11-16")


def test_week_of_the_methodology(
    stream: redis.Redis, risk: Api, trader: Api, week: World, model: ModuleType
) -> None:
    at(week, "2026-11-13T10:00")
    save(risk, model)

    # Утром 13.11 проверяется 12.11 — и все дни месяца до него, которые никто не открыл.
    day = check(trader, model, "2026-11-12")
    assert (day["open_breaches"], day["overdue_breaches"]) == (1, 0)
    [g] = journal(trader, model)
    # Сделок с Эмитентом Г не было — пассивное, 20 рабочих дней: до 10.12.2026.
    assert brief(g) == (*G_OPEN, "open", None)
    assert (g["value"], g["maximum"], g["suggested"], g["kind_by"]) == (
        "2.6786",
        "2.5",
        "passive",
        None,
    )
    assert g["fund"]["code"] == "DOHOD2"

    # 13.11 куплен Эмитент А: без сделки было 9,64% — активное, срок — следующий рабочий день.
    at(week, "2026-11-16T10:00")
    assert check(trader, model, "2026-11-13")["open_breaches"] == 2
    assert [brief(b) for b in journal(trader, model)] == [
        (*A_OPEN, "open", None),
        (*G_OPEN, "open", None),
    ]

    # 16.11 продал — устранено 16.11; выходные без расчёта пропущены.
    at(week, "2026-11-17T10:00")
    assert check(trader, model, "2026-11-16")["open_breaches"] == 1
    # 17.11 продал Эмитента Г: 2,46% — устранено 17.11.
    at(week, "2026-11-18T10:00")
    assert check(trader, model, "2026-11-17")["open_breaches"] == 0
    assert [brief(b) for b in journal(trader, model)] == [
        (*A_OPEN, "eliminated", "2026-11-16"),
        (*G_OPEN, "eliminated", "2026-11-17"),
    ]
    assert journal(trader, model, "open") == []
    assert len(journal(trader, model, "eliminated")) == 2
    # Устранённое не продлевают.
    g_id = journal(trader, model)[1]["id"]
    assert risk.refused(
        "POST",
        f"/breaches/{g_id}/extension",
        {"deadline": "2027-01-15", "decision": "№ 5 от 20.11.2026", "comment": "Продление"},
    ) == (422, "breach_eliminated")

    events = read_events(stream, 4)
    assert [(e["type"], e["actor"]["username"], e["data"]["breach"]["name"]) for e in events] == [
        ("breach.detected", "system", "Эмитент Г"),
        ("breach.detected", "system", "Эмитент А"),
        ("breach.eliminated", "system", "Эмитент А"),
        ("breach.eliminated", "system", "Эмитент Г"),
    ]
    assert events[0]["data"]["breach"] == {
        "limit": "Л4, на эмитента",
        "name": "Эмитент Г",
        "first_day": "2026-11-12",
    }
    assert events[0]["data"]["after"] == {
        "measured": "2.678571",
        "breach_kind": "passive",
        "deadline": "2026-12-10",
    }


def test_not_eliminated_in_time_is_overdue(
    risk: Api, trader: Api, week: World, model: ModuleType
) -> None:
    week.deals.pop(date(2026, 11, 16))
    week.changes[date(2026, 11, 16)] = week.changes[date(2026, 11, 13)]
    week.cash[date(2026, 11, 16)] = "10"
    at(week, "2026-11-13T10:00")
    save(risk, model)
    check(trader, model, "2026-11-12")
    at(week, "2026-11-16T10:00")
    check(trader, model, "2026-11-13")

    # 17.11 позиция Эмитента А всё ещё 10,18%, а срок был 16.11.
    at(week, "2026-11-17T10:00")
    day = check(trader, model, "2026-11-16")
    assert (day["open_breaches"], day["overdue_breaches"]) == (2, 1)
    assert [b["name"] for b in journal(trader, model, "overdue")] == ["Эмитент А"]
    assert [b["state"] for b in journal(trader, model, "open")] == ["overdue", "open"]


def test_detected_late(risk: Api, trader: Api, week: World, model: ModuleType) -> None:
    # Рейтинг Эмитента Г за 12.11 ввели только днём 13.11: утром Л4 — «нет данных».
    week.blank[date(2026, 11, 12)] = frozenset({model.ISSUER_G})
    at(week, "2026-11-13T10:00")
    save(risk, model)
    assert check(trader, model, "2026-11-12")["open_breaches"] == 0

    week.blank.clear()
    at(week, "2026-11-13T15:00")
    check(trader, model, "2026-11-12")
    # Нарушение от 12.11, выявлено 13.11 — срок считается с 13.11.
    [g] = journal(trader, model)
    assert brief(g)[2:6] == ("2026-11-12", "2026-11-13", "passive", "2026-12-11")
    # Повторная проверка того же дня журнал не меняет.
    check(trader, model, "2026-11-12")
    assert journal(trader, model) == [g]

    # 13.11 рейтинга Г снова нет: «нет данных» — ещё не «устранено».
    week.blank[date(2026, 11, 13)] = frozenset({model.ISSUER_G})
    at(week, "2026-11-16T10:00")
    check(trader, model, "2026-11-13")
    g = by_name(journal(trader, model), "Эмитент Г")
    assert (g["state"], g["last_day"]) == ("open", "2026-11-12")

    # 18.11 проверены и пропущенные 16.11 и 17.11: оба нарушения устранены.
    at(week, "2026-11-18T10:00")
    check(trader, model, "2026-11-17")
    assert [(b["name"], b["eliminated_on"]) for b in journal(trader, model)] == [
        ("Эмитент А", "2026-11-16"),
        ("Эмитент Г", "2026-11-17"),
    ]


def test_first_look_a_week_later(risk: Api, trader: Api, week: World, model: ModuleType) -> None:
    at(week, "2026-11-01T10:00")
    save(risk, model)
    # Неделю никто не открывал лимиты: 18.11 проверены все пропущенные дни — выявлено 18.11.
    at(week, "2026-11-18T10:00")
    assert check(trader, model, "2026-11-13")["open_breaches"] == 2
    assert [brief(b)[2:6] for b in journal(trader, model)] == [
        ("2026-11-13", "2026-11-18", "active", "2026-11-19"),
        ("2026-11-12", "2026-11-18", "passive", "2026-12-16"),
    ]


def test_a_deal_that_grows_the_breach(
    risk: Api, trader: Api, week: World, model: ModuleType
) -> None:
    # 13.11, при открытом нарушении по Эмитенту Г, докупили его облигаций на 3,0.
    week.changes[date(2026, 11, 13)] = {"G-FLOAT": ("78", "0.05")}
    week.cash[date(2026, 11, 13)] = "22"
    week.deals[date(2026, 11, 13)] = [model.deal("G-FLOAT", amount="3")]
    at(week, "2026-11-13T10:00")
    save(risk, model)
    check(trader, model, "2026-11-12")
    at(week, "2026-11-16T10:00")
    check(trader, model, "2026-11-13")

    [g] = journal(trader, model)
    assert (g["value"], g["peak"], g["last_day"], g["kind"]) == (
        "2.7857",
        "2.7857",
        "2026-11-13",
        "passive",
    )
    assert [(n["by"], n["text"]) for n in g["notes"]] == [
        ("system", "нарушение увеличено сделкой 13.11.2026")
    ]


def test_review_and_extension(
    stream: redis.Redis, risk: Api, trader: Api, week: World, model: ModuleType
) -> None:
    at(week, "2026-11-13T10:00")
    save(risk, model)
    check(trader, model, "2026-11-12")
    [g] = journal(trader, model)
    path = f"/breaches/{g['id']}"

    review = {"kind": "active", "comment": "Покупка 11.11 вошла в расчёт 12.11"}
    assert trader.call("POST", f"{path}/review", review).status_code == 403
    found = risk.ok("POST", f"{path}/review", review)
    # Активное — срок до конца следующего рабочего дня после выявления.
    assert (found["kind"], found["suggested"], found["kind_by"], found["deadline"]) == (
        "active",
        "passive",
        "risk",
        "2026-11-13",
    )
    assert [(n["by"], n["kind"]) for n in found["notes"]] == [("risk", "active")]
    extension = {"deadline": "2027-01-29", "decision": "№ 5 от 20.11.2026", "comment": "Рынок"}
    assert risk.refused("POST", f"{path}/extension", extension) == (422, "active_not_extended")

    found = risk.ok(
        "POST", f"{path}/review", review | {"kind": "passive", "comment": "Сделок не было"}
    )
    assert found["deadline"] == "2026-12-10"
    # Не позже прежнего срока и не позже трёх месяцев с выявления.
    for deadline in ("2026-12-10", "2027-02-13"):
        assert risk.refused("POST", f"{path}/extension", extension | {"deadline": deadline}) == (
            422,
            "bad_deadline",
        )
    found = risk.ok("POST", f"{path}/extension", extension)
    assert found["deadline"] == "2027-01-29"
    assert [(e["deadline"], e["decision"], e["by"]) for e in found["extensions"]] == [
        ("2027-01-29", "№ 5 от 20.11.2026", "risk")
    ]
    # Продлённый срок вид не меняет; тот же вид — срок тот же.
    found = risk.ok("POST", f"{path}/review", review)
    assert (found["kind"], found["deadline"]) == ("active", "2027-01-29")
    found = risk.ok("POST", f"{path}/review", review | {"comment": "Подтверждаю"})
    assert len(found["notes"]) == 4

    events = read_events(stream, 6)
    reviewed, extended = events[1], events[3]
    assert (reviewed["type"], reviewed["actor"]["username"], reviewed["comment"]) == (
        "breach.reviewed",
        "risk",
        "Покупка 11.11 вошла в расчёт 12.11",
    )
    assert reviewed["data"]["before"] == {"breach_kind": "passive", "deadline": "2026-12-10"}
    assert reviewed["data"]["after"] == {"breach_kind": "active", "deadline": "2026-11-13"}
    assert (extended["type"], extended["data"]["after"]) == (
        "breach.extended",
        {"deadline": "2027-01-29", "decision": "№ 5 от 20.11.2026"},
    )


def test_who_sees_and_changes_breaches(
    risk: Api, trader: Api, week: World, model: ModuleType
) -> None:
    at(week, "2026-11-13T10:00")
    save(risk, model, model.OTHER_FUND)
    risk.ok("GET", f"/funds/{model.OTHER_FUND}/days/2026-11-12")
    [found] = risk.ok("GET", f"/breaches?fund_id={model.OTHER_FUND}")

    # Второй фонд трейдер не видит — ни журнал, ни нарушение.
    assert trader.refused("GET", f"/breaches?fund_id={model.OTHER_FUND}") == (404, "not_found")
    unknown = "00000000-0000-0000-0000-000000000000"
    review = {"kind": "passive", "comment": "Подтверждаю"}
    assert risk.refused("POST", f"/breaches/{unknown}/review", review) == (404, "not_found")
    assert risk.refused("POST", f"/breaches/{found['id']}/review", {"kind": "x"})[0] == 422
    assert risk.refused("GET", f"/breaches?fund_id={model.FUND}&status=late")[0] == 422


def test_monthly_report(risk: Api, trader: Api, week: World, model: ModuleType) -> None:
    at(week, "2026-11-13T10:00")
    save(risk, model)
    check(trader, model, "2026-11-12")
    at(week, "2026-11-18T10:00")

    report = trader.ok("GET", f"/funds/{model.FUND}/reports/2026-11")
    # Месяц идёт — использование на вчера; нарушения — открытые хотя бы день месяца.
    assert (report["month"], report["day"]["date"], report["day"]["calculated"]) == (
        "2026-11",
        "2026-11-17",
        True,
    )
    assert [(b["name"], b["state"]) for b in report["breaches"]] == [
        ("Эмитент А", "eliminated"),
        ("Эмитент Г", "eliminated"),
    ]
    # Октябрь — набора ещё не было.
    october = trader.ok("GET", f"/funds/{model.FUND}/reports/2026-10")
    assert (october["day"]["date"], october["day"]["limit_set"], october["breaches"]) == (
        "2026-10-31",
        None,
        [],
    )
    assert trader.refused("GET", f"/funds/{model.FUND}/reports/2026-12") == (
        422,
        "month_not_started",
    )
    assert trader.refused("GET", f"/funds/{model.FUND}/reports/2026-13") == (422, "bad_month")
    assert trader.refused("GET", f"/funds/{model.FUND}/reports/ноябрь")[0] == 422


@pytest.fixture
def stream(settings: LimitsSettings) -> redis.Redis:
    assert settings.redis_url is not None
    connection = redis.Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
    connection.delete("am.events.limits")
    return connection


def read_events(stream: redis.Redis, count: int) -> list[dict[str, Any]]:
    """События нарушений из журнала действий — без сохранения набора."""
    events: list[dict[str, Any]] = []
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        found = [json.loads(f["event"]) for _id, f in stream.xrange("am.events.limits")]  # type: ignore[union-attr, index]
        events = [e for e in found if e["entity_type"] == "breach"]
        if len(events) >= count:
            break
        time.sleep(0.05)
    stream.close()
    return events
