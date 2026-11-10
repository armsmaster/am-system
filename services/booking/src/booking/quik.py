"""Загрузка сделок из отчёта QUIK «Отчёт по всем сделкам клиента» (решение 151).

Отчёт — HTML-таблица с расширением .xls в windows-1251: так QUIK выгружает отчёт
для Excel. В нём два раздела: обычные сделки и РЕПО с ЦК. Каждое РЕПО — три
строки с одним номером заявки: сама сделка (в цене — ставка) и две части.

Комиссии в отчёте нет — её считает система по тарифу брокера на счёт портфеля или
по ставкам, которые пользователь задал на эту загрузку: купля-продажа — % от
оборота без НКД, РЕПО — % годовых от суммы первой части за срок РЕПО, до копейки
по каждой сделке. Дальше — тот же импорт сделок, что у пакета JSON
(docs/api/trade-import): источник ``quik``, ключ — номер сделки.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any
from uuid import UUID

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from am_common.clock import MOSCOW
from booking.clients import RefdataClient
from booking.imports import Imported, ImportItem, Rejected, TradeImport
from booking.repository import Repository
from booking.roll import HUNDRED, KOPECK, InstrumentKind, Side, trade_amount
from booking.trades import RuleViolationError, TradeDesk

SOURCE = "quik"
# КСУ — клиринговые сертификаты участия: обеспечение РЕПО с ЦК без бумаги (решение 117).
KSU_ISIN = "RU000A0JW4Z1"
HEADER = "Дата и время заключения сделки"
YEAR = Decimal(365)
REPO_ROWS = 3  # сделка и две части
SIDES = {"Купля": Side.BUY, "Продажа": Side.SELL}
DEAL_COLUMNS = (
    "Номер сделки",
    "Код инструмента",
    "Краткое наименование инструмента",
    "Направление",
    "Кол-во",
    "Цена",
    "Объём",
    "НКД",
    "Дата расчётов",
)
REPO_COLUMNS = (
    "Номер сделки",
    "Номер заявки",
    "Код бумаги",
    "Краткое наименование инструмента",
    "Направление",
    "Объём",
    "Сумма РЕПО",
    "Ставка РЕПО",
    "Дата расчётов",
)
MOMENT = re.compile(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}")
PERIOD = re.compile(r"Отчётный период:\s*(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})")
CLIENT = "Код клиента:"


class QuikFormatError(Exception):
    """Файл — не отчёт QUIK по сделкам клиента или в нём нет нужных колонок."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# --- таблицы HTML --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Cell:
    text: str
    num: str | None = None  # x:num — число без разрядных пробелов, как его видит Excel
    header: bool = False


