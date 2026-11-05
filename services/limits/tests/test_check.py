"""Проверка сделки до ввода через API: пример методики — покупка Эмитента А и прямое РЕПО."""

from __future__ import annotations

from datetime import date, datetime
from types import ModuleType
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from am_common.clock import MOSCOW
from am_common.testing import TokenFactory
from limits.main import build_app
from limits.settings import LimitsSettings

if TYPE_CHECKING:
    from conftest import Api, World

pytestmark = pytest.mark.integration

MLN = 1_000_000


@pytest.fixture
def morning(world: World, risk: Api, model: ModuleType) -> World:
    """Утро 13.11: набор действует, позиции — как в таблице 10.11."""
    world.now = datetime(2026, 11, 13, 10, 0, tzinfo=MOSCOW)
    body = {"valid_from": "2026-11-01", "comment": "Политика версии 1.0", "body": model.model_set()}
    risk.ok("POST", f"/funds/{model.FUND}/sets", body)
    return world


def trade(model: ModuleType, **fields: Any) -> dict[str, Any]:
    secid = fields.pop("secid", "A-FLOAT")
    return {
        "fund_id": str(model.FUND),
        "kind": "outright",
        "side": "buy",
        "instrument_id": str(model.PAPER[secid]["id"]),
        "secid": secid,
        "quantity": 15_000,
        "amount": str(15 * MLN),
        **fields,
    }


def rows(found: dict[str, Any]) -> list[tuple[Any, ...]]:
    """Лимит, группа, было → стало, статус до и после."""
    return [
        (
            f"{c['code']} {c['label'] or ''}".strip(),
            c["group"],
            c["before"],
            c["after"],
            c["before_status"],
            c["after_status"],
        )
        for c in found["changes"]
    ]


@pytest.mark.usefixtures("morning")
def test_buying_issuer_a(trader: Api, world: World, model: ModuleType) -> None:
    found = trader.ok("POST", "/check", trade(model))

    # Л3 на эмитента: 9,64% → 10,18% при лимите 10% — после сделки лимит будет нарушен.
    assert rows(found) == [
        ("Л2", None, "0.4319", "0.4321", "ok", "ok"),
        ("7.3", None, "0.4319", "0.4321", "ok", "ok"),
        # 13.11 Эмитент Г уже в группе AA — в Л3 на класс его нет.
        ("Л3 на класс", "все бумаги проверки", "48.9286", "49.4643", "ok", "ok"),
        ("Л3 на эмитента", "Эмитент А", "9.6429", "10.1786", "warning", "breach"),
    ]
    # Покупка за деньги активы не меняет.
    assert found["before"]["assets"] == found["after"]["assets"] == "2800000000.00"
    assert (found["date"], found["limit_set"]["version"], found["calculated"]) == (
        "2026-11-13",
        1,
        True,
    )
    # Только информация: в журнал проверка ничего не пишет.
    assert trader.ok("GET", f"/breaches?fund_id={model.FUND}") == []
    assert world.now.date() == date(2026, 11, 13)


@pytest.mark.usefixtures("morning")
def test_direct_repo(risk: Api, model: ModuleType) -> None:
    repo = {"fund_id": str(model.FUND), "kind": "repo", "side": "sell", "amount": str(300 * MLN)}
    found = risk.ok("POST", "/check", repo)

    # Прямое РЕПО на 300,0: активы 3 100,0, плечо 3,10; доли в активах падают.
    assert (found["after"]["assets"], found["after"]["leverage"]) == ("3100000000.00", "3.1000")
    assert [r[:4] for r in rows(found)][:3] == [
        ("Л1", None, "2.8000", "3.1000"),
        ("Л2", None, "0.4319", "0.3901"),
        ("7.3", None, "0.4319", "0.3901"),
    ]
    assert [(r[0], r[4], r[5]) for r in rows(found)[3:]] == [
        ("Л3 на эмитента", "warning", "ok"),
        # Эмитент Г: 75,0 / 3 100,0 = 2,42% — нарушение уходит, но близко к лимиту.
        ("Л4 на эмитента", "breach", "warning"),
        ("Л5 на выпуск", "warning", "ok"),
        ("Л7 на эмитента", "warning", "ok"),
    ]

    # Обратное РЕПО: деньги стали размещением — активы те же, лимиты не меняются.
    found = risk.ok("POST", "/check", repo | {"side": "buy", "amount": str(10 * MLN)})
    assert (found["after"]["assets"], found["changes"]) == ("2800000000.00", [])


