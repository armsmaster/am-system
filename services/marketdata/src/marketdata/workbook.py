"""Детализация расчёта по облигации в Excel (решение 158, design/04).

Книга строится из того же расчёта, что отдаёт API. Входные данные — значениями
(жёлтые ячейки), всё остальное — формулами Excel с именованными ячейками
(Цена_грязная, RUONIA_оценка, Фондирование…). Доходность проверяется ещё и
функцией ЧИСТВНДОХ (XIRR) — у неё та же конвенция ACT/365. Рядом с итоговыми
величинами — значение сервиса и расхождение. У формул записан и результат,
посчитанный сервисом: его видно и там, где книгу при открытии не пересчитывают.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import cast

import xlsxwriter
from xlsxwriter.format import Format
from xlsxwriter.utility import xl_rowcol_to_cell
from xlsxwriter.workbook import Workbook
from xlsxwriter.worksheet import Worksheet

from am_common.clock import MOSCOW
from am_quant.bond import CashFlow, Coupon, FlowKind, Horizon, HorizonKind
from am_quant.spread import Breakdown
from am_quant.yields import Risk
from marketdata.analytics import (
    Analytics,
    BondType,
    FloaterSpec,
    HorizonYield,
    Measure,
    PriceBasis,
    SpreadView,
)
from marketdata.funding import HALF_LIFE_DAYS, Funding
from marketdata.prices import PRICE_LABELS
from marketdata.rates import RATE_LABELS

TYPE_LABELS = {
    BondType.FIXED: "облигация с фиксированным купоном: доходность",
    BondType.FLOATER: "флоатер: спред над фондированием",
    BondType.LINKER: "линкер (номинал растёт с RUONIA): спред над фондированием",
}
HORIZON_LABELS = {HorizonKind.OFFER: "к оферте", HorizonKind.MATURITY: "к погашению"}
FLOW_LABELS = {
    FlowKind.COUPON: "купон",
    FlowKind.PRINCIPAL: "погашение номинала",
    FlowKind.OFFER: "выкуп по оферте",
}
BASIS_LABELS = {
    PriceBasis.VALUATION: "цена для оценки",
    PriceBasis.PRICE: "«что если»: введённая чистая цена",
    PriceBasis.YIELD: "«что если»: цена по введённой доходности",
    PriceBasis.SPREAD: "«что если»: цена по введённому спреду",
}
BASIS_SHEET = "Базис по дням"
BASIS_FIRST = 4  # первая строка ряда базиса на его листе
TINY = 1e-9  # меньше — ноль: остаток потока линкера сверх номинала
# Столбцы таблицы потоков.
DATE, KIND, AMOUNT, HOW, DAYS, T, DISCOUNT, PV, T_PV, T_T1_PV, T2_PV = range(11)
START, PERIOD, NOMINAL, RATE, XIRR_FLOW, PAR_FLOW = range(11, 17)
COLUMNS = (
    "Дата",
    "Вид",
    "Сумма, ₽",
    "Как получена",
    "Дней от S",
    "t = дни/365",
    "Дисконт (1+y)^−t",
    "PV = сумма × дисконт",
    "t·PV",
    "t(t+1)·PV",
    "t²·PV",
    "Начало периода",
    "Дней в периоде d",
    "Номинал N, ₽",
    "Ставка купона k",
    "Поток для ЧИСТВНДОХ",
    "Поток при цене 100",
)
# Событие графика: дата, что, начало периода, сумма, ставка купона, цена оферты.
Event = tuple[date, str, date | None, str | float | None, Decimal | None, float | None]


def _f(value: Decimal | float) -> float:
    return float(value)


def _serial(day: date) -> float:
    """Дата числом Excel — результат формулы, которая возвращает дату."""
    return float((day - date(1899, 12, 30)).days)


class Styles:
    def __init__(self, book: Workbook) -> None:
        add = book.add_format
        self.title = add({"bold": True, "font_size": 13})
        self.head = add({"bold": True, "bg_color": "#EEF1F5", "border": 1, "text_wrap": True})
        self.label = add({"text_wrap": True, "valign": "top"})
        self.note = add({"font_color": "#5D6672", "valign": "top"})
        self.text = add({"valign": "top"})
        self.date = add({"num_format": "dd.mm.yyyy"})
        self.money = add({"num_format": "#,##0.00"})
        self.money6 = add({"num_format": "#,##0.000000"})
        self.share = add({"num_format": "0.0000%"})  # доля — как проценты
        self.rate = add({"num_format": "0.0000"})  # % годовых, п.п. или бп
        self.number = add({"num_format": "0.000000"})
        self.days = add({"num_format": "0"})
        yellow = {"bg_color": "#FFF6DC"}  # входное значение
        self.input = add(yellow)
        self.input_money = add(yellow | {"num_format": "#,##0.00"})
        self.input_rate = add(yellow | {"num_format": "0.0000"})
        self.input_share = add(yellow | {"num_format": "0.0000%"})
        self.input_date = add(yellow | {"num_format": "dd.mm.yyyy"})


class Sheet:
    """Лист с курсором строк: подпись | значение или формула | сервис | расхождение | пояснение."""

    def __init__(self, book: Workbook, name: str, styles: Styles) -> None:
        self.book = book
        self.name = name
        self.st = styles
        self.ws: Worksheet = book.add_worksheet(name)
        self.row = 0
        self.ws.set_column(0, 0, 46)
        self.ws.set_column(1, 3, 17)

    def ref(self, row: int, col: int) -> str:
        """Абсолютная ссылка на ячейку этого листа — для других листов."""
        return f"'{self.name}'!{xl_rowcol_to_cell(row, col, row_abs=True, col_abs=True)}"

    def column(self, col: int, first: int, last: int) -> str:
        """Диапазон столбца этого листа — для других листов."""
        return f"'{self.name}'!{cell(first, col)}:{cell(last, col)}"

    def title(self, text: str) -> None:
        self.ws.write_string(self.row, 0, text, self.st.title)
        self.row += 2

    def heading(self, *labels: str) -> None:
        for col, label in enumerate(labels):
            self.ws.write_string(self.row, col, label, self.st.head)
        self.row += 1

    def text(self, label: str, value: str = "", note: str = "") -> None:
        self.ws.write_string(self.row, 0, label, self.st.label)
        if value:
            self.ws.write_string(self.row, 1, value, self.st.text)
        self._finish(self.row, None, note)

    def value(
        self,
        label: str,
        value: Decimal | date,
        fmt: Format,
        *,
        name: str | None = None,
        note: str = "",
    ) -> int:
        """Входное значение."""
        row = self.row
        self.ws.write_string(row, 0, label, self.st.label)
        if isinstance(value, date):
            self.ws.write_datetime(row, 1, value, fmt)
        else:
            self.ws.write_number(row, 1, _f(value), fmt)
        self._finish(row, name, note)
        return row

    def formula(
        self,
        label: str,
        formula: str,
        cached: float,
        fmt: Format,
        *,
        name: str | None = None,
        service: float | None = None,
        note: str = "",
    ) -> int:
        """Величина формулой; значение сервиса — рядом, с расхождением."""
        row = self.row
        self.ws.write_string(row, 0, label, self.st.label)
        self.ws.write_formula(row, 1, formula, fmt, cached)
        if service is not None:
            self.ws.write_number(row, 2, service, fmt)
            difference = f"={cell(row, 1)}-{cell(row, 2)}"
            self.ws.write_formula(row, 3, difference, fmt, cached - service)
        self._finish(row, name, note)
        return row

    def _finish(self, row: int, name: str | None, note: str) -> None:
        if note:
            self.ws.write_string(row, 4, note, self.st.note)
        if name:
            self.book.define_name(name, f"={self.ref(row, 1)}")
        self.row += 1


def cell(row: int, col: int) -> str:
    return str(xl_rowcol_to_cell(row, col))


def workbook(found: Analytics) -> bytes:
    """Книга Excel с детализацией расчёта ``found``."""
    output = BytesIO()
    book = xlsxwriter.Workbook(output, {"in_memory": True})
    styles = Styles(book)
    summary = Sheet(book, "Итог", styles)  # первым листом, заполняется по ходу
    _conditions(Sheet(book, "Условия", styles), found)
    _price(summary, found)
    if found.spread is None:
        pages = [_fixed_page(book, styles, found, item) for item in found.yields]
        _fixed_summary(summary, pages)
    else:
        view = found.spread
        _funding(Sheet(book, "Фондирование", styles), view.funding)
        page = Sheet(book, "Потоки", styles)
        page.title(_flows_title(view.result.horizon))
        page.heading("Величина", "Значение", "Сервис", "Расхождение", "Пояснение")
        page.value(
            "Спред s над фондированием — эффективный годовой (сервис)",
            view.result.spread,
            styles.input_share,
            name="Спред",
            note="сервис нашёл его методом Ньютона: Σ CF·(1+Eф+s)^−t = P",
        )
        rate = view.result.funding + view.result.spread
        page.formula(
            "Доходность бумаги y = Eф + s",
            "=Фондирование+Спред",
            _f(rate),
            styles.share,
            name="Доходность_бумаги",
        )
        flows = _flows(
            page,
            found,
            flows=view.result.flows,
            horizon=view.result.horizon,
            rate=rate,
            name="Доходность_бумаги",
        )
        _breakdown(Sheet(book, "Разложение спреда", styles), found, view, flows)
        _basis_table(Sheet(book, BASIS_SHEET, styles), view.funding)
        _spread_summary(summary, view, flows)
    _method(Sheet(book, "Методика", styles), found)
    summary.ws.activate()
    book.close()
    return output.getvalue()


# --- условия -------------------------------------------------------------------------


def _conditions(sheet: Sheet, found: Analytics) -> None:
    st = sheet.st
    sheet.title(f"Условия и цена: {found.instrument.short_name} ({found.instrument.secid})")
    sheet.text("Жёлтым — входные данные: справочник и рыночные данные на момент расчёта.")
    sheet.row += 1
    sheet.heading("Величина", "Значение", "Сервис", "Расхождение", "Пояснение")
    sheet.value("Дата расчётов S (T+1)", found.settlement, st.input_date, name="Дата_расчётов")
    sheet.value(
        "Чистая цена, % от непогашенного номинала",
        found.clean,
        st.input_rate,
        name="Цена_чистая",
        note=BASIS_LABELS[found.basis],
    )
    sheet.value("Непогашенный номинал на S, ₽", found.face_value, st.input_money, name="Номинал")
    sheet.value("НКД на S, ₽", found.accrued, st.input_money, name="НКД")
    sheet.formula(
        "Грязная цена P, ₽",
        "=Цена_чистая*Номинал/100+НКД",
        _f(found.dirty),
        st.money6,
        name="Цена_грязная",
        service=_f(found.dirty),
        note="P = чистая цена × номинал / 100 + НКД",
    )
    view, spec = found.spread, found.terms.floater
    if view is not None and spec is not None:
        _floater(sheet, view, spec)
    sheet.row += 1
    _schedule(sheet, found)


def _schedule(sheet: Sheet, found: Analytics) -> None:
    """График выпуска: купоны, погашения номинала и оферты по датам."""
    st, bond = sheet.st, found.terms.bond
    sheet.heading("Дата", "Событие", "Начало периода", "Сумма, ₽", "Ставка купона", "Оферта, %")
    events: list[Event] = []
    events += [
        (c.end, "купон", c.start, "не объявлен" if c.amount is None else _f(c.amount), c.rate, None)
        for c in bond.coupons
    ]
    events += [
        (p.date, "погашение номинала", None, _f(p.amount), None, None) for p in bond.principal
    ]
    events += [
        (o.date, "оферта" if o.window_end is None else f"оферта, заявки до {o.window_end:%d.%m.%Y}",
         None, None, None, _f(o.price))
        for o in bond.offers
    ]  # fmt: skip
    formats = (st.input_date, st.text, st.input_date, st.input_money, st.input_share, st.input_rate)
    for event in sorted(events, key=lambda e: e[0]):
        for col, (value, fmt) in enumerate(zip(event, formats, strict=True)):
            _put(sheet, col, value, fmt)
        sheet.row += 1
    sheet.ws.set_column(4, 5, 16)


def _put(sheet: Sheet, col: int, value: object, fmt: Format) -> None:
    if value is None:
        return
    if isinstance(value, date):
        sheet.ws.write_datetime(sheet.row, col, value, fmt)
    elif isinstance(value, str):
        sheet.ws.write_string(sheet.row, col, value, sheet.st.text)
    else:
        sheet.ws.write_number(sheet.row, col, _f(cast(float, value)), fmt)


def _floater(sheet: Sheet, view: SpreadView, spec: FloaterSpec) -> None:
    """Параметры флоатера и ставка индекса."""
    st = sheet.st
    sheet.text("Плавающая ставка", RATE_LABELS[spec.rate], "по справочнику")
    sheet.value("Маржа m, доля годовых", spec.margin, st.input_share, name="Маржа")
    sheet.text(
        "Капитализация внутри периода",
        "да" if spec.capitalized else "нет",
        "купон N·((1+I/365)^d − 1 + m·d/365)" if spec.capitalized else "купон N·(I + m)·d/365",
    )
    if view.index is None:
        sheet.formula(
            "Ставка индекса I, % годовых",
            "=RUONIA_оценка",
            _f(view.funding.value),
            st.rate,
            name="Индекс",
            note="оценка RUONIA: ключевая ставка + базис (лист «Фондирование»)",
        )
    else:
        point = view.index
        sheet.value(
            "Ставка индекса I, % годовых",
            point.value,
            st.input_rate,
            name="Индекс",
            note=f"{RATE_LABELS[point.kind]} на {point.as_of:%d.%m.%Y}, источник {point.source}",
        )


# --- фондирование ----------------------------------------------------------------------


def _funding(sheet: Sheet, funding: Funding) -> None:
    st = sheet.st
    last = BASIS_FIRST + len(funding.days) - 1
    rate = _f(funding.value) / 100
    sheet.title("Фондирование: RUONIA = ключевая ставка + сглаженный базис (решение 157)")
    sheet.heading("Величина", "Значение", "Сервис", "Расхождение", "Пояснение")
    sheet.value(
        "Ключевая ставка на день цены, % годовых",
        funding.key_rate.value,
        st.input_rate,
        name="КС",
        note=f"на {funding.key_rate.as_of:%d.%m.%Y}, источник {funding.key_rate.source}",
    )
    sheet.value("Период полураспада базиса H, дней", HALF_LIFE_DAYS, st.input, name="Полураспад")
    sheet.formula(
        "α = 1 − 2^(−1/H)",
        "=1-2^(-1/Полураспад)",
        1 - 2 ** (-1 / _f(HALF_LIFE_DAYS)),
        st.number,
        name="Альфа",
        note="вес нового дня в сглаживании",
    )
    sheet.formula(
        "Базис Б — DEMA на последний день, п.п.",
        f"='{BASIS_SHEET}'!$G${last + 1}",
        _f(funding.basis),
        st.rate,
        name="Базис",
        service=_f(funding.basis),
        note=f"ряд с {funding.since:%d.%m.%Y} по {funding.through:%d.%m.%Y}, лист «{BASIS_SHEET}»",
    )
    sheet.formula(
        "RUONIA — оценка R̂ = КС + Б, % годовых",
        "=КС+Базис",
        _f(funding.value),
        st.rate,
        name="RUONIA_оценка",
        service=_f(funding.value),
    )
    sheet.formula(
        "Фондирование Eф = (1 + R̂/365)^365 − 1",
        "=(1+RUONIA_оценка/100/365)^365-1",
        (1 + rate / 365) ** 365 - 1,
        st.share,
        name="Фондирование",
        note="эффективная годовая ставка",
    )
    sheet.formula(
        "Фондирование непрерывное = 365·ln(1 + R̂/365)",
        "=365*LN(1+RUONIA_оценка/100/365)",
        365 * math.log(1 + rate / 365),
        st.share,
        name="Фондирование_непр",
        note="равно ln(1 + Eф)",
    )
    sheet.value(
        "Последняя опубликованная RUONIA, % годовых",
        funding.ruonia.value,
        st.input_rate,
        note=f"за {funding.ruonia.as_of:%d.%m.%Y}, источник {funding.ruonia.source}, для сравнения",
    )


def _basis_table(table: Sheet, funding: Funding) -> None:
    st, ws = table.st, table.ws
    table.title("Базис RUONIA − КС по календарным дням (решение 157)")
    table.text(
        "b = RUONIA − КС в дни публикации RUONIA, между ними держится; "
        "E1 = α·b + (1−α)·E1₋₁; E2 = α·E1 + (1−α)·E2₋₁; Б = 2·E1 − E2. Первые E1 и E2 — b."
    )
    table.heading("Дата", "RUONIA, %", "КС, %", "Базис b, п.п.", "E1", "E2", "Б = 2·E1 − E2")
    ws.set_column(0, 0, 12)
    ws.set_column(1, 6, 14)
    ws.freeze_panes(BASIS_FIRST, 1)
    for index, day in enumerate(funding.days):
        row = BASIS_FIRST + index
        b, c, d, e, f = (cell(row, col) for col in range(1, 6))
        ws.write_datetime(row, 0, day.day, st.date)
        if day.ruonia is not None:
            ws.write_number(row, 1, _f(day.ruonia), st.input_rate)
        ws.write_number(row, 2, _f(day.key_rate), st.input_rate)
        if index == 0:
            ws.write_formula(row, 3, f"={b}-{c}", st.rate, _f(day.basis))
            ws.write_formula(row, 4, f"={d}", st.number, _f(day.ema))
            ws.write_formula(row, 5, f"={d}", st.number, _f(day.ema_of_ema))
        else:
            d0, e0, f0 = (cell(row - 1, col) for col in range(3, 6))
            ws.write_formula(row, 3, f"=IF(ISNUMBER({b}),{b}-{c},{d0})", st.rate, _f(day.basis))
            ws.write_formula(row, 4, f"=Альфа*{d}+(1-Альфа)*{e0}", st.number, _f(day.ema))
            ws.write_formula(row, 5, f"=Альфа*{e}+(1-Альфа)*{f0}", st.number, _f(day.ema_of_ema))
        ws.write_formula(row, 6, f"=2*{e}-{f}", st.number, _f(day.dema))


# --- потоки ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Flows:
    """Лист потоков и где на нём то, на что ссылаются другие листы."""

    sheet: Sheet
    xirr: int  # строка доходности по ЧИСТВНДОХ
    first: int  # первая строка таблицы: покупка
    last: int  # последняя строка потоков
    metrics: dict[str, int]  # метрика → строка


@dataclass(frozen=True, slots=True)
class _Sums:
    """Суммы по потокам, посчитанные здесь же, — результаты формул в книге."""

    times: list[float]
    pvs: list[float]
    total: float  # Σ PV
    weighted: float  # Σ t·PV
    plus: float  # Σ t(t+1)·PV
    squared: float  # Σ t²·PV

    @classmethod
    def of(cls, flows: Sequence[CashFlow], settle: date, rate: float) -> _Sums:
        times = [(f.date - settle).days / 365 for f in flows]
        pvs = [_f(f.amount) * (1 + rate) ** -t for f, t in zip(flows, times, strict=True)]
        pairs = list(zip(times, pvs, strict=True))
        return cls(
            times=times,
            pvs=pvs,
            total=sum(pvs),
            weighted=sum(t * pv for t, pv in pairs),
            plus=sum(t * (t + 1) * pv for t, pv in pairs),
            squared=sum(t * t * pv for t, pv in pairs),
        )


def _fixed_page(
    book: Workbook, styles: Styles, found: Analytics, item: HorizonYield
) -> tuple[HorizonYield, _Flows | None]:
    if item.result is None:
        return item, None
    kind = item.horizon.kind
    sheet = Sheet(book, f"Потоки {HORIZON_LABELS[kind]}", styles)
    sheet.title(_flows_title(item.horizon))
    sheet.heading("Величина", "Значение", "Сервис", "Расхождение", "Пояснение")
    name = "Доходность_оферта" if kind is HorizonKind.OFFER else "Доходность_погашение"
    sheet.value(
        "Доходность y — эффективная годовая (сервис)",
        item.result.yield_rate,
        styles.input_share,
        name=name,
        note="сервис нашёл её методом Ньютона: Σ CF·(1+y)^−t = P",
    )
    result = item.result
    return item, _flows(
        sheet, found, flows=result.flows, horizon=item.horizon, rate=result.yield_rate, name=name
    )


def _flows_title(horizon: Horizon) -> str:
    kind = HORIZON_LABELS[horizon.kind]
    return f"Потоки {kind} {horizon.date:%d.%m.%Y}: дисконтирование и метрики"


def _flows(
    sheet: Sheet,
    found: Analytics,
    *,
    flows: Sequence[CashFlow],
    horizon: Horizon,
    rate: Decimal,
    name: str,
) -> _Flows:
    """Метрики в обоих измерениях и таблица потоков с дисконтом по доходности ``name``."""
    st = sheet.st
    y = _f(rate)
    head = sheet.row + 12  # шапка таблицы — под блоком метрик
    first, last = head + 1, head + len(flows) + 1
    sums_row = last + 1
    sums = _Sums.of(flows, found.settlement, y)
    annual, smooth = _risks(found, horizon)
    macaulay = sums.weighted / sums.total
    pv = cell(sums_row, PV)

    xirr = sheet.formula(
        "Доходность по ЧИСТВНДОХ (XIRR) — проверка",
        f"=XIRR({cell(first, XIRR_FLOW)}:{cell(last, XIRR_FLOW)},"
        f"{cell(first, DATE)}:{cell(last, DATE)})",
        y,
        st.share,
        name=f"{name}_XIRR" if found.spread is None else "Доходность_XIRR",
        service=y,
        note="функция Excel по столбцу «Поток для ЧИСТВНДОХ», та же конвенция ACT/365",
    )
    sheet.formula(
        "Доходность непрерывная y_c = ln(1 + y)",
        f"=LN(1+{name})",
        math.log(1 + y),
        st.share,
        service=math.log(1 + y),
    )
    sheet.formula(
        "Σ PV — сумма приведённых потоков, ₽",
        f"={pv}",
        sums.total,
        st.money6,
        service=_f(found.dirty),
        note="равна грязной цене — значит, доходность найдена верно",
    )
    sheet.row += 1
    macaulay_row = sheet.formula(
        "Дюрация Маколея D, лет",
        f"={cell(sums_row, T_PV)}/{pv}",
        macaulay,
        st.number,
        service=_f(annual.macaulay),
        note="Σ t·PV / Σ PV — от измерения не зависит",
    )
    d = cell(macaulay_row, 1)
    metrics = {
        "modified": sheet.formula(
            "Модифицированная дюрация, годовые = D/(1+y)",
            f"={d}/(1+{name})",
            macaulay / (1 + y),
            st.number,
            service=_f(annual.modified),
        ),
        "dv01": sheet.formula(
            "DV01, годовые, ₽ за 1 бп = D/(1+y)·P/10 000",
            f"={d}/(1+{name})*{pv}/10000",
            macaulay / (1 + y) * sums.total / 10_000,
            st.number,
            service=_f(annual.dv01),
        ),
        "convexity": sheet.formula(
            "Выпуклость, годовые = Σ t(t+1)·PV / (P·(1+y)²)",
            f"={cell(sums_row, T_T1_PV)}/({pv}*(1+{name})^2)",
            sums.plus / (sums.total * (1 + y) ** 2),
            st.number,
            service=_f(annual.convexity),
        ),
        "modified_c": sheet.formula(
            "Модифицированная дюрация, непрерывные = D",
            f"={d}",
            macaulay,
            st.number,
            service=_f(smooth.modified),
        ),
        "dv01_c": sheet.formula(
            "DV01, непрерывные, ₽ за 1 бп = D·P/10 000",
            f"={d}*{pv}/10000",
            macaulay * sums.total / 10_000,
            st.number,
            service=_f(smooth.dv01),
        ),
        "convexity_c": sheet.formula(
            "Выпуклость, непрерывные = Σ t²·PV / P",
            f"={cell(sums_row, T2_PV)}/{pv}",
            sums.squared / sums.total,
            st.number,
            service=_f(smooth.convexity),
        ),
    }
    sheet.row = head
    _flow_table(sheet, found, flows=flows, horizon=horizon, name=name, y=y, sums=sums)
    return _Flows(sheet, xirr, first, last, metrics)


def _risks(found: Analytics, horizon: Horizon) -> tuple[Risk, Risk]:
    """Метрики сервиса к горизонту: годовые и непрерывные."""
    if found.spread is not None:
        result = found.spread.result
        return result.risk, result.continuous.risk
    item = next(i.result for i in found.yields if i.horizon == horizon and i.result is not None)
    return item.risk, item.continuous_risk


def _flow_table(
    sheet: Sheet,
    found: Analytics,
    *,
    flows: Sequence[CashFlow],
    horizon: Horizon,
    name: str,
    y: float,
    sums: _Sums,
) -> None:
    st, ws = sheet.st, sheet.ws
    spread = found.spread is not None
    sheet.heading(*(COLUMNS if spread else COLUMNS[:-1]))
    ws.set_column(DAYS, PAR_FLOW, 15)
    ws.set_column(HOW, HOW, 44)
    first = sheet.row
    # Первая строка — покупка по грязной цене: с неё начинает ЧИСТВНДОХ.
    ws.write_formula(first, DATE, "=Дата_расчётов", st.date, _serial(found.settlement))
    ws.write_string(first, KIND, "покупка по грязной цене", st.text)
    ws.write_formula(first, XIRR_FLOW, "=-Цена_грязная", st.money, -_f(found.dirty))
    if spread:
        par = -_f(found.face_value + found.accrued)
        ws.write_formula(first, PAR_FLOW, "=-(Номинал+НКД)", st.money, par)
    settle = found.settlement
    coupons = {c.end: c for c in found.terms.bond.coupons if settle < c.end <= horizon.date}
    rows = zip(flows, sums.times, sums.pvs, strict=True)
    for row, (flow, t, value) in enumerate(rows, start=first + 1):
        coupon = coupons[flow.date] if flow.kind is FlowKind.COUPON else None
        a, c, e, f, g, h = (cell(row, col) for col in (DATE, AMOUNT, DAYS, T, DISCOUNT, PV))
        amount = _f(flow.amount)
        ws.write_datetime(row, DATE, flow.date, st.date)
        ws.write_string(row, KIND, FLOW_LABELS[flow.kind], st.text)
        formula, how = _amount(sheet, found, horizon, flow, coupon, row=row)
        if formula is None:
            ws.write_number(row, AMOUNT, amount, st.input_money)
        else:
            ws.write_formula(row, AMOUNT, formula, st.money6, amount)
        ws.write_string(row, HOW, how, st.note)
        ws.write_formula(row, DAYS, f"={a}-Дата_расчётов", st.days, (flow.date - settle).days)
        ws.write_formula(row, T, f"={e}/365", st.number, t)
        ws.write_formula(row, DISCOUNT, f"=1/(1+{name})^{f}", st.number, (1 + y) ** -t)
        ws.write_formula(row, PV, f"={c}*{g}", st.money6, value)
        ws.write_formula(row, T_PV, f"={f}*{h}", st.money6, t * value)
        ws.write_formula(row, T_T1_PV, f"={f}*({f}+1)*{h}", st.money6, t * (t + 1) * value)
        ws.write_formula(row, T2_PV, f"={f}^2*{h}", st.money6, t * t * value)
        ws.write_formula(row, XIRR_FLOW, f"={c}", st.money, amount)
        if spread:
            ws.write_formula(row, PAR_FLOW, f"={c}", st.money, amount)
    total = first + len(flows) + 1
    ws.write_string(total, KIND, "итого", st.head)
    for col, cached in (
        (PV, sums.total),
        (T_PV, sums.weighted),
        (T_T1_PV, sums.plus),
        (T2_PV, sums.squared),
    ):
        ws.write_formula(
            total, col, f"=SUM({cell(first + 1, col)}:{cell(total - 1, col)})", st.money6, cached
        )


def _amount(
    sheet: Sheet,
    found: Analytics,
    horizon: Horizon,
    flow: CashFlow,
    coupon: Coupon | None,
    *,
    row: int,
) -> tuple[str | None, str]:
    """Формула суммы потока и как она получена; без формулы — сумма из графика."""
    st, ws = sheet.st, sheet.ws
    a, n = cell(row, DATE), cell(row, NOMINAL)
    grown = 0.0
    if found.type is BondType.LINKER:
        ruonia = _f(cast(SpreadView, found.spread).funding.value) / 100
        grown = _f(found.face_value) * (1 + ruonia / 365) ** (flow.date - found.settlement).days
        growth = f"=Номинал*(1+RUONIA_оценка/100/365)^({a}-Дата_расчётов)"
        ws.write_formula(row, NOMINAL, growth, st.money6, grown)
    if coupon is not None:
        return _coupon(sheet, found, coupon, row)
    price = _f(horizon.price)
    if found.type is BondType.LINKER:
        # На горизонте внутри периода к номиналу добавляется НКД периода — обычно его нет.
        extra = _f(flow.amount) - grown * price / 100
        tail = f"+{extra:.10f}" if abs(extra) > TINY else ""
        return f"={n}*{price:g}/100{tail}", "N(T) = N(S)·(1+R̂/365)^(T−S) по цене горизонта"
    if flow.kind is FlowKind.OFFER:
        return None, f"остаток номинала по цене оферты {price:g}% с НКД периода"
    return None, "по графику погашений"


def _coupon(sheet: Sheet, found: Analytics, coupon: Coupon, row: int) -> tuple[str | None, str]:
    """Купон: у линкера — от растущего номинала, у флоатера — прогноз по ставке индекса."""
    st, ws = sheet.st, sheet.ws
    m, n, k = (cell(row, col) for col in (PERIOD, NOMINAL, RATE))
    ws.write_datetime(row, START, coupon.start, st.date)
    ws.write_number(row, PERIOD, coupon.days, st.days)
    if found.type is BondType.LINKER:
        ws.write_number(row, RATE, _f(cast(Decimal, coupon.rate)), st.input_share)
        return f"={k}*{n}*{m}/365", "k × N(D) × d/365, N(D) = N(S)·(1+R̂/365)^(D−S)"
    spec = found.terms.floater
    if spec is None or coupon.amount is not None:
        return None, "купон из графика"
    nominal = _f(found.terms.bond.outstanding(coupon.start))
    ws.write_number(row, NOMINAL, nominal, st.input_money)
    if spec.capitalized:
        return (
            f"={n}*((1+Индекс/100/365)^{m}-1+Маржа*{m}/365)",
            "прогноз: N × ((1+I/365)^d − 1 + m·d/365)",
        )
    return f"={n}*(Индекс/100+Маржа)*{m}/365", "прогноз: N × (I + m) × d/365"


# --- разложение спреда -----------------------------------------------------------------


def _breakdown(sheet: Sheet, found: Analytics, view: SpreadView, flows: _Flows) -> None:
    st = sheet.st
    result, spec = view.result, view.terms.floater
    page, first, last = flows.sheet, flows.first, flows.last
    settle, end = found.settlement, result.horizon.date
    coupons = [c for c in view.terms.bond.coupons if settle < c.end <= end]
    sheet.title("Разложение спреда: каждая часть меняет одно условие (design/04)")
    sheet.heading("Величина", "Значение", "Сервис", "Расхождение", "Пояснение")
    sheet.formula(
        "τ — средняя длина купонного периода до горизонта, лет",
        f"=AVERAGE({page.column(PERIOD, first + 1, last)})/365",
        sum(c.days for c in coupons) / len(coupons) / 365,
        st.number,
        name="Тау",
    )
    if spec is None:
        rates = [_f(cast(Decimal, c.rate)) for c in coupons]
        sheet.formula(
            "k — средняя ставка купона линкера",
            f"=AVERAGE({page.column(RATE, first + 1, last)})",
            sum(rates) / len(rates),
            st.share,
            name="Купон_линкера",
        )
    parts = result.breakdown
    sheet.formula(
        "Доходность при чистой цене 100 (ЧИСТВНДОХ)",
        f"=XIRR({page.column(PAR_FLOW, first, last)},{page.column(DATE, first, last)})",
        _f(result.funding + parts.total - parts.price),
        st.share,
        name="Доходность_при_100",
        note="столбец «Поток при цене 100»: покупка по номиналу с НКД",
    )
    _parts(
        sheet,
        found,
        Measure.ANNUAL,
        parts,
        result.spread,
        at_par="Доходность_при_100-Фондирование",
        spread="Доходность_XIRR-Фондирование",
    )
    _parts(
        sheet,
        found,
        Measure.CONTINUOUS,
        result.continuous.breakdown,
        result.continuous.spread,
        at_par="LN(1+Доходность_при_100)-LN(1+Фондирование)",
        spread="LN(1+Доходность_XIRR)-LN(1+Фондирование)",
    )


def _parts(
    sheet: Sheet,
    found: Analytics,
    measure: Measure,
    parts: Breakdown,
    value: Decimal,
    *,
    at_par: str,
    spread: str,
) -> None:
    """Цепочка частей спреда в одном измерении, бп."""
    st = sheet.st
    floater = found.terms.floater is not None
    sheet.row += 1
    sheet.heading(
        "Эффективные годовые, бп" if measure is Measure.ANNUAL else "Непрерывная капитализация, бп",
        "Значение",
        "Сервис",
        "Расхождение",
        "Пояснение",
    )
    rows = []
    for label, formula, part, note in zip(
        ("Базис ставки", "Капитализация", "Маржа" if floater else "Купон"),
        _part_formulas(found, measure),
        (parts.basis, parts.capitalization, parts.margin),
        (
            "ставка индекса вместо RUONIA",
            "простая ставка за период вместо ежедневной капитализации",
            "сверх ставки индекса" if floater else "купон линкера сверх роста номинала",
        ),
        strict=True,
    ):
        bp = _f(part) * 10_000
        rows.append(sheet.formula(label, f"=({formula})*10000", bp, st.rate, service=bp, note=note))
    at_par_bp = _f(parts.total - parts.price) * 10_000
    par_row = sheet.formula("Спред при чистой цене 100", f"=({at_par})*10000", at_par_bp, st.rate)
    summed = "+".join(cell(r, 1) for r in rows)
    other = _f(parts.other) * 10_000
    sheet.formula(
        "Прочее = спред при 100 − базис − капитализация − маржа",
        f"={cell(par_row, 1)}-({summed})",
        other,
        st.rate,
        service=other,
        note="неполный текущий период, объявленный купон, НКД",
    )
    total = _f(value) * 10_000
    spread_row = sheet.formula("Спред", f"=({spread})*10000", total, st.rate, service=total)
    price = _f(parts.price) * 10_000
    sheet.formula(
        "Поправка на цену = спред − спред при 100",
        f"={cell(spread_row, 1)}-{cell(par_row, 1)}",
        price,
        st.rate,
        service=price,
        note="дисконт или премия к номиналу",
    )


def _part_formulas(found: Analytics, measure: Measure) -> tuple[str, str, str]:
    """Базис, капитализация и маржа (у линкера — купон) формулами Excel, доли."""
    spec = found.terms.floater
    i, r = "Индекс/100", "RUONIA_оценка/100"
    if measure is Measure.ANNUAL:
        if spec is None:
            return "0", "0", "(1+Фондирование)*((1+Купон_линкера*Тау)^(1/Тау)-1)"
        basis = f"(1+{i}/365)^365-(1+{r}/365)^365"
        if spec.capitalized:
            margin = f"((1+{i}/365)^(365*Тау)+Маржа*Тау)^(1/Тау)-1-((1+{i}/365)^365-1)"
            return basis, "0", margin
        simple = f"(1+{i}*Тау)^(1/Тау)"
        return basis, f"{simple}-1-((1+{i}/365)^365-1)", f"(1+({i}+Маржа)*Тау)^(1/Тау)-{simple}"
    if spec is None:
        return "0", "0", "LN(1+Купон_линкера*Тау)/Тау"
    basis = f"365*LN(1+{i}/365)-365*LN(1+{r}/365)"
    if spec.capitalized:
        return basis, "0", f"LN((1+{i}/365)^(365*Тау)+Маржа*Тау)/Тау-365*LN(1+{i}/365)"
    return (
        basis,
        f"LN(1+{i}*Тау)/Тау-365*LN(1+{i}/365)",
        f"(LN(1+({i}+Маржа)*Тау)-LN(1+{i}*Тау))/Тау",
    )


# --- итог и методика -------------------------------------------------------------------


def _price(sheet: Sheet, found: Analytics) -> None:
    st = sheet.st
    valuation = found.valuation
    entered = found.basis in {PriceBasis.YIELD, PriceBasis.SPREAD}
    continuous = entered and found.inputs.measure is Measure.CONTINUOUS
    source = BASIS_LABELS[found.basis]
    source += "" if valuation is None else f": {PRICE_LABELS.get(valuation.type, valuation.type)}"
    source += " — в непрерывной капитализации" if continuous else ""
    sheet.title(f"Расчёт по облигации {found.instrument.short_name} ({found.instrument.secid})")
    sheet.text("Тип", TYPE_LABELS[found.type])
    sheet.text("Момент цены", f"{found.at.astimezone(MOSCOW):%d.%m.%Y %H:%M} МСК")
    sheet.value("Торговый день", found.trading_day, st.date)
    sheet.formula("Дата расчётов S", "=Дата_расчётов", _serial(found.settlement), st.date)
    sheet.text("Откуда цена", source)
    sheet.formula("Чистая цена, %", "=Цена_чистая", _f(found.clean), st.rate)
    sheet.formula("Грязная цена P, ₽", "=Цена_грязная", _f(found.dirty), st.money)
    sheet.row += 1
    sheet.heading("Результат", "Excel", "Сервис", "Расхождение", "Где посчитано")


def _fixed_summary(sheet: Sheet, pages: Sequence[tuple[HorizonYield, _Flows | None]]) -> None:
    st = sheet.st
    for item, flows in pages:
        label = f"{HORIZON_LABELS[item.horizon.kind]} {item.horizon.date:%d.%m.%Y}"
        if flows is None or item.result is None:
            problem = "" if item.problem is None else item.problem.message
            sheet.text(f"Доходность {label}", "не посчитать", problem)
            continue
        result, xirr = item.result, flows.sheet.ref(flows.xirr, 1)
        y = _f(result.yield_rate)
        sheet.formula(
            f"Доходность {label}, эффективная годовая",
            f"={xirr}",
            y,
            st.share,
            service=y,
            note=f"лист «{flows.sheet.name}», по ЧИСТВНДОХ",
        )
        sheet.formula(
            f"Доходность {label}, непрерывная",
            f"=LN(1+{xirr})",
            math.log(1 + y),
            st.share,
            service=_f(result.continuous_rate),
        )
        _metrics(sheet, flows, result.risk, result.continuous_risk, "Модифицированная дюрация")


def _spread_summary(sheet: Sheet, view: SpreadView, flows: _Flows) -> None:
    st = sheet.st
    result = view.result
    sheet.formula(
        "RUONIA — оценка R̂ = КС + базис, % годовых",
        "=RUONIA_оценка",
        _f(view.funding.value),
        st.rate,
        service=_f(view.funding.value),
        note="лист «Фондирование»",
    )
    sheet.formula(
        "Фондирование, эффективное годовое",
        "=Фондирование",
        _f(result.funding),
        st.share,
        service=_f(result.funding),
    )
    sheet.formula(
        "Фондирование, непрерывное",
        "=Фондирование_непр",
        _f(result.continuous.funding),
        st.share,
        service=_f(result.continuous.funding),
    )
    for label, formula, value in (
        ("Спред, эффективный годовой, бп", "Доходность_XIRR-Фондирование", result.spread),
        (
            "Спред, непрерывный, бп",
            "LN(1+Доходность_XIRR)-LN(1+Фондирование)",
            result.continuous.spread,
        ),
    ):
        bp = _f(value) * 10_000
        note = "листы «Потоки» и «Разложение спреда»"
        sheet.formula(label, f"=({formula})*10000", bp, st.rate, service=bp, note=note)
    _metrics(sheet, flows, result.risk, result.continuous.risk, "Спред-дюрация")


def _metrics(sheet: Sheet, flows: _Flows, annual: Risk, smooth: Risk, duration: str) -> None:
    for key, label, value in (
        ("modified", f"{duration}, годовые", annual.modified),
        ("dv01", "DV01, годовые, ₽ за 1 бп", annual.dv01),
        ("modified_c", f"{duration}, непрерывные", smooth.modified),
        ("dv01_c", "DV01, непрерывные, ₽ за 1 бп", smooth.dv01),
    ):
        ref = flows.sheet.ref(flows.metrics[key], 1)
        sheet.formula(label, f"={ref}", _f(value), sheet.st.number, service=_f(value))


def _method(sheet: Sheet, found: Analytics) -> None:
    sheet.title("Методика (docs/design/04-bond-analytics.md, решения 12, 75, 156–158)")
    lines = [
        "Время до потока t = (дата потока − S)/365; S — дата расчётов, следующий торговый день.",
        "Грязная цена P = чистая цена × непогашенный номинал / 100 + НКД на S.",
        "Доходность y — эффективная годовая: P = Σ CF·(1 + y)^(−t). Сервис находит её методом "
        "Ньютона, Excel проверяет функцией ЧИСТВНДОХ (XIRR) — у неё та же конвенция.",
        "Непрерывная доходность y_c = ln(1 + y) — приближение однодневной ставки; "
        "дисконт тот же: (1 + y)^(−t) = e^(−y_c·t).",
        "Дюрация Маколея D = Σ t·PV / P. Модифицированная: в годовых — D/(1 + y), "
        "в непрерывных — D.",
        "DV01 = модифицированная дюрация × P / 10 000 — рублей на бумагу за 1 бп.",
        "Выпуклость: в годовых — Σ t(t+1)·PV / (P·(1 + y)²), в непрерывных — Σ t²·PV / P.",
    ]
    if found.spread is not None:
        lines += [
            "Флоатер и линкер: не доходность, а спред над фондированием по RUONIA.",
            "RUONIA для расчёта — оценка R̂ = ключевая ставка на день цены + базис Б "
            "(решение 157). Б — DEMA базиса RUONIA − КС с периодом полураспада полгода "
            f"по календарным дням (лист «{BASIS_SHEET}»).",
            "Фондирование: эффективное Eф = (1 + R̂/365)^365 − 1, непрерывное — 365·ln(1 + R̂/365).",
            "Спред s: P = Σ CF·(1 + Eф + s)^(−t); непрерывный — ln(1 + Eф + s) − ln(1 + Eф).",
            "Прогноз купона флоатера — плоский: ставка индекса на все будущие периоды равна "
            "текущей. Без капитализации N·(I + m)·d/365, с капитализацией "
            "N·((1 + I/365)^d − 1 + m·d/365).",
            "Номинал линкера растёт с RUONIA: N(D) = N(S)·(1 + R̂/365)^(D − S); "
            "купон — k·N(D)·d/365.",
            "Разложение спреда — цепочкой: базис, капитализация, маржа (у линкера — купон); "
            "«прочее» и поправка на цену замыкают её на спред точно.",
        ]
    lines.append(
        "Жёлтые ячейки — входные данные, остальное — формулы. «Расхождение» — Excel минус сервис."
    )
    for line in lines:
        sheet.text(line)
    sheet.ws.set_column(0, 0, 150)