class _Tables(HTMLParser):
    """Строки всех таблиц подряд. QUIK не закрывает пустые ячейки и строки итогов:
    новая ячейка закрывает прежнюю, новая строка — прежнюю строку."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[Cell]] = []
        self._row: list[Cell] | None = None
        self._cell: list[str] | None = None
        self._num: str | None = None
        self._header = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._close_row()
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._close_cell()
            self._cell, self._header = [], tag == "th"
            self._num = dict(attrs).get("x:num")
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"}:
            self._close_cell()
        elif tag in {"tr", "table"}:
            self._close_row()

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def close(self) -> None:
        super().close()
        self._close_row()

    def _close_cell(self) -> None:
        if self._cell is not None and self._row is not None:
            text = " ".join("".join(self._cell).split())
            self._row.append(Cell(text, self._num, self._header))
        self._cell = None

    def _close_row(self) -> None:
        self._close_cell()
        if self._row is not None:
            self.rows.append(self._row)
        self._row = None


def _decode(content: bytes) -> str:
    if content.startswith(b"\xef\xbb\xbf"):
        return content[3:].decode("utf-8", errors="replace")
    found = re.search(rb"charset=['\"]?([\w-]+)", content[:4000], re.IGNORECASE)
    encoding = found[1].decode("ascii") if found else "windows-1251"
    try:
        return content.decode(encoding, errors="replace")
    except LookupError:
        return content.decode("windows-1251", errors="replace")


# --- разбор отчёта ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Deal:
    """Купля-продажа из отчёта: НКД — на всю сделку, как в отчёте."""

    line: int
    number: str
    trade_at: datetime
    code: str
    name: str
    side: Side
    quantity: int
    price: Decimal
    volume: Decimal  # чистая сумма: от неё — комиссия
    accrued: Decimal
    settle: date

    @property
    def turnover(self) -> Decimal:
        return self.volume + self.accrued


@dataclass(frozen=True, slots=True)
class RepoDeal:
    """РЕПО с ЦК из трёх строк отчёта: номер — первой строки, самой сделки."""

    line: int
    number: str
    trade_at: datetime
    code: str
    name: str
    side: Side  # по первой части: покупка — обратное РЕПО, продажа — прямое
    amount: Decimal
    rate: Decimal
    first_date: date
    second_date: date
    second_amount: Decimal

    @property
    def ksu(self) -> bool:
        return self.code.upper() == KSU_ISIN or self.name.upper().startswith("КСУ")

    @property
    def days(self) -> int:
        return (self.second_date - self.first_date).days


@dataclass(frozen=True, slots=True)
class Problem:
    """Строка, которую нельзя загрузить, — с причиной словами."""

    line: int
    number: str
    code: str
    message: str


@dataclass(slots=True)
class Report:
    client_code: str | None = None
    period: tuple[date, date] | None = None
    deals: list[Deal] = field(default_factory=list)
    repos: list[RepoDeal] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Line:
    index: int
    values: Mapping[str, Cell]

    def text(self, column: str) -> str:
        found = self.values.get(column)
        return "" if found is None else found.text

    def number(self, column: str) -> Decimal:
        found = self.values.get(column)
        raw = "" if found is None else (found.num or found.text)
        try:
            return Decimal(raw.replace("\xa0", "").replace(" ", "").replace(",", "."))
        except InvalidOperation as exc:
            raise ValueError(f"в колонке «{column}» не число: {raw!r}") from exc

    def day(self, column: str) -> date:
        return datetime.strptime(self.text(column), "%d.%m.%Y").date()

    def moment(self) -> datetime:
        return datetime.strptime(self.text(HEADER), "%d.%m.%Y %H:%M:%S").replace(tzinfo=MOSCOW)

    def side(self) -> Side:
        found = SIDES.get(self.text("Направление"))
        if found is None:
            raise ValueError(f"направление «{self.text('Направление')}» — не купля и не продажа")
        return found


def parse_report(content: bytes) -> Report:
    """Сделки и РЕПО отчёта; строки, которые не разобрать, — в ``problems``."""
    tables = _Tables()
    tables.feed(_decode(content))
    tables.close()
    report = Report()
    columns: list[str] | None = None
    repo_lines: list[_Line] = []
    index = 0
    for row in tables.rows:
        first = row[0].text if row else ""
        if row and row[0].header and first == HEADER:
            columns = [c.text for c in row]
            needed = REPO_COLUMNS if "Сумма РЕПО" in columns else DEAL_COLUMNS
            missing = [c for c in needed if c not in columns]
            if missing:
                raise QuikFormatError(f"В отчёте нет колонок: {', '.join(missing)}.")
            continue
        if first.startswith(CLIENT):
            report.client_code = first.removeprefix(CLIENT).strip() or None
        if found := PERIOD.search(" ".join(c.text for c in row)):
            report.period = (_date(found[1]), _date(found[2]))
        if columns is None or not MOMENT.fullmatch(first):
            continue
        index += 1
        line = _Line(index, dict(zip(columns, row, strict=False)))
        if "Сумма РЕПО" in columns:
            repo_lines.append(line)
            continue
        try:
            report.deals.append(_deal(line))
        except ValueError as exc:
            report.problems.append(
                Problem(index, line.text("Номер сделки"), "bad_row", f"Строку не разобрать: {exc}.")
            )
    if columns is None:
        raise QuikFormatError(
            "Это не отчёт QUIK по сделкам клиента: нет таблицы сделок. Выгрузите в QUIK "
            "«Отчёт по всем сделкам клиента» в формате Excel."
        )
    _repos(repo_lines, report)
    return report


def _date(text: str) -> date:
    return datetime.strptime(text, "%d.%m.%Y").date()


def _deal(line: _Line) -> Deal:
    quantity = line.number("Кол-во")
    if quantity <= 0 or quantity != quantity.to_integral_value():
        raise ValueError(f"количество {quantity} — не целое число бумаг")
    price = line.number("Цена")
    if price <= 0:
        raise ValueError(f"цена {price} не больше нуля")
    return Deal(
        line=line.index,
        number=line.text("Номер сделки"),
        trade_at=line.moment(),
        code=line.text("Код инструмента"),
        name=line.text("Краткое наименование инструмента"),
        side=line.side(),
        quantity=int(quantity),
        price=price,
        volume=line.number("Объём"),
        accrued=line.number("НКД"),
        settle=line.day("Дата расчётов"),
    )


def _repos(lines: Sequence[_Line], report: Report) -> None:
    """Три строки одной заявки — одно РЕПО: сделка и первая часть в одну сторону
    с одной датой расчётов, вторая часть — в обратную, позже."""
    orders: dict[tuple[str, str], list[_Line]] = defaultdict(list)
    for line in lines:
        orders[(line.text("Номер заявки"), line.text(HEADER))].append(line)
    for group in orders.values():
        group.sort(key=lambda item: (len(item.text("Номер сделки")), item.text("Номер сделки")))
        try:
            report.repos.append(_repo(group))
        except ValueError as exc:
            report.problems += [
                Problem(item.index, item.text("Номер сделки"), "bad_repo", f"РЕПО: {exc}.")
                for item in group
            ]


def _repo(group: Sequence[_Line]) -> RepoDeal:
    if len(group) != REPO_ROWS:
        raise ValueError(
            f"в отчёте {len(group)} строки заявки вместо трёх — сделки и двух частей; "
            "возьмите отчёт за период, где видна вся сделка"
        )
    main, *legs = group
    side = main.side()
    first = [leg for leg in legs if leg.side() is side]
    second = [leg for leg in legs if leg.side() is not side]
    if len(first) != 1 or len(second) != 1:
        raise ValueError("не найти первую и вторую части")
    first_date, second_date = first[0].day("Дата расчётов"), second[0].day("Дата расчётов")
    if second_date <= first_date:
        raise ValueError("вторая часть не позже первой")
    return RepoDeal(
        line=main.index,
        number=main.text("Номер сделки"),
        trade_at=main.moment(),
        code=main.text("Код бумаги"),
        name=main.text("Краткое наименование инструмента"),
        side=side,
        amount=main.number("Сумма РЕПО"),
        rate=main.number("Ставка РЕПО"),
        first_date=first_date,
        second_date=second_date,
        second_amount=second[0].number("Объём"),
    )


# --- комиссии ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rates:
    """Ставки комиссии: купля-продажа — % от оборота без НКД, РЕПО — % годовых."""

    outright: Decimal | None = None
    repo: Decimal | None = None


def tariff_on(tariffs: Sequence[Row[Any]], day: date) -> Row[Any] | None:
    """Тариф на день: последняя строка с датой начала не позже дня."""
    found = [t for t in tariffs if t.valid_from <= day]
    return found[-1] if found else None


def _kopecks(value: Decimal) -> Decimal:
    return value.quantize(KOPECK, rounding=ROUND_HALF_UP)


def deal_fee(deal: Deal, rate: Decimal) -> Decimal:
    """Купля-продажа: объём сделки без НКД × ставка, до копейки."""
    return _kopecks(deal.volume * rate / HUNDRED)


def repo_fee(repo: RepoDeal, rate: Decimal) -> Decimal:
    """РЕПО: сумма первой части × ставка, % годовых × срок в днях / 365, до копейки."""
    return _kopecks(repo.amount * rate / HUNDRED * repo.days / YEAR)


# --- загрузка ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QuikLine:
    """Строка ответа: сделка отчёта, комиссия и что с ней сделала загрузка."""

    line: int
    external_id: str
    kind: str  # outright | repo
    code: str = ""
    name: str = ""
    side: Side | None = None  # None — строку не разобрать
    trade_at: datetime | None = None
    quantity: int | None = None
    price: Decimal | None = None
    amount: Decimal | None = None  # купля-продажа — сумма с НКД, РЕПО — сумма первой части
    fee: Decimal | None = None
    fee_rate: Decimal | None = None
    result: str | None = None  # created | amended | unchanged; None — отклонена
    trade_id: UUID | None = None
    error: str | None = None
    message: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QuikResult:
    client_code: str | None
    period: tuple[date, date] | None
    tariff: Row[Any] | None  # тариф счёта на последний день сделок отчёта
    rates: Rates  # ставки пользователя на эту загрузку
    applied: bool
    lines: list[QuikLine]
    # Бумаги отчёта, которых нет в справочнике: код и название. Пока они есть,
    # отчёт не загружается — сначала бумагу заводят в справочнике.
    new_instruments: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Target:
    """Куда и с какими ставками грузить: портфель, тарифы его счёта, ставки пользователя."""

    portfolio: str
    tariffs: Sequence[Row[Any]]
    rates: Rates
    comment: str


class QuikImport:
    def __init__(self, session: AsyncSession, desk: TradeDesk, refdata: RefdataClient) -> None:
        self._session = session
        self._desk = desk
        self._refdata = refdata

    async def run(
        self, content: bytes, portfolio_id: UUID, rates: Rates, *, apply: bool
    ) -> QuikResult:
        """Проверка (``apply`` — нет) или загрузка отчёта в портфель."""
        report = parse_report(content)
        visible = await self._desk.visible()
        portfolio = visible.get(portfolio_id)
        if portfolio is None:
            raise RuleViolationError(
                "portfolio_unavailable", "Портфеля нет или к нему нет доступа.", 404
            )
        if portfolio.account_id is None:
            raise RuleViolationError(
                "no_account",
                f"У портфеля {portfolio.code} нет счёта — выберите портфель, в который брокер "
                "заводит сделки.",
            )
        async with self._session.begin():
            tariffs = await Repository(self._session).tariffs(portfolio.account_id)
        kinds = await self._refdata.kinds()
        new = sorted({(d.code, d.name) for d in report.deals if d.code.upper() not in kinds})
        if apply and new:
            codes = ", ".join(f"{code} ({name})" for code, name in new)
            raise RuleViolationError(
                "new_instruments",
                f"В отчёте бумаги, которых нет в справочнике: {codes}. Заведите их в разделе "
                "«Справочник» и загрузите отчёт снова.",
            )
        comment = "Загружена из отчёта QUIK" + (
            f", код клиента {report.client_code}." if report.client_code else "."
        )
        target = _Target(portfolio.code, tariffs, rates, comment)
        prepared = [self._deal(deal, kinds, target) for deal in report.deals] + [
            self._repo(repo, target) for repo in report.repos
        ]
        lines = [line for line, item in prepared if item is None]
        items = [(line, item) for line, item in prepared if item is not None]
        imported = await TradeImport(self._session, self._desk, self._refdata).run(
            SOURCE, [item for _, item in items], dry_run=not apply
        )
        done: dict[int, Imported | Rejected] = {t.index: t for t in imported.trades} | {
            r.index: r for r in imported.rejected
        }
        for index, (line, _) in enumerate(items):
            lines.append(_outcome(line, done[index]))
        lines += [
            QuikLine(
                line=p.line,
                external_id=p.number,
                kind="repo" if p.code == "bad_repo" else "outright",
                error=p.code,
                message=p.message,
            )
            for p in report.problems
        ]
        days = [d.trade_at.date() for d in report.deals] + [r.trade_at.date() for r in report.repos]
        tariff = tariff_on(tariffs, max(days)) if days else None
        lines.sort(key=lambda item: item.line)
        return QuikResult(report.client_code, report.period, tariff, rates, apply, lines, new)

    def _deal(
        self, deal: Deal, kinds: Mapping[str, str], target: _Target
    ) -> tuple[QuikLine, ImportItem | None]:
        line = QuikLine(
            line=deal.line,
            external_id=deal.number,
            kind="outright",
            code=deal.code,
            name=deal.name,
            side=deal.side,
            trade_at=deal.trade_at,
            quantity=deal.quantity,
            price=deal.price,
            amount=deal.turnover,
        )
        rate = _rate(target.rates.outright, target.tariffs, deal.trade_at.date(), "outright_rate")
        if rate is None:
            return _no_tariff(line, deal.trade_at.date()), None
        fee = deal_fee(deal, rate)
        line = _with_fee(line, fee, rate)
        kind = kinds.get(deal.code.upper())
        face = accrued = None
        if kind == InstrumentKind.BOND:
            # Номинал и НКД на бумагу — из отчёта: так сумма сделки сойдётся с брокером.
            face = _kopecks(deal.volume * HUNDRED / (deal.quantity * deal.price))
            accrued = _kopecks(deal.accrued / deal.quantity)
        if kind is not None:
            expected = trade_amount(InstrumentKind(kind), deal.quantity, deal.price, face, accrued)
            if expected != deal.turnover:
                return _refuse(
                    line,
                    "amount_mismatch",
                    f"Сумма по расчёту системы {expected} ₽ не сходится с отчётом "
                    f"{deal.turnover} ₽ (объём и НКД) — проверьте сделку.",
                ), None
        item = ImportItem(
            external_id=deal.number,
            portfolio=target.portfolio,
            code=deal.code,
            side=deal.side,
            trade_at=deal.trade_at,
            settle_date=deal.settle,
            quantity=deal.quantity,
            price=deal.price,
            accrued=accrued,
            face_value=face,
            fee=fee,
            comment=target.comment,
        )
        return line, item

    def _repo(self, repo: RepoDeal, target: _Target) -> tuple[QuikLine, ImportItem | None]:
        line = QuikLine(
            line=repo.line,
            external_id=repo.number,
            kind="repo",
            code=repo.code,
            name=repo.name,
            side=repo.side,
            trade_at=repo.trade_at,
            quantity=None,
            price=None,
            amount=repo.amount,
        )
        if not repo.ksu:
            return _refuse(
                line,
                "bond_repo",
                "РЕПО под облигации из отчёта QUIK пока не загружается — введите его вручную.",
            ), None
        rate = _rate(target.rates.repo, target.tariffs, repo.trade_at.date(), "repo_rate")
        if rate is None:
            return _no_tariff(line, repo.trade_at.date()), None
        fee = repo_fee(repo, rate)
        item = ImportItem(
            external_id=repo.number,
            portfolio=target.portfolio,
            code="KSU",
            side=repo.side,
            trade_at=repo.trade_at,
            settle_date=repo.first_date,
            fee=fee,
            comment=target.comment,
            kind="repo",
            amount=repo.amount,
            repo_rate=repo.rate,
            second_date=repo.second_date,
            second_amount=repo.second_amount,
        )
        return _with_fee(line, fee, rate), item


def _rate(
    chosen: Decimal | None, tariffs: Sequence[Row[Any]], day: date, column: str
) -> Decimal | None:
    """Ставка пользователя на загрузку; нет её — тариф счёта на день сделки."""
    if chosen is not None:
        return chosen
    tariff = tariff_on(tariffs, day)
    return None if tariff is None else Decimal(getattr(tariff, column))


def _with_fee(line: QuikLine, fee: Decimal, rate: Decimal) -> QuikLine:
    return QuikLine(**{**_fields(line), "fee": fee, "fee_rate": rate})


def _refuse(line: QuikLine, code: str, message: str) -> QuikLine:
    return QuikLine(**{**_fields(line), "error": code, "message": message})


def _no_tariff(line: QuikLine, day: date) -> QuikLine:
    return _refuse(
        line,
        "no_tariff",
        f"Нет тарифа брокера на {day:%d.%m.%Y} — задайте ставку на загрузку "
        "или попросите администратора завести тариф счёта.",
    )


def _outcome(line: QuikLine, done: Imported | Rejected) -> QuikLine:
    if isinstance(done, Rejected):
        return _refuse(line, done.error, done.message)
    return QuikLine(
        **{
            **_fields(line),
            "result": done.result.value,
            "trade_id": done.trade_id,
            "warnings": tuple(done.warnings),
        }
    )


def _fields(line: QuikLine) -> dict[str, Any]:
    return {name: getattr(line, name) for name in line.__dataclass_fields__}
