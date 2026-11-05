"""Лимиты через API: набор политики «Доход 2» и неделя примера методики (design/09)."""

from __future__ import annotations

import json
import time
from datetime import date
from types import ModuleType
from typing import TYPE_CHECKING, Any

import pytest
import redis
from fastapi.testclient import TestClient

from am_common.migrations import schema_diff
from am_common.testing import TokenFactory
from limits.db import metadata
from limits.settings import LimitsSettings

if TYPE_CHECKING:
    from conftest import Api, World

pytestmark = pytest.mark.integration


def save(api: Api, model: ModuleType, **changes: Any) -> Any:
    body = {"valid_from": "2026-11-01", "comment": "Политика версии 1.0", "body": model.model_set()}
    return api.ok("POST", f"/funds/{model.FUND}/sets", body | changes)


def table(found: dict[str, Any]) -> dict[str, tuple[Any, ...]]:
    """Использование: «Л3 на эмитента» → значение, использовано, статус, самая большая группа."""
    return {
        f"{c['code']} {c['label'] or ''}".strip(): (
            c["value"],
            c["usage"],
            c["status"],
            c["groups"][0]["name"] if c["groups"] else None,
        )
        for c in found["checks"]
    }


async def test_migrations_match_the_model(settings: LimitsSettings) -> None:
    assert settings.database_url is not None
    assert settings.database_schema is not None
    diff = await schema_diff(
        settings.database_url.get_secret_value(), settings.database_schema, metadata
    )
    assert diff == []


def test_usage_on_10_11_from_the_methodology(risk: Api, trader: Api, model: ModuleType) -> None:
    saved = save(risk, model)
    assert (saved["version"], saved["author"]) == (1, "risk")

    found = trader.ok("GET", f"/funds/{model.FUND}/days/2026-11-10")

    assert (found["fund"]["code"], found["today"], found["calculated"]) == ("DOHOD2", False, True)
    assert found["limit_set"]["version"] == 1
    assert found["totals"] == {
        "assets": "2800000000.00",
        "liabilities": "1800000000.00",
        "nav": "1000000000.00",
        "leverage": "2.8000",
        # 1 209,25 / 2 800 = 0,43 года.
        "duration": "0.4319",
    }
    assert [c["metric"] for c in found["checks"][:4]] == ["leverage", "duration", "duration", None]
    # Таблица «Использование в примере, 10.11»: значение, % лимита, статус.
    assert table(found) == {
        "Л1": ("2.8000", "70.00", "ok", None),
        "Л2": ("0.4319", "17.28", "ok", None),
        "7.3": ("0.4319", None, "ok", None),
        "Л3 на класс": ("51.6071", "51.61", "ok", "все бумаги проверки"),
        "Л3 на эмитента": ("9.6429", "96.43", "warning", "Эмитент А"),
        "Л4 на класс": ("2.1429", "4.29", "ok", "все бумаги проверки"),
        "Л4 на эмитента": ("2.1429", "85.71", "ok", "Эмитент В"),
        "Л5 на класс": ("9.2857", "46.43", "ok", "все бумаги проверки"),
        "Л5 на выпуск": ("2.3214", "92.86", "warning", "ICB-1"),
        "Л6 на класс": ("2.1429", "21.43", "ok", "все бумаги проверки"),
        "Л6 на эмитента": ("2.1429", "85.71", "ok", "Банк Д"),
        "Л7 на эмитента": ("9.2857", "92.86", "warning", "Банк Д"),
        "6.1 на класс": ("0.0000", None, "ok", None),
        "4": ("0.0000", None, "ok", None),
        "4.3": ("0.0000", None, "ok", None),
    }
    # ВЭБ.РФ — 14,29% активов, но лимиты на эмитента к нему не применяются (п. 6.5).
    l3 = next(c for c in found["checks"] if c["code"] == "Л3" and c["label"] == "на эмитента")
    assert [g["name"] for g in l3["groups"]] == [
        "Эмитент А",
        "Эмитент Б",
        "Эмитент Е",
        "Банк Д",
        "Эмитент Г",
        "Эмитент В",
    ]


