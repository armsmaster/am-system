"""Сервис портфелей целиком: API → правила → БД → события.

Структура, которую строят тесты (как в test_domain.py):

    F1 (фонд, счёт A1)                 F2 (фонд, счёт A2)
    ├── F1-BONDS                        └── F2-MAIN (physical, A2)
    │   └── F1-OFZ (physical, A1)
    └── F1-TREASURY (physical, A1)
    BOOK (представление) ── F1-OFZ, F2-MAIN
"""

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
from portfolio.db import metadata
from portfolio.main import build_app
from portfolio.settings import PortfolioSettings

if TYPE_CHECKING:
    from conftest import FakeKeycloak

pytestmark = pytest.mark.integration

STREAM = "am.events.portfolio"


class Api:
    """Клиент API от имени конкретного пользователя."""

    def __init__(self, client: TestClient, tokens: TokenFactory, subject: str, *roles: str) -> None:
        self._client = client
        token = tokens.issue(subject=subject, username=subject, roles=roles)
        self._headers = {"Authorization": f"Bearer {token}"}

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Response:
        return self._client.request(method, path, json=body, headers=self._headers)

    def ok(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        response = self.call(method, path, body)
        assert response.is_success, response.text
        return response.json() if response.content else None

    def rule(self, method: str, path: str, body: dict[str, Any] | None = None) -> str:
        """Код нарушенного правила (ответ 422)."""
        response = self.call(method, path, body)
        assert response.status_code == 422, response.text
        code: str = response.json()["detail"]["code"]
        return code


@pytest.fixture
def admin(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "u-admin", "admin")


@pytest.fixture
def ids(admin: Api) -> dict[str, str]:
    """Строит типовую структуру и возвращает код → id."""
    ids: dict[str, str] = {}

    def portfolio(code: str, kind: str, parents: list[str], **extra: Any) -> None:
        body = {"code": code, "name": f"Портфель {code}", "kind": kind, **extra}
        body["parent_ids"] = [ids[p] for p in parents]
        ids[code] = admin.ok("POST", "/portfolios", body)["id"]

    def account(code: str, fund: str) -> None:
        body = {
            "fund_id": ids[fund],
            "code": code,
            "name": f"Счёт {code}",
            "cash_kind": "broker",
            "institution": "Брокер",
            "cash_account": "30601810000000000001",
            "depo_account": "D-1",
        }
        ids[code] = admin.ok("POST", "/accounts", body)["id"]

    portfolio("F1", "compound", [], is_fund=True)
    account("A1", "F1")
    portfolio("F1-BONDS", "compound", ["F1"])
    portfolio("F1-OFZ", "physical", ["F1-BONDS"], account_id=ids["A1"])
    portfolio("F1-TREASURY", "physical", ["F1"], account_id=ids["A1"])
    portfolio("F2", "compound", [], is_fund=True)
    account("A2", "F2")
    portfolio("F2-MAIN", "physical", ["F2"], account_id=ids["A2"])
    portfolio("BOOK", "compound", [])
    admin.ok("PUT", f"/portfolios/{ids['BOOK']}/children/{ids['F1-OFZ']}")
    admin.ok("PUT", f"/portfolios/{ids['BOOK']}/children/{ids['F2-MAIN']}")
    return ids


def by_code(api: Api) -> dict[str, dict[str, Any]]:
    return {item["code"]: item for item in api.ok("GET", "/portfolios")}


# --- схема БД ------------------------------------------------------------------


async def test_migrations_match_the_model(settings: PortfolioSettings) -> None:
    assert settings.database_url is not None
    assert settings.database_schema is not None

    diff = await schema_diff(
        settings.database_url.get_secret_value(), settings.database_schema, metadata
    )

    assert diff == []


# --- структура портфелей ---------------------------------------------------------


def test_admin_builds_the_structure(admin: Api, ids: dict[str, str]) -> None:
    portfolios = by_code(admin)

    ofz = portfolios["F1-OFZ"]
    assert ofz["kind"] == "physical"
    assert ofz["fund_id"] == ids["F1"]
    assert ofz["account_id"] == ids["A1"]
    assert set(ofz["parent_ids"]) == {ids["F1-BONDS"], ids["BOOK"]}
    assert portfolios["BOOK"]["fund_id"] is None
    assert admin.ok("GET", "/me")["sees_everything"] is True


def test_rules_are_enforced(admin: Api, ids: dict[str, str]) -> None:
    def new(code: str, kind: str, parents: list[str], **extra: Any) -> dict[str, Any]:
        return {
            "code": code,
            "name": code,
            "kind": kind,
            "parent_ids": [ids[p] for p in parents],
            **extra,
        }

    assert (
        admin.rule("POST", "/portfolios", new("X", "physical", ["F1"]))
        == "physical_without_account"
    )
    assert admin.rule("POST", "/portfolios", new("f1-ofz", "compound", [])) == "duplicate_code"
    assert (
        admin.rule("POST", "/portfolios", new("X", "physical", ["F1"], account_id=ids["A2"]))
        == "account_of_other_fund"
    )
    assert (
        admin.rule("PUT", f"/portfolios/{ids['F2']}/children/{ids['F1-OFZ']}")
        == "physical_fund_count"
    )
    assert (
        admin.rule("PUT", f"/portfolios/{ids['F1-BONDS']}/children/{ids['F2']}")
        == "fund_inside_fund"
    )
    assert (
        admin.rule("PUT", f"/portfolios/{ids['F1-OFZ']}/children/{ids['F2-MAIN']}")
        == "parent_not_compound"
    )
    assert admin.rule("DELETE", f"/portfolios/{ids['F2']}/children/{ids['F2-MAIN']}") == (
        "physical_fund_count"
    )
    assert (
        admin.rule(
            "POST", "/portfolios", new("X", "physical", ["F1"], is_fund=True, account_id=ids["A1"])
        )
        == "fund_not_compound"
    )
    # Ничего из отклонённого не сохранилось.
    assert len(by_code(admin)) == 7


def test_link_and_unlink(admin: Api, ids: dict[str, str]) -> None:
    path = f"/portfolios/{ids['BOOK']}/children/{ids['F1-TREASURY']}"

    admin.ok("PUT", path)
    admin.ok("PUT", path)  # повтор безопасен
    assert ids["BOOK"] in by_code(admin)["F1-TREASURY"]["parent_ids"]

    admin.ok("DELETE", path)
    assert ids["BOOK"] not in by_code(admin)["F1-TREASURY"]["parent_ids"]
    assert admin.call("DELETE", path).status_code == 404


def test_rename(admin: Api, ids: dict[str, str]) -> None:
    renamed = admin.ok("PATCH", f"/portfolios/{ids['F1-OFZ']}", {"name": "ОФЗ", "code": "F1-GOV"})
    unchanged = admin.ok("PATCH", f"/portfolios/{ids['F1-OFZ']}", {"name": "ОФЗ"})

    assert (renamed["code"], renamed["name"]) == ("F1-GOV", "ОФЗ")
    assert unchanged == renamed


def test_account_and_treasury(admin: Api, ids: dict[str, str]) -> None:
    second = admin.ok(
        "POST",
        "/accounts",
        {
            "fund_id": ids["F1"],
            "code": "A1-BANK",
            "name": "Расчётный счёт",
            "cash_kind": "bank",
            "institution": "Банк",
            "cash_account": "40701810000000000002",
        },
    )

    moved = admin.ok(
        "PUT", f"/portfolios/{ids['F1-TREASURY']}/account", {"account_id": second["id"]}
    )
    same = admin.ok(
        "PUT", f"/portfolios/{ids['F1-TREASURY']}/account", {"account_id": second["id"]}
    )
    other_fund = admin.rule(
        "PUT", f"/portfolios/{ids['F1-OFZ']}/account", {"account_id": ids["A2"]}
    )
    treasury = admin.ok("PUT", f"/funds/{ids['F1']}/treasury", {"portfolio_id": ids["F1-TREASURY"]})
    wrong = admin.rule("PUT", f"/funds/{ids['F1']}/treasury", {"portfolio_id": ids["F2-MAIN"]})
    cleared = admin.ok("PUT", f"/funds/{ids['F1']}/treasury", {"portfolio_id": None})

    assert moved["account_id"] == second["id"] == same["account_id"]
    assert second["depo_account"] is None
    assert other_fund == "account_of_other_fund"
    assert treasury["treasury_id"] == ids["F1-TREASURY"]
    assert wrong == "treasury_not_in_fund"
    assert cleared["treasury_id"] is None


def test_update_account(admin: Api, ids: dict[str, str]) -> None:
    path = f"/accounts/{ids['A1']}"

    updated = admin.ok("PATCH", path, {"institution": "Другой брокер", "derivatives_market": True})
    unchanged = admin.ok("PATCH", path, {})

    assert updated["institution"] == "Другой брокер"
    assert updated["derivatives_market"] is True
    assert updated["cash_account"] == "30601810000000000001"
    assert unchanged == updated
    assert admin.rule("PATCH", path, {"code": "a2"}) == "duplicate_code"
    assert admin.call("PATCH", f"/accounts/{ids['F1']}", {"name": "x"}).status_code == 404


def test_bad_input_is_rejected(admin: Api, ids: dict[str, str]) -> None:
    unknown_parent = admin.call(
        "POST",
        "/portfolios",
        {"code": "X", "name": "X", "kind": "compound", "parent_ids": [ids["A1"]]},
    )
    blank_code = admin.call("POST", "/portfolios", {"code": "  ", "name": "X", "kind": "compound"})
    spaced_code = admin.call(
        "POST", "/portfolios", {"code": "A B", "name": "X", "kind": "compound"}
    )
    unknown_account = admin.call(
        "PUT", f"/portfolios/{ids['F1-OFZ']}/account", {"account_id": ids["F1"]}
    )
    unknown_treasury = admin.call(
        "PUT", f"/funds/{ids['F1']}/treasury", {"portfolio_id": ids["A1"]}
    )

    assert unknown_parent.status_code == 404
    assert unknown_parent.json()["detail"] == {
        "code": "not_found",
        "message": "Не найдено: portfolio",
    }
    assert blank_code.status_code == spaced_code.status_code == 422
    assert unknown_account.status_code == unknown_treasury.status_code == 404


# --- доступы ---------------------------------------------------------------------


def test_user_sees_only_granted_subtree(
    admin: Api, ids: dict[str, str], client: TestClient, tokens: TokenFactory
) -> None:
    trader = Api(client, tokens, "u-trader", "trading")
    admin.ok("PUT", f"/users/u-trader/grants/{ids['F1-BONDS']}")

    portfolios = by_code(trader)
    me = trader.ok("GET", "/me")
    accounts = trader.ok("GET", "/accounts")

    assert set(portfolios) == {"F1-BONDS", "F1-OFZ"}
    # Родитель, которого пользователь не видит, не раскрывается.
    assert portfolios["F1-BONDS"]["parent_ids"] == []
    assert portfolios["F1-OFZ"]["parent_ids"] == [ids["F1-BONDS"]]
    assert me["sees_everything"] is False
    assert set(me["portfolio_ids"]) == {ids["F1-BONDS"], ids["F1-OFZ"]}
    assert [a["code"] for a in accounts] == ["A1"]


@pytest.mark.parametrize(
    ("username", "role"),
    [
        ("service-account-am-trade-feed", "trade_feed"),
        ("service-account-am-statement-feed", "statement_feed"),
        ("service-account-am-limits", "fund_reader"),
    ],
)
@pytest.mark.usefixtures("ids")
def test_feeds_see_every_portfolio(
    admin: Api,
    client: TestClient,
    tokens: TokenFactory,
    username: str,
    role: str,
) -> None:
    # Скрипты сделок и выписок присылают данные всех фондов (вопрос 100), сервис лимитов
    # их читает (решение 147); менять портфели не могут.
    feed = Api(client, tokens, username, role)

    assert set(by_code(feed)) == set(by_code(admin))
    assert [a["code"] for a in feed.ok("GET", "/accounts")] == [
        a["code"] for a in admin.ok("GET", "/accounts")
    ]
    assert feed.ok("GET", "/me")["sees_everything"] is True
    assert feed.call("POST", "/accounts", {}).status_code == 403


def test_access_to_view_spans_funds(
    admin: Api, ids: dict[str, str], client: TestClient, tokens: TokenFactory
) -> None:
    ops = Api(client, tokens, "u-ops", "operations")
    admin.ok("PUT", f"/users/u-ops/grants/{ids['BOOK']}")

    assert set(by_code(ops)) == {"BOOK", "F1-OFZ", "F2-MAIN"}
    assert [a["code"] for a in ops.ok("GET", "/accounts")] == ["A1", "A2"]


def test_without_grants_user_sees_nothing(
    client: TestClient, tokens: TokenFactory, ids: dict[str, str]
) -> None:
    trader = Api(client, tokens, "u-trader", "trading")

    assert trader.ok("GET", "/portfolios") == []
    assert trader.ok("GET", "/accounts") == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/portfolios"),
        ("PATCH", "/portfolios/00000000-0000-0000-0000-000000000000"),
        ("POST", "/accounts"),
        ("GET", "/users"),
        ("PUT", "/users/u-ops/grants/00000000-0000-0000-0000-000000000000"),
    ],
)
def test_only_admin_changes_things(
    client: TestClient, tokens: TokenFactory, method: str, path: str
) -> None:
    trader = Api(client, tokens, "u-trader", "trading")

    assert trader.call(method, path, {}).status_code == 403


