"""Эмитенты, рейтинги и классификаторы через API (design/09, часть 9.1).

Пример методики: Эмитент Г теряет AA+ у двух агентств по очереди — 11.11 АКРА,
12.11 Эксперт РА. Роль Эмитента Г играет Газпром нефть — эмитент записанной в ISS
облигации 003P-08R; рейтинги условные. Часы приложения стоят на 17.11.2026.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import update

from am_common.clock import MOSCOW
from am_common.db import Database
from am_common.testing import TokenFactory
from refdata.db import instrument
from refdata.main import build_app
from refdata.settings import RefdataSettings

if TYPE_CHECKING:
    from conftest import FakeIss

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=MOSCOW)
UNKNOWN = "00000000-0000-0000-0000-000000000000"


class Api:
    def __init__(self, client: TestClient, tokens: TokenFactory, role: str) -> None:
        self._client = client
        token = tokens.issue(subject=f"u-{role}", username=role, roles=[role])
        self._headers = {"Authorization": f"Bearer {token}"}

    def call(
        self, method: str, path: str, body: dict[str, Any] | None = None, **params: Any
    ) -> Any:
        return self._client.request(method, path, json=body, params=params, headers=self._headers)

    def ok(self, method: str, path: str, body: dict[str, Any] | None = None, **params: Any) -> Any:
        response = self.call(method, path, body, **params)
        assert response.is_success, response.text
        return response.json()

    def refused(
        self, method: str, path: str, body: dict[str, Any] | None = None, **params: Any
    ) -> tuple[int, str]:
        response = self.call(method, path, body, **params)
        assert response.is_error, response.text
        detail = response.json()["detail"]
        return response.status_code, detail["code"] if isinstance(detail, dict) else "invalid"


@pytest.fixture
def client(settings: RefdataSettings, iss: FakeIss) -> Iterator[TestClient]:
    app = build_app(settings, http_transport=iss.transport(), clock=lambda: NOW)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def trader(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "trading")


@pytest.fixture
def ops(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "operations")


@pytest.fixture
def risk(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "risk")


def load(api: Api, code: str) -> dict[str, Any]:
    found: dict[str, Any] = api.ok("POST", "/instruments", {"code": code})
    return found


def rate(api: Api, subject: dict[str, str], **fields: Any) -> dict[str, Any]:
    found: dict[str, Any] = api.ok("POST", "/ratings", {**subject, "action": "assigned", **fields})
    return found


def agencies(found: dict[str, Any]) -> dict[str, str]:
    """Действующие рейтинги: агентство → запись агентства."""
    return {r["agency"]: r["rating"] for r in found["ratings"]}


# --- эмитенты ------------------------------------------------------------------


def test_issuers_come_from_iss(trader: Api) -> None:
    ofz = load(trader, "SU26238RMFS4")
    load(trader, "SU29014RMFS6")
    load(trader, "LQDT")

    assert (ofz["issuer"]["name"], ofz["iss_type"]) == (
        "Министерство финансов Российской Федерации",
        "ofz_bond",
    )
    listed = trader.ok("GET", "/issuers")
    assert [(i["name"], i["instruments"]) for i in listed] == [
        ("Акционерное общество ВИМ Инвестиции", 1),
        ("Министерство финансов Российской Федерации", 2),
    ]
    # Поиск — по части названия или по ИНН целиком.
    assert [i["iss_id"] for i in trader.ok("GET", "/issuers", query="финансов")] == [1228]
    assert [i["iss_id"] for i in trader.ok("GET", "/issuers", query="7701140866")] == [4456]
    minfin = trader.ok("GET", f"/issuers/{ofz['issuer']['id']}")
    assert (minfin["inn"], [i["secid"] for i in minfin["instruments"]]) == (
        "7710168360",
        ["SU26238RMFS4", "SU29014RMFS6"],
    )
    assert trader.refused("GET", f"/issuers/{UNKNOWN}") == (404, "not_found")


def test_sync_brings_the_issuer_and_keeps_it(
    trader: Api, ops: Api, iss: FakeIss, settings: RefdataSettings
) -> None:
    bond = load(trader, "RU000A107HG1")
    # Так выглядят бумаги, загруженные до эмитентов: эмитента и типа ISS нет.
    assert settings.database_url is not None
    assert settings.database_schema is not None
    database = Database(settings.database_url.get_secret_value(), settings.database_schema)

    async def forget() -> None:
        async with database.session() as session, session.begin():
            await session.execute(update(instrument).values(issuer_id=None, iss_type=None))
        await database.dispose()

    asyncio.run(forget())
    assert trader.ok("GET", f"/instruments/{bond['id']}")["issuer"] is None

    synced = ops.ok("POST", f"/instruments/{bond['id']}/sync")
    assert (synced["issuer"]["name"], synced["iss_type"]) == (
        'Публичное акционерное общество "Газпром нефть"',
        "exchange_bond",
    )
    # ISS ответил без эмитента — прежний остаётся: к нему привязаны рейтинги.
    iss.hide_issuer("RU000A107HG1")
    kept = ops.ok("POST", f"/instruments/{bond['id']}/sync")
    assert (kept["issuer"]["id"], kept["iss_type"]) == (synced["issuer"]["id"], "exchange_bond")


# --- рейтинги ------------------------------------------------------------------


def test_ratings_of_issuer_g_from_the_methodology(trader: Api, risk: Api) -> None:
    bond = load(trader, "RU000A107HG1")
    issuer = {"issuer_id": bond["issuer"]["id"]}
    issue = {"instrument_id": bond["id"]}

    rate(risk, issuer, agency="acra", rating="AA+(RU)", valid_from="2026-06-01")
    rate(risk, issuer, agency="expert_ra", rating="ruAA+", valid_from="2026-06-01")
    # Рейтинг из пресс-релиза — с типографским минусом.
    rate(risk, issuer, agency="acra", rating="AA−(RU)", valid_from="2026-11-11")
    found = rate(
        risk,
        issuer,
        agency="expert_ra",
        rating="ruaa-",
        valid_from="2026-11-12",
        comment="Пресс-релиз Эксперт РА",
    )
    # У выпуска своего рейтинга нет — по политике берут рейтинг эмитента.
    risk.ok("POST", "/ratings", {**issue, "action": "unrated", "valid_from": "2026-06-01"})

    def on(day: str) -> dict[str, Any]:
        attributes = trader.ok("GET", "/attributes", date=day)
        [g] = [i for i in attributes["issuers"] if i["id"] == bond["issuer"]["id"]]
        [paper] = [i for i in attributes["instruments"] if i["id"] == bond["id"]]
        return {"issuer": g["ratings"], "issue": paper["ratings"]["status"]}

    # Таблица методики: 10.11 — оба AA+, 11.11 — АКРА AA−, 12.11 — оба AA−.
    assert agencies(on("2026-11-10")["issuer"]) == {"acra": "AA+(RU)", "expert_ra": "ruAA+"}
    assert agencies(on("2026-11-11")["issuer"]) == {"acra": "AA-(RU)", "expert_ra": "ruAA+"}
    assert [r["grade"] for r in on("2026-11-12")["issuer"]["ratings"]] == ["AA-", "AA-"]
    assert (on("2026-11-12")["issue"], on("2026-05-31")["issuer"]["status"]) == (
        "unrated",
        "unknown",
    )

    # Сегодня — 17.11: история записей и что действует.
    assert found["now"]["status"] == "rated"
    assert [(r["agency_name"], r["since"]) for r in found["now"]["ratings"]] == [
        ("АКРА", "2026-11-11"),
        ("Эксперт РА", "2026-11-12"),
    ]
    assert [(r["agency"], r["rating"], r["author"]) for r in found["records"]][-1] == (
        "expert_ra",
        "ruAA-",
        "risk",
    )
    assert found["records"][-1]["comment"] == "Пресс-релиз Эксперт РА"

    # Агентства отозвали рейтинги — рейтинга нет, а не «неизвестно».
    risk.ok(
        "POST",
        "/ratings",
        {**issuer, "action": "withdrawn", "agency": "acra", "valid_from": "2026-11-13"},
    )
    gone = risk.ok(
        "POST",
        "/ratings",
        {**issuer, "action": "withdrawn", "agency": "expert_ra", "valid_from": "2026-11-14"},
    )
    assert agencies(on("2026-11-13")["issuer"]) == {"expert_ra": "ruAA-"}
    assert (gone["now"]["status"], gone["records"][-1]["rating"]) == ("unrated", None)
    assert trader.ok("GET", "/ratings", **issuer) == gone


def test_rating_rules(trader: Api, ops: Api, risk: Api, tokens: TokenFactory) -> None:
    bond = load(trader, "RU000A107HG1")
    fund = load(trader, "LQDT")
    issue = {"instrument_id": bond["id"]}
    entry = {**issue, "action": "assigned", "agency": "acra", "rating": "AA(RU)"}
    day = {"valid_from": "2026-11-10"}

    # Ведёт риск-менеджмент; администратор может всё.
    assert trader.call("POST", "/ratings", {**entry, **day}).status_code == 403
    assert ops.call("POST", "/ratings", {**entry, **day}).status_code == 403
    admin = Api(trader._client, tokens, "admin")
    admin.ok("POST", "/ratings", {**entry, **day})

    refusals = [
        ({**entry, "agency": "moodys", **day}, "unknown_agency"),
        ({**entry, "rating": "AA", **day}, "unknown_rating"),
        ({**entry, "agency": None, **day}, "agency_required"),
        ({**entry, "rating": "", **day}, "rating_required"),
        ({**entry, "valid_from": "2026-11-18"}, "future_date"),
        ({**issue, "action": "withdrawn", "agency": "nkr", **day}, "nothing_to_withdraw"),
        ({**issue, "action": "unrated", **day}, "rated"),
        ({**entry, "instrument_id": fund["id"], **day}, "not_rated"),
        ({**entry, "issuer_id": bond["issuer"]["id"], **day}, "subject_required"),
        ({"action": "unrated", **day}, "subject_required"),
    ]
    for body, code in refusals:
        assert risk.refused("POST", "/ratings", body) == (422, code), code
    assert risk.refused("POST", "/ratings", {**entry, "instrument_id": UNKNOWN, **day}) == (
        404,
        "not_found",
    )
    assert trader.refused("GET", "/ratings", issuer_id=UNKNOWN) == (404, "not_found")
    message = risk.call("POST", "/ratings", {**issue, "action": "unrated", **day}).json()
    assert message["detail"]["message"] == (
        "На 10.11.2026 у RU000A107HG1 действуют рейтинги: АКРА AA(RU). Сначала отметьте их отзыв."
    )
    withdraw = risk.call(
        "POST", "/ratings", {**issue, "action": "withdrawn", "agency": "nkr", **day}
    ).json()
    assert withdraw["detail"]["message"] == (
        "На 10.11.2026 у RU000A107HG1 нет рейтинга НКР — отзывать нечего."
    )


def test_mistaken_rating_is_cancelled(trader: Api, risk: Api) -> None:
    bond = load(trader, "RU000A107HG1")
    issuer = {"issuer_id": bond["issuer"]["id"]}
    wrong = rate(risk, issuer, agency="nkr", rating="A+.ru", valid_from="2026-11-10")
    [record] = wrong["records"]

    path = f"/ratings/{record['id']}/cancel"
    assert trader.call("POST", path, {"comment": "Не тот эмитент"}).status_code == 403
    assert risk.refused("POST", path, {"comment": ""}) == (422, "invalid")
    cancelled = risk.ok("POST", path, {"comment": "Не тот эмитент"})

    [kept] = cancelled["records"]
    assert (kept["cancelled_by"], kept["cancel_comment"]) == ("risk", "Не тот эмитент")
    assert cancelled["now"] == {"status": "unknown", "ratings": []}
    assert risk.refused("POST", path, {"comment": "Ещё раз"}) == (422, "already_cancelled")
    assert risk.refused("POST", f"/ratings/{UNKNOWN}/cancel", {"comment": "Нет такой"}) == (
        404,
        "not_found",
    )


def test_scale_for_the_form(trader: Api) -> None:
    found = trader.ok("GET", "/ratings/scale")

    assert [a["code"] for a in found["agencies"]] == ["acra", "expert_ra", "nkr", "nra"]
    assert found["agencies"][1]["ratings"][:2] == ["ruAAA", "ruAA+"]
    assert found["grades"][3] == "AA-"


# --- классификаторы ------------------------------------------------------------


def create(api: Api, name: str, target: str, *values: str) -> dict[str, Any]:
    found: dict[str, Any] = api.ok(
        "POST", "/classifiers", {"name": name, "target": target, "values": list(values)}
    )
    return found


def test_classifiers_are_kept_by_risk(trader: Api, risk: Api) -> None:
    seniority = create(risk, "Очерёдность", "instrument", "старший долг", "субординированная")
    issuer_kind = create(risk, "Тип эмитента", "issuer", "Банк", "прочие")
    path = f"/classifiers/{seniority['id']}"

    assert [v["name"] for v in seniority["values"]] == ["старший долг", "субординированная"]
    assert (seniority["target"], seniority["created_by"]) == ("instrument", "risk")
    assert (
        trader.call(
            "POST", "/classifiers", {"name": "x", "target": "issuer", "values": ["a"]}
        ).status_code
        == 403
    )

    renamed = risk.ok("PUT", path, {"name": "Очерёдность выплат", "description": "п. 2 политики"})
    assert (renamed["name"], renamed["description"]) == ("Очерёдность выплат", "п. 2 политики")
    # Регистр букв — тот же классификатор, а не чужое имя.
    risk.ok("PUT", path, {"name": "очерёдность выплат", "description": "п. 2 политики"})
    risk.ok("POST", f"{path}/values", {"name": "третья очередь"})
    bank = issuer_kind["values"][0]["id"]
    fixed = risk.ok("PUT", f"/classifiers/{issuer_kind['id']}/values/{bank}", {"name": "банк"})
    assert [v["name"] for v in fixed["values"]] == ["банк", "прочие"]
    risk.ok(
        "PUT", f"/classifiers/{issuer_kind['id']}/values/{bank}", {"name": "кредитная организация"}
    )

    refusals: list[tuple[str, str, dict[str, Any], str]] = [
        (
            "POST",
            "/classifiers",
            {"name": "тип эмитента", "target": "issuer", "values": ["a"]},
            "classifier_exists",
        ),
        (
            "POST",
            "/classifiers",
            {"name": "Транш", "target": "instrument", "values": []},
            "values_required",
        ),
        (
            "POST",
            "/classifiers",
            {"name": "Транш", "target": "instrument", "values": ["старший", "Старший"]},
            "duplicate_value",
        ),
        ("PUT", path, {"name": "очерёдность выплат", "description": "п. 2 политики"}, "no_changes"),
        ("PUT", path, {"name": "Тип эмитента"}, "classifier_exists"),
        ("POST", f"{path}/values", {"name": "Третья очередь"}, "duplicate_value"),
        (
            "PUT",
            f"/classifiers/{issuer_kind['id']}/values/{bank}",
            {"name": "кредитная организация"},
            "no_changes",
        ),
        (
            "PUT",
            f"/classifiers/{issuer_kind['id']}/values/{bank}",
            {"name": "Прочие"},
            "duplicate_value",
        ),
    ]
    for method, url, body, code in refusals:
        assert risk.refused(method, url, body) == (422, code), code
    assert risk.refused("PUT", f"/classifiers/{UNKNOWN}", {"name": "x"}) == (404, "not_found")
    assert risk.refused(
        "PUT", f"/classifiers/{issuer_kind['id']}/values/{UNKNOWN}", {"name": "x"}
    ) == (404, "not_found")

    listed = trader.ok("GET", "/classifiers")
    assert sorted((c["name"], len(c["values"])) for c in listed) == [
        ("Тип эмитента", 2),
        ("очерёдность выплат", 3),
    ]


def test_bonds_and_issuers_are_classified_from_a_date(trader: Api, risk: Api) -> None:
    veb = load(trader, "RU000A10F504")
    seniority = create(risk, "Очерёдность", "instrument", "старший долг", "субординированная")
    issuer_kind = create(risk, "Тип эмитента", "issuer", "банк", "прочие")
    senior, sub = (v["id"] for v in seniority["values"])
    bank, other = (v["id"] for v in issuer_kind["values"])
    paper = {"instrument_id": veb["id"]}
    issuer = {"issuer_id": veb["issuer"]["id"]}

    def classify(
        subject: dict[str, str], classifier: dict[str, Any], value: str, **fields: Any
    ) -> Any:
        return risk.ok(
            "POST",
            "/classifications",
            {**subject, "classifier_id": classifier["id"], "value_id": value, **fields},
        )

    # ВЭБ.РФ — не банк для Л7 (решение 137); очерёдность — с начала обращения.
    classify(issuer, issuer_kind, other, comment="Решение 137")
    classify(paper, seniority, senior)
    changed = classify(paper, seniority, sub, valid_from="2026-11-11")
    assert [(r["value"], r["valid_from"]) for r in changed["records"]] == [
        ("старший долг", None),
        ("субординированная", "2026-11-11"),
    ]
    assert changed["now"] == [
        {
            "classifier_id": seniority["id"],
            "classifier": "Очерёдность",
            "value_id": sub,
            "value": "субординированная",
            "since": "2026-11-11",
        }
    ]

    attributes = trader.ok("GET", "/attributes", date="2026-11-10")
    [bond] = attributes["instruments"]
    assert (bond["classes"], bond["kind"], bond["coupon_kind"]) == (
        {seniority["id"]: senior},
        "bond",
        "fixed",
    )
    assert (bond["iss_type"], bond["currency"], bond["nominal_indexation"]) == (
        "exchange_bond",
        "RUB",
        "unknown",
    )
    assert attributes["issuers"][0]["classes"] == {issuer_kind["id"]: other}
    assert [c["name"] for c in attributes["classifiers"]] == ["Очерёдность", "Тип эмитента"]

    body = {**paper, "classifier_id": seniority["id"], "value_id": sub}
    refusals = [
        ({**body, "valid_from": "2026-11-12"}, "no_changes"),
        ({**body, "classifier_id": issuer_kind["id"], "value_id": bank}, "wrong_target"),
        ({**issuer, "classifier_id": seniority["id"], "value_id": senior}, "wrong_target"),
        ({**body, "value_id": bank}, "unknown_value"),
        ({**body, "valid_from": "2026-11-18"}, "future_date"),
    ]
    for wrong, code in refusals:
        assert risk.refused("POST", "/classifications", wrong) == (422, code), code
    assert risk.refused("POST", "/classifications", {**body, "classifier_id": UNKNOWN}) == (
        404,
        "not_found",
    )
    assert trader.call("POST", "/classifications", body).status_code == 403
    assert trader.refused("GET", "/classifications", instrument_id=UNKNOWN) == (404, "not_found")

    mistaken = changed["records"][-1]["id"]
    path = f"/classifications/{mistaken}/cancel"
    back = risk.ok("POST", path, {"comment": "Ошиблись выпуском"})
    assert back["now"][0]["value"] == "старший долг"
    assert back["records"][-1]["cancel_comment"] == "Ошиблись выпуском"
    assert risk.refused("POST", path, {"comment": "Ещё раз"}) == (422, "already_cancelled")
    assert risk.refused("POST", f"/classifications/{UNKNOWN}/cancel", {"comment": "Нет"}) == (
        404,
        "not_found",
    )
    assert trader.ok("GET", "/classifications", **issuer)["now"][0]["value"] == "прочие"


# --- журнал --------------------------------------------------------------------


@pytest.fixture
def stream(settings: RefdataSettings) -> redis.Redis:
    assert settings.redis_url is not None
    connection = redis.Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
    connection.delete("am.events.refdata")
    return connection


def test_changes_go_to_the_journal(stream: redis.Redis, trader: Api, risk: Api) -> None:
    bond = load(trader, "RU000A107HG1")
    issuer = {"issuer_id": bond["issuer"]["id"]}
    rated = rate(risk, issuer, agency="acra", rating="AA(RU)", valid_from="2026-11-10")
    risk.ok("POST", f"/ratings/{rated['records'][0]['id']}/cancel", {"comment": "Опечатка"})
    kind = create(risk, "Тип эмитента", "issuer", "банк")
    risk.ok("PUT", f"/classifiers/{kind['id']}", {"name": "Эмитент", "description": None})
    risk.ok("POST", f"/classifiers/{kind['id']}/values", {"name": "прочие"})
    risk.ok("PUT", f"/classifiers/{kind['id']}/values/{kind['values'][0]['id']}", {"name": "банки"})
    set_ = risk.ok(
        "POST",
        "/classifications",
        {**issuer, "classifier_id": kind["id"], "value_id": kind["values"][0]["id"]},
    )
    risk.ok("POST", f"/classifications/{set_['records'][0]['id']}/cancel", {"comment": "Не банк"})

    events: list[dict[str, Any]] = []
    for _ in range(100):
        events = [json.loads(f["event"]) for _id, f in stream.xrange("am.events.refdata")]  # type: ignore[union-attr, index]
        if len(events) >= 9:
            break
        time.sleep(0.05)
    stream.close()

    assert [e["type"] for e in events] == [
        "instrument.loaded",
        "rating.added",
        "rating.cancelled",
        "classifier.created",
        "classifier.updated",
        "classifier.value_added",
        "classifier.value_renamed",
        "classification.set",
        "classification.cancelled",
    ]
    _loaded, added, cancelled, created, updated, _value, renamed, classified, unset = events
    assert (added["entity_type"], added["data"]["issuer"]) == (
        "issuer",
        {"name": 'Публичное акционерное общество "Газпром нефть"'},
    )
    assert added["data"]["after"] == {
        "action": "assigned",
        "agency": "acra",
        "rating": "AA(RU)",
        "valid_from": "2026-11-10",
    }
    assert (cancelled["comment"], cancelled["data"]["after"]) == ("Опечатка", None)
    assert created["data"]["after"] == {
        "name": "Тип эмитента",
        "target": "issuer",
        "description": None,
        "values": "банк",
    }
    assert updated["data"]["before"]["name"] == "Тип эмитента"
    assert renamed["data"] == {
        "classifier": {"name": "Эмитент"},
        "before": {"class_value": "банк"},
        "after": {"class_value": "банки"},
    }
    assert classified["data"]["classifier"] == {"name": "Эмитент"}
    assert classified["data"]["after"] == {"class_value": "банки", "valid_from": None}
    assert (unset["comment"], unset["data"]["before"]["class_value"]) == ("Не банк", "банки")