def test_week_of_the_methodology(risk: Api, world: World, model: ModuleType) -> None:
    save(risk, model)
    week = {}
    # 13.11: управляющий купил облигаций Эмитента А на 15,0 за деньги.
    world.changes[date(2026, 11, 13)] = {"A-FLOAT": ("285", "0.05")}
    world.cash[date(2026, 11, 13)] = "10"
    world.deals[date(2026, 11, 13)] = [model.deal("A-FLOAT")]
    # 17.11: продал облигации Эмитента Г на 6,0.
    world.changes[date(2026, 11, 17)] = {"G-FLOAT": ("69", "0.05")}
    world.cash[date(2026, 11, 17)] = "31"
    for day in ("2026-11-11", "2026-11-12", "2026-11-13", "2026-11-17"):
        week[day] = table(risk.ok("GET", f"/funds/{model.FUND}/days/{day}"))

    # 11.11 АКРА понизило Эмитента Г, но Эксперт РА держит AA+: наивысший — AA+.
    assert week["2026-11-11"]["Л4 на эмитента"] == ("2.1429", "85.71", "ok", "Эмитент В")
    # 12.11 — понизило и Эксперт РА: Эмитент Г в группе AA, 2,68% при лимите 2,5%.
    assert week["2026-11-12"]["Л4 на эмитента"] == ("2.6786", "107.14", "breach", "Эмитент Г")
    assert week["2026-11-12"]["Л3 на класс"][0] == "48.9286"
    assert week["2026-11-12"]["Л4 на класс"][0] == "4.8214"
    # 13.11 — покупка Эмитента А: 10,18% при лимите 10%; НКЦ облигацию принимает.
    assert week["2026-11-13"]["Л3 на эмитента"] == ("10.1786", "101.79", "breach", "Эмитент А")
    assert week["2026-11-13"]["4.3"] == ("0.0000", None, "ok", None)
    # 17.11 — после продажи Эмитент Г 2,46%: в пределах, но близко к лимиту.
    assert week["2026-11-17"]["Л4 на эмитента"] == ("2.4643", "98.57", "warning", "Эмитент Г")


def test_today_and_days_without_a_set_or_a_calculation(
    risk: Api, world: World, model: ModuleType
) -> None:
    before = risk.ok("GET", f"/funds/{model.FUND}/days/2026-10-30")
    assert (before["limit_set"], before["calculated"], before["checks"]) == (None, False, [])

    save(risk, model)
    world.not_calculated.add(date(2026, 11, 18))
    future = risk.ok("GET", f"/funds/{model.FUND}/days/2026-11-18")
    assert (future["limit_set"]["version"], future["calculated"], future["totals"]) == (
        1,
        False,
        None,
    )
    # Сегодня — по текущим ценам, как СЧА.
    assert risk.ok("GET", f"/funds/{model.FUND}/days/2026-11-17")["today"] is True


def test_versions_of_a_set(risk: Api, trader: Api, model: ModuleType) -> None:
    save(risk, model)
    second = model.model_set()
    second["settings"]["warn_at"] = "95"
    saved = save(risk, model, valid_from="2026-11-12", body=second, comment="Порог 95%")

    assert saved["version"] == 2
    versions = trader.ok("GET", f"/funds/{model.FUND}/sets")
    assert [
        (v["version"], v["valid_from"], v["body"]["settings"]["warn_at"]) for v in versions
    ] == [
        (1, "2026-11-01", "90"),
        (2, "2026-11-12", "95"),
    ]
    # День проверяется по версии, действующей на этот день.
    old = trader.ok("GET", f"/funds/{model.FUND}/days/2026-11-11")
    new = trader.ok("GET", f"/funds/{model.FUND}/days/2026-11-13")
    assert (old["limit_set"]["version"], new["limit_set"]["version"]) == (1, 2)
    assert table(old)["Л5 на выпуск"][2] == "warning"
    assert table(new)["Л5 на выпуск"][2] == "ok"
    # Новая версия — не раньше действующей.
    assert risk.refused(
        "POST",
        f"/funds/{model.FUND}/sets",
        {"valid_from": "2026-11-11", "comment": "Задним числом", "body": second},
    ) == (422, "valid_from_too_early")


def test_who_may_do_what(
    risk: Api, trader: Api, client: TestClient, tokens: TokenFactory, model: ModuleType
) -> None:
    body = {"valid_from": "2026-11-01", "comment": "Политика", "body": model.model_set()}
    path = f"/funds/{model.FUND}/sets"
    assert trader.call("POST", path, body).status_code == 403
    assert model.Api(client, tokens, "price_feed").call("GET", path).status_code == 403
    assert [f["code"] for f in trader.ok("GET", "/funds")] == ["DOHOD2"]
    assert [f["code"] for f in risk.ok("GET", "/funds")] == ["DOHOD2", "OPIF1"]
    # Второй фонд трейдер не видит; портфель — не фонд.
    assert trader.refused("GET", f"/funds/{model.OTHER_FUND}/sets") == (404, "not_found")
    assert risk.refused("GET", f"/funds/{model.BONDS}/days/2026-11-10") == (404, "not_found")


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda b: b["limits"].append(b["limits"][0]), "duplicate_code"),
        (lambda b: b["settings"]["ratings"]["agencies"].append("moodys"), "unknown_agency"),
        (lambda b: b["settings"]["ratings"]["groups"].update({"Aa3": "AA"}), "unknown_grade"),
        (
            lambda b: b["settings"]["ratings"]["rules"][0]["filter"].append(
                {"attribute": "rating_group", "values": ["AAA"]}
            ),
            "rating_group_in_rating_rule",
        ),
        (
            lambda b: b["limits"][3]["checks"][0]["filter"].append(
                {"attribute": "class:00000000-0000-0000-0000-000000000000", "values": ["x"]}
            ),
            "unknown_classifier",
        ),
        (
            lambda b: b["limits"][3]["checks"][0]["filter"].append(
                {
                    "attribute": b["limits"][3]["checks"][0]["filter"][2]["attribute"],
                    "values": ["x"],
                }
            ),
            "unknown_value",
        ),
        (
            lambda b: b["limits"][3]["checks"][0]["filter"].append(
                {"attribute": "issuer", "values": ["00000000-0000-0000-0000-000000000000"]}
            ),
            "unknown_issuer",
        ),
        (
            lambda b: b["limits"][3]["checks"][1].update(
                exclude_issuers=["00000000-0000-0000-0000-000000000000"]
            ),
            "unknown_issuer",
        ),
        (
            lambda b: b["limits"][9]["checks"][0]["rules"][0]["filter"][0].update(values=["x"]),
            "unknown_instrument",
        ),
        (
            lambda b: b["limits"][3]["checks"][0]["filter"][4].update(values=["BBB"]),
            "unknown_group",
        ),
    ],
)
def test_set_references_the_reference_data(
    risk: Api, model: ModuleType, change: Any, code: str
) -> None:
    body = model.model_set()
    change(body)
    assert risk.refused(
        "POST",
        f"/funds/{model.FUND}/sets",
        {"valid_from": "2026-11-01", "comment": "Политика", "body": body},
    ) == (422, code)


