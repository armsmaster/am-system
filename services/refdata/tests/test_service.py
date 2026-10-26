"""Справочник целиком: API → ISS (подменный) → правила → БД → события."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import pytest
import redis
from fastapi.testclient import TestClient
from httpx2 import Response

from am_common.migrations import schema_diff
from am_common.testing import TokenFactory
from refdata.clock import moscow_now
from refdata.db import metadata
from refdata.settings import RefdataSettings

if TYPE_CHECKING:
    from conftest import FakeIss

pytestmark = pytest.mark.integration


class Api:
    def __init__(self, client: TestClient, tokens: TokenFactory, *roles: str) -> None:
        self._client = client
        token = tokens.issue(subject=f"u-{'-'.join(roles)}", username="+".join(roles), roles=roles)
        self._headers = {"Authorization": f"Bearer {token}"}

    def call(
        self, method: str, path: str, body: dict[str, Any] | None = None, **params: Any
    ) -> Response:
        return self._client.request(method, path, json=body, params=params, headers=self._headers)

    def ok(self, method: str, path: str, body: dict[str, Any] | None = None, **params: Any) -> Any:
        response = self.call(method, path, body, **params)
        assert response.is_success, response.text
        return response.json() if response.content else None

    def problem(
        self, method: str, path: str, body: dict[str, Any] | None = None, **params: Any
    ) -> tuple[int, str]:
        response = self.call(method, path, body, **params)
        assert response.is_error, response.text
        detail = response.json()["detail"]
        # Ошибки формата запроса FastAPI отдаёт списком — это «validation_error».
        return response.status_code, detail["code"] if isinstance(
            detail, dict
        ) else "validation_error"


@pytest.fixture
def trader(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "trading")


@pytest.fixture
def ops(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "operations")


def today() -> str:
    return moscow_now().date().isoformat()


# --- схема БД ------------------------------------------------------------------


async def test_migrations_match_the_model(settings: RefdataSettings) -> None:
    assert settings.database_url is not None
    assert settings.database_schema is not None

    diff = await schema_diff(
        settings.database_url.get_secret_value(), settings.database_schema, metadata
    )

    assert diff == []


# --- поиск и загрузка ----------------------------------------------------------


def test_lookup_shows_what_will_be_loaded(trader: Api) -> None:
    preview = trader.ok("GET", "/lookup", code="RU000A1038V6")

    assert preview["secid"] == "SU26238RMFS4"
    assert preview["params"]["coupon_kind"] == "fixed"
    assert len(preview["params"]["coupons"]) == 40
    assert preview["instrument_id"] is None


def test_lookup_explains_failures(trader: Api, iss: FakeIss) -> None:
    status, code = trader.problem("GET", "/lookup", code="NOSUCHCODE123")
    response = trader.call("GET", "/lookup", code="LQD")
    iss.describe("SU26238RMFS4", "GROUP", "stock_shares")
    unsupported = trader.problem("GET", "/lookup", code="SU26238RMFS4")
    iss.down = True
    unavailable = trader.problem("GET", "/lookup", code="SU26238RMFS4")

    assert (status, code) == (404, "not_in_iss")
    # Нет точного совпадения — показываем, что нашлось.
    assert [c["secid"] for c in response.json()["detail"]["candidates"]] == ["LQDTM", "LQDT"]
    assert unsupported == (422, "unsupported_security")
    assert unavailable == (503, "iss_unavailable")


def test_trader_loads_a_bond_once(trader: Api, ops: Api) -> None:
    first = trader.call("POST", "/instruments", {"code": "SU29014RMFS6"})
    again = ops.call("POST", "/instruments", {"code": "RU000A101N52"})  # тот же выпуск по ISIN

    assert first.status_code == 201
    assert again.status_code == 200
    instrument = first.json()
    assert again.json()["id"] == instrument["id"]
    assert instrument["added_by"] == "trading"
    assert [v["number"] for v in instrument["versions"]] == [1]
    assert instrument["versions"][0]["valid_from"] is None  # действует с начала обращения
    assert [a["code"] for a in instrument["alerts"]] == ["floater_terms_missing"]


def test_search_finds_by_code_and_name(trader: Api) -> None:
    trader.ok("POST", "/instruments", {"code": "SU26238RMFS4"})
    trader.ok("POST", "/instruments", {"code": "LQDT"})

    assert [i["secid"] for i in trader.ok("GET", "/instruments")] == ["LQDT", "SU26238RMFS4"]
    assert [i["secid"] for i in trader.ok("GET", "/instruments", query="26238")] == ["SU26238RMFS4"]
    assert [i["kind"] for i in trader.ok("GET", "/instruments", query="ликвидность")] == [
        "fund_share"
    ]


# --- правка сопровождением -----------------------------------------------------


def floater_terms() -> dict[str, Any]:
    return {"rate": "ruonia", "margin_bp": "0", "capitalized": True}


def test_operations_complete_a_floater(trader: Api, ops: Api) -> None:
    bond = trader.ok("POST", "/instruments", {"code": "SU29014RMFS6"})

    edited = ops.ok(
        "POST",
        f"/instruments/{bond['id']}/versions",
        {
            "valid_from": today(),
            "comment": "Параметры из решения о выпуске",
            "changes": {"floater": floater_terms()},
        },
    )

    assert [v["number"] for v in edited["versions"]] == [1, 2]
    assert edited["current"]["params"]["floater"] == {
        "rate": "ruonia",
        "margin_bp": "0",
        "capitalized": True,
    }
    assert edited["current"]["source"] == "manual"
    assert edited["alerts"] == []
    # Прошлые даты остаются на первой версии.
    assert trader.ok("GET", f"/instruments/{bond['id']}/as-of/2025-01-01")["number"] == 1
    assert (
        trader.ok("GET", f"/instruments/{bond['id']}/versions/2")["comment"]
        == "Параметры из решения о выпуске"
    )


def test_edit_rules(trader: Api, ops: Api) -> None:
    bond = trader.ok("POST", "/instruments", {"code": "SU46020RMFS2"})
    path = f"/instruments/{bond['id']}/versions"
    fixed = {
        "valid_from": today(),
        "comment": "Купон фиксированный",
        "changes": {"coupon_kind": "fixed"},
    }

    assert trader.call("POST", path, fixed).status_code == 403
    ops.ok("POST", path, fixed)
    assert ops.problem("POST", path, fixed) == (422, "no_changes")
    assert ops.problem(
        "POST", path, {**fixed, "valid_from": "2000-01-01", "changes": {"short_name": "x"}}
    ) == (
        422,
        "valid_from_too_early",
    )
    assert ops.problem("POST", path, {**fixed, "changes": {"wat": 1}}) == (422, "unknown_field")
    assert ops.problem("POST", path, {**fixed, "comment": ""}) == (422, "validation_error")
    assert ops.problem(
        "POST", "/instruments/00000000-0000-0000-0000-000000000000/versions", fixed
    ) == (
        404,
        "not_found",
    )
    assert ops.problem("GET", f"/instruments/{bond['id']}/versions/9") == (404, "not_found")


def test_as_of_before_first_version(trader: Api, ops: Api) -> None:
    bond = trader.ok("POST", "/instruments", {"code": "SU26238RMFS4"})

    # У первой версии нет даты начала — она действует для любой даты.
    assert trader.ok("GET", f"/instruments/{bond['id']}/as-of/1990-01-01")["number"] == 1


# --- синхронизация с ISS -------------------------------------------------------


def test_sync_creates_a_version_only_when_iss_changed(trader: Api, ops: Api, iss: FakeIss) -> None:
    bond = trader.ok("POST", "/instruments", {"code": "SU26238RMFS4"})
    path = f"/instruments/{bond['id']}/sync"

    unchanged = ops.ok("POST", path)
    iss.describe("SU26238RMFS4", "MATDATE", "2041-06-15")
    changed = ops.ok("POST", path)

    assert [v["number"] for v in unchanged["versions"]] == [1]
    assert [v["number"] for v in changed["versions"]] == [1, 2]
    latest = changed["versions"][-1]
    assert latest["source"] == "iss"
    assert latest["author"] == "moex-iss"
    assert latest["valid_from"] == today()
    assert "дата погашения" in latest["comment"]
    assert changed["current"]["params"]["maturity_date"] == "2041-06-15"


def test_sync_keeps_pinned_fields_and_flags_the_difference(
    trader: Api, ops: Api, iss: FakeIss
) -> None:
    bond = trader.ok("POST", "/instruments", {"code": "SU26238RMFS4"})
    ops.ok(
        "POST",
        f"/instruments/{bond['id']}/versions",
        {"valid_from": today(), "comment": "Своё краткое имя", "changes": {"short_name": "ОФЗ 38"}},
    )
    iss.describe("SU26238RMFS4", "SHORTNAME", "ОФЗ 26238 (нов)")

    synced = ops.ok("POST", f"/instruments/{bond['id']}/sync")

    assert synced["short_name"] == "ОФЗ 38"
    assert [a["code"] for a in synced["alerts"]] == ["differs_from_iss"]
    # Снять закрепление — значит взять значение из ISS.
    unpinned = ops.ok(
        "POST",
        f"/instruments/{bond['id']}/versions",
        {"valid_from": today(), "comment": "Берём имя из ISS", "unpin": ["short_name"]},
    )
    assert unpinned["short_name"] == "ОФЗ 26238 (нов)"
    assert unpinned["alerts"] == []


def test_sync_failure_is_recorded(trader: Api, ops: Api, iss: FakeIss) -> None:
    bond = trader.ok("POST", "/instruments", {"code": "SU26238RMFS4"})
    iss.down = True

    assert ops.problem("POST", f"/instruments/{bond['id']}/sync") == (503, "iss_unavailable")
    iss.down = False
    stored = trader.ok("GET", f"/instruments/{bond['id']}")

    assert "ISS is down" in stored["last_sync_error"]
    assert trader.call("POST", f"/instruments/{bond['id']}/sync").status_code == 403


# --- календарь -----------------------------------------------------------------


def test_calendar_exceptions_and_settlement_dates(trader: Api, ops: Api) -> None:
    ops.ok(
        "PUT",
        "/calendar/days/2026-11-04",
        {"is_trading": False, "comment": "День народного единства"},
    )
    ops.ok("PUT", "/calendar/days/2026-11-04", {"is_trading": False, "comment": "Праздник"})

    assert trader.ok("GET", "/calendar/2026") == [
        {"day": "2026-11-04", "is_trading": False, "comment": "Праздник"}
    ]
    assert trader.ok("GET", "/calendar/2027") == []
    assert trader.ok("GET", "/calendar/add-trading-days", date="2026-11-03", days=1) == {
        "date": "2026-11-05"
    }
    assert trader.call("PUT", "/calendar/days/2026-11-05", {"is_trading": False}).status_code == 403

    ops.ok("DELETE", "/calendar/days/2026-11-04")
    assert trader.ok("GET", "/calendar/2026") == []
    assert ops.problem("DELETE", "/calendar/days/2026-11-04") == (422, "no_exception")


# --- журнал --------------------------------------------------------------------


@pytest.fixture
def stream(settings: RefdataSettings) -> redis.Redis:
    assert settings.redis_url is not None
    connection = redis.Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
    connection.delete("am.events.refdata")
    return connection


def test_changes_go_to_the_journal(stream: redis.Redis, trader: Api, ops: Api) -> None:
    bond = trader.ok("POST", "/instruments", {"code": "SU29014RMFS6"})
    ops.ok(
        "POST",
        f"/instruments/{bond['id']}/versions",
        {
            "valid_from": today(),
            "comment": "Маржа из решения о выпуске",
            "changes": {"floater": floater_terms()},
        },
    )
    ops.ok("PUT", "/calendar/days/2026-11-04", {"is_trading": False, "comment": "Праздник"})
    ops.ok("DELETE", "/calendar/days/2026-11-04")

    events: list[dict[str, Any]] = []
    for _ in range(100):
        events = [json.loads(f["event"]) for _id, f in stream.xrange("am.events.refdata")]  # type: ignore[union-attr, index]
        if len(events) >= 4:
            break
        time.sleep(0.05)
    stream.close()

    assert [e["type"] for e in events] == [
        "instrument.loaded",
        "instrument.version_added",
        "calendar.day_set",
        "calendar.day_cleared",
    ]
    loaded, edited, day_set, _cleared = events
    assert loaded["data"]["instrument"] == {"secid": "SU29014RMFS6", "isin": "RU000A101N52"}
    assert loaded["data"]["after"]["coupons"] == "24 зап."
    assert edited["comment"] == "Маржа из решения о выпуске"
    assert edited["data"]["before"] == {"floater": None}
    assert edited["data"]["after"] == {
        "floater": {"rate": "ruonia", "margin_bp": "0", "capitalized": True}
    }
    assert day_set["data"] == {
        "day": "2026-11-04",
        "before": None,
        "after": {"is_trading": False, "comment": "Праздник"},
    }
