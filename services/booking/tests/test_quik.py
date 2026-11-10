"""Загрузка сделок из отчёта QUIK (решение 151): разбор, комиссии по тарифу, проверка и запись.

Отчёт в fixtures/quik — синтетический: разметка как у настоящего отчёта QUIK
(незакрытые ячейки, x:num, строки итогов без </TR>), сделки выдуманы.
"""

from __future__ import annotations

import base64
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import insert, select

from am_common.app import Resources
from am_common.clock import MOSCOW
from am_common.testing import TokenFactory
from booking.db import fund_closing, outbox, trade_version
from booking.quik import QuikFormatError, parse_report

if TYPE_CHECKING:
    from conftest import World

pytestmark = pytest.mark.integration

REPORT = (Path(__file__).parent / "fixtures" / "quik" / "report.xls").read_bytes()
FUND = UUID("00000000-0000-0000-0000-00000000f001")
BONDS = UUID("00000000-0000-0000-0000-00000000b001")
ACCOUNT = UUID(int=BONDS.int + 1)  # счёт портфеля в фейке портфелей
RZD = UUID(int=1054)  # РЖД 1Р-54R: в справочнике её нет, пока тест не заведёт


def add_rzd(world: World) -> None:
    """Пользователь завёл бумагу в справочнике."""
    world.add_instrument(RZD, "RU000A10FV69", world.ofz_terms())


TARIFF = {"outright_rate": "0.0125", "repo_rate": "0.5", "valid_from": "2026-11-01"}


class Api:
    def __init__(self, client: TestClient, tokens: TokenFactory, username: str, *roles: str):
        self._client = client
        token = tokens.issue(subject=f"u-{username}", username=username, roles=roles)
        self._headers = {"Authorization": f"Bearer {token}"}

    def call(self, method: str, path: str, body: Any = None) -> Response:
        return self._client.request(method, path, json=body, headers=self._headers)

    def ok(self, method: str, path: str, body: Any = None) -> Any:
        response = self.call(method, path, body)
        assert response.is_success, response.text
        return response.json()

    def problem(self, method: str, path: str, body: Any = None) -> tuple[int, str]:
        response = self.call(method, path, body)
        return response.status_code, response.json()["detail"]["code"]


@pytest.fixture
def trader(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "trader", "trading")


@pytest.fixture
def ops(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "ops", "operations")


@pytest.fixture
def admin(client: TestClient, tokens: TokenFactory) -> Api:
    return Api(client, tokens, "admin", "admin")


def upload(content: bytes = REPORT, **fields: Any) -> dict[str, Any]:
    return {
        "file": base64.b64encode(content).decode("ascii"),
        "portfolio_id": str(BONDS),
        **fields,
    }


def outcome(report: dict[str, Any]) -> list[tuple[str, str | None, str | None]]:
    """Номер сделки, результат или код отказа, комиссия."""
    return [
        (line["external_id"], line["result"] or line["error"], line["fee"])
        for line in report["lines"]
    ]


async def trades_in_db(resources: Resources) -> int:
    async with resources.require_database().session() as session:
        return len((await session.execute(select(trade_version.c.trade_id))).all())


CHECKED = [
    # ОФЗ 26238: оборот без НКД 52 500,00 × 0,0125% = 6,5625 → 6,56.
    ("18100000001", "created", "6.56"),
    # Линкер: 1 068 581,85 × 0,0125% = 133,57.
    ("18100000002", "created", "133.57"),
    ("18100000003", "created", "2.25"),  # паи: 18 000,00 × 0,0125%
    ("18100000004", "created", "1.25"),  # РЖД 1Р-54R: 9 965,00 × 0,0125%
    ("18100000005", "amount_mismatch", "0.20"),
    ("18100000006", "bad_row", None),
    # Обратное РЕПО с КСУ: 30 000 000 × 0,5% годовых × 3 дня / 365 = 1 232,88.
    ("18100000101", "created", "1232.88"),
    ("18100000201", "created", "315.07"),  # прямое: 23 000 000 × 0,5% / 365
    ("18100000301", "bond_repo", None),
    ("18100000401", "bad_repo", None),
    ("18100000402", "bad_repo", None),
]


