"""Детализация расчёта в Excel (решение 158): книга считает то же, что сервис.

Тест не верит записанным в книгу результатам формул: он пересчитывает каждую
формулу маленьким вычислителем (арифметика, LN, SUM, AVERAGE, IF, ISNUMBER,
XIRR, именованные ячейки, ссылки между листами) и сверяет результат с записанным
и с ответом API. Все «Расхождения» Excel и сервиса — около нуля.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterator
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from typing import TYPE_CHECKING, Any
from uuid import UUID

import openpyxl
import pytest
from fastapi.testclient import TestClient
from openpyxl.utils.cell import range_boundaries

from am_common.app import Resources
from am_common.clock import MOSCOW
from am_common.testing import TokenFactory
from marketdata.commands import refresh_instruments
from marketdata.db import accrued_interest
from marketdata.main import build_app
from marketdata.refdata import refdata_client
from marketdata.repository import SETTLEMENT, Repository
from marketdata.scheduler import RatesLoader, iss_client, poll_if_due
from marketdata.settings import MarketdataSettings

if TYPE_CHECKING:
    from conftest import World

pytestmark = pytest.mark.integration

OFZ = UUID("00000000-0000-0000-0000-000000026238")
LINKER = UUID("00000000-0000-0000-0000-00000010f504")
RZD = UUID("00000000-0000-0000-0000-0000000a0e06")
GPN = UUID("00000000-0000-0000-0000-0000000a0e08")
AT = datetime(2026, 10, 28, 15, 0, tzinfo=MOSCOW)
NOW = datetime(2026, 10, 27, 11, 5, tzinfo=MOSCOW)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EPOCH = datetime(1899, 12, 30)


class Api:
    def __init__(self, client: TestClient, tokens: TokenFactory) -> None:
        self._client = client
        self._trader = _auth(tokens, "trader", "trading")
        self._script = _auth(tokens, "script", "price_feed")

    def analytics(self, instrument_id: UUID, **params: Any) -> Any:
        response = self._client.get(
            f"/instruments/{instrument_id}/analytics", params=params, headers=self._trader
        )
        assert response.is_success, response.text
        return response.json()

    def workbook(self, instrument_id: UUID, **params: Any) -> Book:
        response = self._client.get(
            f"/instruments/{instrument_id}/analytics.xlsx", params=params, headers=self._trader
        )
        assert response.is_success, response.text
        assert response.headers["content-type"] == XLSX
        assert response.headers["content-disposition"].startswith("attachment; filename=")
        return Book(response.content)

    def ruonia(self, *days: tuple[str, float]) -> None:
        items = [{"rate": "ruonia", "date": d, "value": v} for d, v in days]
        response = self._client.post(
            "/feed/v1/rates", json={"source": "cbr", "rates": items}, headers=self._script
        )
        assert response.is_success, response.text
        assert response.json()["rejected"] == []


def _auth(tokens: TokenFactory, username: str, role: str) -> dict[str, str]:
    token = tokens.issue(subject=f"u-{username}", username=username, roles=[role])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def api(settings: MarketdataSettings, world: World, tokens: TokenFactory) -> Iterator[Api]:
    """Сервис в 15:00 28.10.2026."""
    app = build_app(settings, http_transport=world.transport(), clock=lambda: AT)
    with TestClient(app) as client:
        yield Api(client, tokens)


# --- вычислитель формул ---------------------------------------------------------------

TOKEN = re.compile(
    r"""(?P<sheetref>'(?P<sheet>[^']+)'!(?P<sref>\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?))
      |(?P<func>[A-Z]+)\(
      |(?P<ref>\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?)
      |(?P<name>[^\W\d]\w*)
      |(?P<number>\d+(?:\.\d+)?)
      |(?P<op>[-+*/^(),])""",
    re.VERBOSE,
)
DIFFERENCE = re.compile(r"=B(\d+)-C\1")


class Blank(float):
    """Пустая ячейка: в арифметике — 0, но не число для ISNUMBER."""


def _xirr(values: list[float], dates: list[float]) -> float:
    """Ставка, при которой Σ v·(1+r)^(−(d − d₀)/365) = 0, — как ЧИСТВНДОХ."""

    def npv(rate: float) -> float:
        return math.fsum(
            v / (1 + rate) ** ((d - dates[0]) / 365) for v, d in zip(values, dates, strict=True)
        )

    low, high = -0.99, 10.0
    for _ in range(200):
        middle = (low + high) / 2
        if npv(middle) > 0:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def _numbers(values: list[Any]) -> list[float]:
    return [v for v in values if isinstance(v, float) and not isinstance(v, Blank)]


FUNCTIONS: dict[str, Callable[..., Any]] = {
    "LN": math.log,
    "SUM": lambda values: sum(_numbers(values)),
    "AVERAGE": lambda values: sum(_numbers(values)) / len(_numbers(values)),
    "IF": lambda condition, yes, no: yes if condition else no,
    "ISNUMBER": lambda value: isinstance(value, float) and not isinstance(value, Blank),
    "XIRR": _xirr,
}


class Book:
    def __init__(self, content: bytes) -> None:
        self.formulas = openpyxl.load_workbook(BytesIO(content))
        self.cached = openpyxl.load_workbook(BytesIO(content), data_only=True)
        self.names: dict[str, tuple[str, str]] = {}
        for name, defined in self.formulas.defined_names.items():
            sheet, ref = defined.attr_text.split("!")
            self.names[name] = (sheet.strip("'"), ref.replace("$", ""))
        self.values: dict[tuple[str, str], Any] = {}
        self.differences: list[tuple[str, str, float]] = []
        # Сначала ряд базиса, сверху вниз: каждая строка ссылается на строку выше, а на
        # последнюю — лист «Фондирование»; иначе за 5 лет рекурсия ушла бы на 1826 шагов.
        first = sorted(self.formulas.worksheets, key=lambda ws: ws.title != "Базис по дням")
        for ws in first:
            for row in ws.iter_rows():
                for cell in row:
                    self.value(ws.title, cell.coordinate)

    @property
    def sheets(self) -> list[str]:
        return list(self.formulas.sheetnames)

    def value(self, sheet: str, ref: str) -> Any:
        key = (sheet, ref)
        if key not in self.values:
            self.values[key] = self._evaluate(sheet, ref)
        return self.values[key]

    def name(self, name: str) -> Any:
        return self.value(*self.names[name])

    def labelled(self, sheet: str, label: str, column: str = "B") -> Any:
        """Значение в строке с подписью ``label`` в первом столбце."""
        row = next(c.row for c in self.formulas[sheet]["A"] if c.value == label)
        return self.value(sheet, f"{column}{row}")

    def _evaluate(self, sheet: str, ref: str) -> Any:
        cell = self.formulas[sheet][ref]
        raw = cell.value
        if cell.data_type != "f":
            return _plain(raw)
        found = self._formula(sheet, raw[1:])
        cached = _plain(self.cached[sheet][ref].value)
        assert isinstance(found, float), (sheet, ref, raw)
        assert abs(found - cached) <= 1e-7 * max(1.0, abs(cached)), (sheet, ref, raw, found, cached)
        # «Расхождение» — столбец D: B − C. В ряду базиса B − C — это RUONIA − КС.
        if DIFFERENCE.fullmatch(raw) and ref.startswith("D") and sheet != "Базис по дням":
            self.differences.append((sheet, ref, found))
        return found

    def _formula(self, sheet: str, formula: str) -> Any:
        python, position = [], 0
        while position < len(formula):
            match = TOKEN.match(formula, position)
            assert match is not None, (formula, position)
            position = match.end()
            if match["sheetref"]:
                python.append(f"R({match['sheet']!r}, {match['sref']!r})")
            elif match["func"]:
                python.append(f"F[{match['func']!r}](")
            elif match["ref"]:
                python.append(f"R({sheet!r}, {match['ref']!r})")
            elif match["name"]:
                python.append(f"R(*N[{match['name']!r}])")
            elif match["number"]:
                python.append(repr(float(match["number"])))  # числа Excel — дробные
            elif match["op"] == "^":
                python.append("**")
            else:
                python.append(match[0])
        scope = {"R": self._range, "F": FUNCTIONS, "N": self.names}
        return eval("".join(python), scope)  # noqa: S307 — формулы из нашей же книги

    def _range(self, sheet: str, ref: str) -> Any:
        ref = ref.replace("$", "")
        if ":" not in ref:
            return self.value(sheet, ref)
        left, top, right, bottom = range_boundaries(ref)
        ws = self.formulas[sheet]
        return [
            self.value(sheet, ws.cell(row, col).coordinate)
            for row in range(top, bottom + 1)
            for col in range(left, right + 1)
        ]


def _plain(value: Any) -> Any:
    """Значение ячейки как в Excel: даты — числами, пусто — Blank."""
    if value is None:
        return Blank(0)
    if isinstance(value, datetime):
        return (value - EPOCH).total_seconds() / 86_400
    if isinstance(value, int | float):
        return float(value)
    return value


def checked(book: Book) -> Book:
    """Все расхождения Excel и сервиса — около нуля."""
    assert book.differences
    worst = max(book.differences, key=lambda d: abs(d[2]))
    assert abs(worst[2]) < 1e-6, worst
    return book


def close(value: float, expected: str | Decimal, tolerance: float) -> bool:
    return abs(value - float(expected)) <= tolerance


# --- данные -----------------------------------------------------------------------------


async def bonds(resources: Resources, world: World, **floater: Any) -> None:
    world.add_bond(RZD, "RU000A0ZZ4P9", "РЖД 1Р-06R", world.recorded_terms("RU000A0ZZ4P9"))
    world.add_bond(
        GPN, "RU000A107HG1", "Газпнф3P8R", world.recorded_terms("RU000A107HG1") | floater
    )
    await refresh_instruments(resources.require_database(), refdata_client(resources))
    settle = datetime(2026, 10, 29).date()
    async with resources.require_database().session() as session, session.begin():
        await Repository(session).upsert_points(
            accrued_interest,
            [
                {"instrument_id": i, "as_of": settle, "source": SETTLEMENT, "value": Decimal(v)}
                for i, v in ((RZD, "8.62"), (GPN, "6.71"))
            ],
        )
    await RatesLoader(iss_client(resources)).load_due(resources, refdata_client(resources), AT)


# --- тесты ------------------------------------------------------------------------------


async def test_fixed_coupon_bond(api: Api, resources: Resources) -> None:
    await refresh_instruments(resources.require_database(), refdata_client(resources))

    found = api.analytics(OFZ, price="52.493")
    book = checked(api.workbook(OFZ, price="52.493"))

    assert book.sheets == ["Итог", "Условия", "Потоки к погашению", "Методика"]
    [maturity] = found["yields"]
    assert close(book.name("Цена_грязная"), found["price"]["dirty"], 1e-9)
    assert close(book.name("Доходность_погашение_XIRR") * 100, maturity["yield_percent"], 1e-6)
    label = "Доходность к погашению 15.05.2041, непрерывная"
    smooth = maturity["continuous"]
    assert close(book.labelled("Итог", label) * 100, smooth["yield_percent"], 1e-6)
    dv01 = book.labelled("Итог", "DV01, непрерывные, ₽ за 1 бп")
    assert close(dv01, smooth["dv01"], 1e-6)


async def test_offer_and_unknown_coupons(api: Api, resources: Resources, world: World) -> None:
    await bonds(resources, world)

    book = checked(api.workbook(RZD, price="103.14"))

    # К погашению не посчитать — купоны не объявлены: листа нет, в итоге — причина.
    assert book.sheets == ["Итог", "Условия", "Потоки к оферте", "Методика"]
    assert book.labelled("Итог", "Доходность к погашению 04.04.2033") == "не посчитать"
    assert book.labelled("Итог", "Доходность к погашению 04.04.2033", "E").startswith("Купон")


async def test_key_rate_floater_with_basis_history(
    api: Api, resources: Resources, world: World
) -> None:
    gpn = {"rate": "key_rate", "margin_bp": "130", "capitalized": False}
    await bonds(resources, world, floater=gpn)
    # Пт 23.10 и Пн 26.10 — с выходными между ними: базис держится.
    api.ruonia(("2026-10-22", 13.80), ("2026-10-23", 13.95), ("2026-10-26", 14.05))
    api.ruonia(("2026-10-27", 13.90))

    spread = api.analytics(GPN, price="100.07")["spread"]
    book = checked(api.workbook(GPN, price="100.07"))

    assert book.sheets == [
        "Итог",
        "Условия",
        "Фондирование",
        "Потоки",
        "Разложение спреда",
        "Базис по дням",
        "Методика",
    ]
    funding = spread["funding"]
    assert close(book.name("Базис") * 100, funding["basis_bp"], 0.005)
    assert close(book.name("RUONIA_оценка"), funding["value_percent"], 1e-6)
    assert close(book.name("Индекс"), spread["index"]["value"], 1e-9)
    assert isinstance(book.value("Базис по дням", "B7"), Blank)  # 24.10 — суббота
    annual = book.labelled("Итог", "Спред, эффективный годовой, бп")
    assert close(annual, spread["spread_bp"], 0.005)
    continuous = book.labelled("Итог", "Спред, непрерывный, бп")
    assert close(continuous, spread["continuous"]["spread_bp"], 0.005)


async def test_capitalized_ruonia_floater_by_continuous_spread(
    api: Api, resources: Resources, world: World
) -> None:
    gpn = {"rate": "ruonia", "margin_bp": "130", "capitalized": True}
    await bonds(resources, world, floater=gpn)
    api.ruonia(("2026-10-27", 13.90))

    found = api.analytics(GPN, spread="80", measure="continuous")
    book = checked(api.workbook(GPN, spread="80", measure="continuous"))

    source = book.labelled("Итог", "Откуда цена")
    assert source == "«что если»: цена по введённому спреду — в непрерывной капитализации"
    assert close(book.name("Цена_чистая"), found["price"]["clean"], 1e-6)
    # Индекс флоатера на RUONIA — та же оценка RUONIA.
    assert book.name("Индекс") == book.name("RUONIA_оценка")
    assert close(book.labelled("Итог", "Спред, непрерывный, бп"), "80", 1e-6)


async def test_linker(resources: Resources, client: TestClient, tokens: TokenFactory) -> None:
    await refresh_instruments(resources.require_database(), refdata_client(resources))
    await poll_if_due(resources, refdata_client(resources), iss_client(resources), NOW)
    await RatesLoader(iss_client(resources)).load_due(resources, refdata_client(resources), AT)
    api = Api(client, tokens)
    api.ruonia(("2026-10-26", 13.90))

    spread = api.analytics(LINKER, price="99.75")["spread"]
    book = checked(api.workbook(LINKER, price="99.75"))

    annual = book.labelled("Итог", "Спред, эффективный годовой, бп")
    assert close(annual, spread["spread_bp"], 0.005)
    breakdown = book.labelled("Разложение спреда", "Купон")  # первая из двух таблиц — годовые
    assert close(breakdown, spread["breakdown"]["margin_bp"], 0.005)