@pytest.mark.usefixtures("morning")
def test_short_and_new_paper(trader: Api, model: ModuleType) -> None:
    # Продали облигаций Эмитента А больше, чем было: короткая корпоративная — не по правилам.
    sell = trade(model, side="sell", quantity=300_000, amount=str(300 * MLN))
    found = trader.ok("POST", "/check", sell)
    eligible = next(c for c in found["changes"] if c["code"] == "4")
    assert (eligible["before_status"], eligible["after_status"]) == ("ok", "breach")
    assert eligible["reasons"] == [
        "короткая позиция: правило «корпоративные, старший долг» её не разрешает"
    ]
    a = next(c for c in found["changes"] if c["label"] == "на эмитента" and c["code"] == "Л3")
    # Короткая позиция — со знаком минус: −30,0 / 2 830,0.
    assert (a["before"], a["after"], a["group"]) == ("9.6429", "-1.0601", "Эмитент А")

    # Паи SBMM — бумаги в позициях нет; правила разрешают, долей и дюрации не меняют.
    found = trader.ok(
        "POST", "/check", trade(model, secid="SBMM", quantity=10_000, amount=str(10 * MLN))
    )
    assert found["changes"] == []


@pytest.mark.usefixtures("morning")
def test_a_trader_of_one_portfolio_sees_the_fund_limits(
    trader: Api, world: World, model: ModuleType
) -> None:
    # Трейдер ведёт только портфель облигаций фонда — доступа к самому фонду у него нет.
    world.hidden["trading"] = {str(model.FUND), str(model.OTHER_FUND)}
    assert trader.ok("GET", "/funds") == []

    # Данные фонда сервис сделок трейдеру не отдаёт — их читает учётная запись am-limits.
    found = trader.ok("POST", "/check", trade(model))
    assert (found["fund_id"], rows(found)[-1][1:]) == (
        str(model.FUND),
        ("Эмитент А", "9.6429", "10.1786", "warning", "breach"),
    )
    # Токен служебной учётной записи — один на несколько проверок.
    trader.ok("POST", "/check", trade(model, quantity=1, amount="1000"))
    assert world.tokens_issued == 1


@pytest.mark.usefixtures("morning")
def test_without_the_service_account(
    trader: Api,
    world: World,
    model: ModuleType,
    settings: LimitsSettings,
    tokens: TokenFactory,
) -> None:
    world.hidden["trading"] = {str(model.FUND), str(model.OTHER_FUND)}
    # Keycloak недоступен — проверка недоступна, сделку ввести всё равно можно.
    world.keycloak_down = True
    assert trader.refused("POST", "/check", trade(model)) == (503, "keycloak_unavailable")

    # Секрета нет — читать данные фонда за трейдера нечем.
    bare = settings.model_copy(update={"keycloak_client_secret": None})
    app = build_app(bare, http_transport=world.transport(), clock=lambda: world.now)
    with TestClient(app) as client:
        alone = model.Api(client, tokens, "trading")
        assert alone.refused("POST", "/check", trade(model)) == (503, "keycloak_unavailable")


def test_without_a_set_or_a_calculation(risk: Api, world: World, model: ModuleType) -> None:
    world.now = datetime(2026, 11, 13, 10, 0, tzinfo=MOSCOW)
    found = risk.ok("POST", "/check", trade(model))
    assert (found["limit_set"], found["calculated"], found["changes"]) == (None, False, [])

    body = {"valid_from": "2026-11-01", "comment": "Политика версии 1.0", "body": model.model_set()}
    risk.ok("POST", f"/funds/{model.FUND}/sets", body)
    world.not_calculated.add(date(2026, 11, 13))
    found = risk.ok("POST", "/check", trade(model))
    assert (found["limit_set"]["version"], found["calculated"], found["before"]) == (1, False, None)


@pytest.mark.usefixtures("morning")
def test_who_sees_the_check(
    risk: Api, trader: Api, client: TestClient, tokens: TokenFactory, model: ModuleType
) -> None:
    # Сопровождение сделки не вводит — проверка до ввода ему не нужна.
    assert (
        model.Api(client, tokens, "operations").call("POST", "/check", trade(model)).status_code
        == 403
    )
    assert trader.refused("POST", "/check", trade(model, fund_id=str(model.OTHER_FUND))) == (
        404,
        "not_found",
    )
    assert risk.refused("POST", "/check", trade(model, instrument_id=None))[0] == 422
    assert risk.refused("POST", "/check", trade(model, fund_id=str(model.BONDS))) == (
        404,
        "not_found",
    )
    assert risk.refused("POST", "/check", trade(model, amount="0"))[0] == 422