async def test_check_then_load(admin: Api, trader: Api, resources: Resources, world: World) -> None:
    admin.ok("POST", f"/accounts/{ACCOUNT}/tariffs", TARIFF)

    checked = trader.ok("POST", "/imports/quik", upload())

    assert (checked["client_code"], checked["period_from"], checked["period_till"]) == (
        "00000T_01",
        "2026-11-27",
        "2026-11-30",
    )
    assert checked["applied"] is False
    assert checked["tariff"]["outright_rate"] == "0.012500"
    # Новой бумаги в справочнике нет: проверка говорит какой, загрузка не пройдёт.
    assert checked["new_instruments"] == [{"code": "RU000A10FV69", "name": "РЖД 1Р-54R"}]
    assert outcome(checked)[3][1] == "unknown_instrument"
    refused = trader.call("POST", "/imports/quik", upload(apply=True)).json()["detail"]
    assert (refused["code"], refused["message"]) == (
        "new_instruments",
        "В отчёте бумаги, которых нет в справочнике: RU000A10FV69 (РЖД 1Р-54R). Заведите их "
        "в разделе «Справочник» и загрузите отчёт снова.",
    )
    assert await trades_in_db(resources) == 0

    add_rzd(world)
    checked = trader.ok("POST", "/imports/quik", upload())
    assert checked["new_instruments"] == []
    assert outcome(checked) == CHECKED
    linker = next(line for line in checked["lines"] if line["external_id"] == "18100000002")
    assert (linker["kind"], linker["side"], linker["amount"], linker["fee_rate"]) == (
        "outright",
        "buy",
        "1078071.85",
        "0.012500",
    )
    mismatch = next(line for line in checked["lines"] if line["error"] == "amount_mismatch")
    assert mismatch["message"] == (
        "Сумма по расчёту системы 1680.03 ₽ не сходится с отчётом 1680.04 ₽ (объём и НКД) "
        "— проверьте сделку."
    )
    # Проверка ничего не записала.
    assert await trades_in_db(resources) == 0

    loaded = trader.ok("POST", "/imports/quik", upload(apply=True))
    assert loaded["applied"] is True
    assert outcome(loaded) == CHECKED
    trade_id = next(line["trade_id"] for line in loaded["lines"] if line["result"] == "created")
    trade = trader.ok("GET", f"/trades/{trade_id}")
    assert (trade["external_id"], trade["fee"], trade["face_value"], trade["accrued"]) == (
        "18100000001",
        "6.56",
        "1000.000000",
        "35.01",
    )
    assert trade["versions"][0]["comment"] == "Загружена из отчёта QUIK, код клиента 00000T_01."
    repo = next(line for line in loaded["lines"] if line["external_id"] == "18100000101")
    found = trader.ok("GET", f"/trades/{repo['trade_id']}")
    assert (
        found["kind"],
        found["side"],
        found["amount"],
        found["repo_rate"],
        found["second_date"],
        found["second_amount"],
        found["fee"],
    ) == ("repo", "buy", "30000000.00", "14.200000", "2026-11-30", "30035013.70", "1232.88")

    # Тот же отчёт ещё раз — без изменений.
    again = trader.ok("POST", "/imports/quik", upload(apply=True))
    assert {line["result"] for line in again["lines"] if line["result"]} == {"unchanged"}

    # Свои ставки на загрузку: новая комиссия — новые версии сделок купли-продажи.
    custom = trader.ok("POST", "/imports/quik", upload(apply=True, outright_rate="0.02"))
    assert outcome(custom)[:3] == [
        ("18100000001", "amended", "10.50"),
        ("18100000002", "amended", "213.72"),
        ("18100000003", "amended", "3.60"),
    ]
    assert outcome(custom)[6][1] == "unchanged"  # ставка РЕПО — из тарифа, как была