def test_grants_and_users(admin: Api, ids: dict[str, str], keycloak: FakeKeycloak) -> None:
    grant = f"/users/u-trader/grants/{ids['F1']}"

    admin.ok("PUT", grant)
    admin.ok("PUT", grant)  # повтор безопасен
    users = {user["username"]: user for user in admin.ok("GET", "/users")}
    admin.ok("GET", "/users")

    assert set(users) == {"admin", "trader", "ops"}  # служебные учётки скрыты
    assert users["trader"]["full_name"] == "Тимур Трейдеров"
    assert users["trader"]["roles"] == ["trading"]
    assert users["trader"]["grant_ids"] == [ids["F1"]]
    assert users["ops"]["full_name"] == "ops"
    # Служебный токен Keycloak получен один раз и переиспользуется.
    assert keycloak.token_requests == 1

    admin.ok("DELETE", grant)
    assert admin.call("DELETE", grant).status_code == 404
    assert admin.rule("PUT", f"/users/u-trader/grants/{ids['F1-OFZ']}") == "grant_not_compound"
    assert admin.call("PUT", f"/users/u-nobody/grants/{ids['F1']}").status_code == 404


def test_directory_outage_is_reported(admin: Api, keycloak: FakeKeycloak) -> None:
    keycloak.admin_api_status = 500

    response = admin.call("GET", "/users")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "directory_unavailable"