@pytest.mark.parametrize(
    "change",
    [
        lambda b: b["limits"][0]["checks"][0].pop("max"),  # порога нет
        lambda b: b["limits"].clear(),
        lambda b: b["limits"][3]["checks"][0]["filter"].append(
            {"attribute": "colour", "values": ["x"]}
        ),
        lambda b: b["limits"][3]["checks"][0].update(max="101"),
        lambda b: b["limits"][3]["checks"][0].update(type="unknown"),
    ],
)
def test_malformed_sets(risk: Api, model: ModuleType, change: Any) -> None:
    body = model.model_set()
    change(body)
    assert risk.refused(
        "POST",
        f"/funds/{model.FUND}/sets",
        {"valid_from": "2026-11-01", "comment": "Политика", "body": body},
    ) == (422, "invalid")


@pytest.mark.parametrize(
    ("service", "code"),
    [
        ("portfolio", "portfolio_unavailable"),
        ("booking", "booking_unavailable"),
        ("refdata", "refdata_unavailable"),
    ],
)
def test_neighbours_down(
    risk: Api, world: World, model: ModuleType, service: str, code: str
) -> None:
    save(risk, model)
    world.down.add(service)
    found = risk.call("GET", f"/funds/{model.FUND}/days/2026-11-10")
    assert (found.status_code, found.json()["detail"]["code"]) == (503, code)
    assert found.json()["detail"]["message"].endswith("недоступен. Повторите позже.")


def test_neighbour_error_is_unavailable(risk: Api, world: World, model: ModuleType) -> None:
    save(risk, model)
    world.failing.add("booking")
    assert risk.refused("GET", f"/funds/{model.FUND}/days/2026-11-10") == (
        503,
        "booking_unavailable",
    )


def test_neighbour_refusal_is_not_an_outage(risk: Api, world: World, model: ModuleType) -> None:
    save(risk, model)
    # Сервис сделок не видит у риск-менеджмента доступа к фонду — так и говорим.
    world.hidden["risk"] = {str(model.FUND)}
    world.visible_funds = True
    assert risk.refused("GET", f"/funds/{model.FUND}/days/2026-11-10") == (403, "booking_refused")


@pytest.fixture
def stream(settings: LimitsSettings) -> redis.Redis:
    assert settings.redis_url is not None
    connection = redis.Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
    connection.delete("am.events.limits")
    return connection


def test_saving_goes_to_the_journal(stream: redis.Redis, risk: Api, model: ModuleType) -> None:
    save(risk, model)
    save(risk, model, valid_from="2026-11-12", comment="Уточнили пороги")

    events: list[dict[str, Any]] = []
    for _ in range(100):
        events = [json.loads(f["event"]) for _id, f in stream.xrange("am.events.limits")]  # type: ignore[union-attr, index]
        if len(events) >= 2:
            break
        time.sleep(0.05)
    stream.close()

    first, second = events
    assert (first["type"], first["entity_type"], first["data"]["fund"]) == (
        "limit_set.saved",
        "limit_set",
        {"code": "DOHOD2"},
    )
    assert first["data"]["before"] is None
    assert second["data"]["before"]["version"] == 1
    assert second["data"]["after"] == {
        "version": 2,
        "valid_from": "2026-11-12",
        "document": "Инвестиционная политика ИПИФ «Доход 2» 1.0",
        "limits": "Л1, Л2, 7.3, Л3, Л4, Л5, Л6, Л7, 6.1, 4, 4.3",
    }
    assert second["comment"] == "Уточнили пороги"