async def test_confirmed_trade_and_closed_day(
    admin: Api, trader: Api, ops: Api, resources: Resources, world: World
) -> None:
    add_rzd(world)
    admin.ok("POST", f"/accounts/{ACCOUNT}/tariffs", TARIFF)
    loaded = trader.ok("POST", "/imports/quik", upload(apply=True))
    first = loaded["lines"][0]["trade_id"]
    ops.ok("POST", f"/trades/{first}/confirm", {"comment": None})

    # Подтверждённую сделку трейдинг не правит — проверка скажет это до записи.
    checked = trader.ok("POST", "/imports/quik", upload(outright_rate="0.02"))
    assert outcome(checked)[0][1] == "confirmed_trade"

    # Закрытый день: строки за 27.11 отклоняются, остальные проходят.
    async with resources.require_database().session() as session, session.begin():
        await session.execute(
            insert(fund_closing).values(
                fund_id=FUND,
                action="close",
                first_day=date(2026, 11, 27),
                last_day=date(2026, 11, 27),
                closed_through=date(2026, 11, 27),
                author="ops",
                created_at=datetime(2026, 11, 28, 10, tzinfo=MOSCOW),
            )
        )
    for apply in (False, True):
        late = trader.ok("POST", "/imports/quik", upload(apply=apply, repo_rate="0.6"))
        results = dict((n, r) for n, r, _ in outcome(late))
        assert (results["18100000101"], results["18100000201"]) == ("day_closed", "amended")


async def test_rates_without_a_tariff(trader: Api, world: World) -> None:
    add_rzd(world)
    checked = trader.ok("POST", "/imports/quik", upload())
    assert checked["tariff"] is None
    refused = next(line for line in checked["lines"] if line["external_id"] == "18100000001")
    assert (refused["error"], refused["message"]) == (
        "no_tariff",
        "Нет тарифа брокера на 27.11.2026 — задайте ставку на загрузку или попросите "
        "администратора завести тариф счёта.",
    )
    assert outcome(checked)[6][1] == "no_tariff"
    # Ставки на загрузку — и без тарифа.
    custom = trader.ok("POST", "/imports/quik", upload(outright_rate="0.0125", repo_rate="0.5"))
    assert outcome(custom) == CHECKED


def test_files_that_are_not_a_report(trader: Api) -> None:
    assert trader.problem("POST", "/imports/quik", upload(b"<html><body>hi</body></html>")) == (
        422,
        "not_quik_report",
    )
    no_volume = REPORT.replace("<TH>Объём</TH>".encode("windows-1251"), b"<TH>X</TH>")
    detail = trader.call("POST", "/imports/quik", upload(no_volume)).json()["detail"]
    assert detail["message"] == "В отчёте нет колонок: Объём."
    assert trader.problem("POST", "/imports/quik", {**upload(), "file": "не base64"}) == (
        422,
        "bad_file",
    )
    huge = upload(b"x" * (5 * 1024 * 1024 + 1))
    assert trader.problem("POST", "/imports/quik", huge) == (413, "file_too_large")
    # Compound-портфель счёта не имеет; чужой портфель не виден.
    assert trader.problem("POST", "/imports/quik", upload(portfolio_id=str(FUND))) == (
        422,
        "no_account",
    )
    hidden = upload(portfolio_id="00000000-0000-0000-0000-00000000b002")
    assert trader.problem("POST", "/imports/quik", hidden) == (404, "portfolio_unavailable")


def test_report_encodings_and_quirks() -> None:
    # Отчёт в UTF-8 с меткой порядка байтов и незнакомая кодировка в заголовке.
    text = REPORT.decode("windows-1251")
    assert len(parse_report(b"\xef\xbb\xbf" + text.encode("utf-8")).deals) == 5
    unknown = text.replace("charset=windows-1251", "charset=x-nothing")
    assert len(parse_report(unknown.encode("windows-1251")).deals) == 5
    # Количество не целое, цена не больше нуля, не число — строка не разбирается.
    report = parse_report(
        REPORT.replace(b'x:num="100">100', b'x:num="1.5">1.5')
        .replace(b'x:num="18000.00"', b'x:num="abc"')
        .replace(b'x:num="99.75"', b'x:num="0"')
    )
    assert {p.number: p.message for p in report.problems if p.code == "bad_row"} == {
        "18100000001": "Строку не разобрать: количество 1.5 — не целое число бумаг.",
        "18100000002": "Строку не разобрать: цена 0 не больше нуля.",
        "18100000003": "Строку не разобрать: в колонке «Объём» не число: 'abc'.",
        "18100000006": "Строку не разобрать: направление «Обмен» — не купля и не продажа.",
    }
    with pytest.raises(QuikFormatError):
        parse_report(b"")