def test_directory_needs_service_account(
    settings: PortfolioSettings, keycloak: FakeKeycloak, tokens: TokenFactory
) -> None:
    unconfigured = settings.model_copy(update={"keycloak_client_secret": None})
    with TestClient(build_app(unconfigured, http_transport=keycloak.transport())) as client:
        response = Api(client, tokens, "u-admin", "admin").call("GET", "/users")

    assert response.status_code == 503


# --- журнал ----------------------------------------------------------------------


@pytest.fixture
def stream(settings: PortfolioSettings) -> redis.Redis:
    """Поток событий сервиса, очищенный от событий прошлых тестов."""
    assert settings.redis_url is not None
    connection = redis.Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
    connection.delete(STREAM)
    return connection


def test_every_change_goes_to_the_event_stream(
    stream: redis.Redis, admin: Api, ids: dict[str, str]
) -> None:
    admin.ok("PATCH", f"/portfolios/{ids['F1-OFZ']}", {"name": "ОФЗ"})
    admin.ok("PUT", f"/users/u-trader/grants/{ids['F1']}")

    events: list[dict[str, Any]] = []
    for _ in range(100):
        entries = stream.xrange(STREAM)
        events = [json.loads(fields["event"]) for _id, fields in entries]  # type: ignore[union-attr, index]
        if len(events) >= 13:
            break
        time.sleep(0.05)
    stream.close()

    types = [event["type"] for event in events]
    assert types == [
        "portfolio.created",
        "account.created",
        "portfolio.created",
        "portfolio.created",
        "portfolio.created",
        "portfolio.created",
        "account.created",
        "portfolio.created",
        "portfolio.created",
        "portfolio.linked",
        "portfolio.linked",
        "portfolio.updated",
        "access.granted",
    ]
    # В журнале — коды связанных объектов на момент действия, а не только id.
    assert events[1]["data"]["after"]["fund_code"] == "F1"
    assert events[3]["data"]["after"]["account_code"] == "A1"
    renamed = events[-2]
    assert renamed["actor"] == {"subject": "u-admin", "username": "u-admin"}
    assert renamed["data"]["before"]["name"] == "Портфель F1-OFZ"
    assert renamed["data"]["after"]["name"] == "ОФЗ"
    assert events[-1]["data"]["user"] == {"subject": "u-trader", "username": "trader"}