def test_repo_structure_problems() -> None:
    def repo_problems(content: bytes) -> set[str]:
        return {p.message for p in parse_report(content).problems if p.code == "bad_repo"}

    # Обе части в одну сторону; вторая часть не позже первой.
    one_way = REPORT.replace(
        "<TD>18100000103</TD>\r\n<TD>86400000101</TD>\r\n<TD>РЕПО с ЦК</TD>\r\n"
        "<TD>RU000A0JW4Z1</TD>\r\n<TD>КСУ обл.</TD>\r\n<TD>\r\n<TD>Продажа".encode("windows-1251"),
        "<TD>18100000103</TD>\r\n<TD>86400000101</TD>\r\n<TD>РЕПО с ЦК</TD>\r\n"
        "<TD>RU000A0JW4Z1</TD>\r\n<TD>КСУ обл.</TD>\r\n<TD>\r\n<TD>Купля".encode("windows-1251"),
    )
    assert "РЕПО: не найти первую и вторую части." in repo_problems(one_way)
    same_day = REPORT.replace(
        b"<TD>30.11.2026</TD>\r\n<TD>\r\n<TD>\r\n<TD>10001</TD>",
        b"<TD>27.11.2026</TD>\r\n<TD>\r\n<TD>\r\n<TD>10001</TD>",
        1,
    )
    assert "РЕПО: вторая часть не позже первой." in repo_problems(same_day)


async def test_tariffs(admin: Api, trader: Api, resources: Resources) -> None:
    assert trader.ok("GET", f"/accounts/{ACCOUNT}/tariffs") == []
    assert trader.call("POST", f"/accounts/{ACCOUNT}/tariffs", TARIFF).status_code == 403
    admin.ok("POST", f"/accounts/{ACCOUNT}/tariffs", TARIFF | {"valid_from": "2026-12-01"})
    found = admin.ok(
        "POST", f"/accounts/{ACCOUNT}/tariffs", TARIFF | {"comment": "Тариф брокера с ноября"}
    )
    assert [(t["valid_from"], t["outright_rate"], t["repo_rate"], t["author"]) for t in found] == [
        ("2026-11-01", "0.012500", "0.500000", "admin"),
        ("2026-12-01", "0.012500", "0.500000", "admin"),
    ]
    assert trader.ok("GET", f"/accounts/{ACCOUNT}/tariffs") == found
    unknown = f"/accounts/{UUID(int=999)}/tariffs"
    assert admin.problem("POST", unknown, TARIFF) == (404, "account_not_found")
    negative = TARIFF | {"repo_rate": "-1"}
    assert admin.call("POST", f"/accounts/{ACCOUNT}/tariffs", negative).status_code == 422
    async with resources.require_database().session() as session:
        events = (await session.execute(select(outbox.c.payload))).scalars().all()
    tariff_events = [e for e in events if e["type"] == "broker_tariff.set"]
    assert tariff_events[-1]["data"] == {
        "portfolios": ["OPIF1-BONDS"],
        "after": {"outright_fee": "0.0125", "repo_fee": "0.5", "valid_from": "2026-11-01"},
    }


def test_feed_checks_without_writing(trader: Api) -> None:
    trade = {
        "external_id": "X-1",
        "portfolio": "OPIF1-BONDS",
        "code": "SU26238RMFS4",
        "side": "buy",
        "quantity": 10,
        "price": 52.5,
        "trade_at": "2026-11-27T11:00:00+03:00",
        "settle_date": "2026-11-30",
    }
    body = {"source": "broker-x", "trades": [trade]}
    checked = trader.ok("POST", "/feed/v1/trades?dry_run=true", body)
    assert (checked["created"], checked["trades"][0]["trade_id"]) == (1, None)
    trader.ok("POST", "/feed/v1/trades", body)
    cancel = {"source": "broker-x", "trades": [trade | {"cancelled": True}]}
    assert trader.ok("POST", "/feed/v1/trades?dry_run=true", cancel)["cancelled"] == 1
    # Проверка отмену не записала: сделка действует.
    assert trader.ok("POST", "/feed/v1/trades", body)["unchanged"] == 1
