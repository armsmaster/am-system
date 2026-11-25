"""СЧА и стоимость пая по дням в Excel (решение 168).

Значения — те же, что на странице «СЧА»: стоимость пая, сглаженная фильтром Калмана,
СЧА, паи, цена бумаги сравнения. Изменение за день — формулой Excel: видно, откуда оно.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

import xlsxwriter
from xlsxwriter.format import Format
from xlsxwriter.worksheet import Worksheet

from booking.queries import NavHistory

HEADERS = (
    "Дата",
    "Стоимость пая, ₽",
    "Сглаженная (Калман), ₽",
    "Изменение за день, ₽",
    "СЧА, ₽",
    "Паёв",
)
FIRST_ROW = 4  # над шапкой — название фонда и пояснение


def nav_workbook(history: NavHistory) -> bytes:
    """Книга с одним листом: заголовок, шапка и дни по порядку."""
    out = BytesIO()
    book = xlsxwriter.Workbook(out, {"in_memory": True})
    sheet = book.add_worksheet("СЧА")
    head = book.add_format({"bold": True, "bg_color": "#EEF1F5", "border": 1, "text_wrap": True})
    day = book.add_format({"num_format": "dd.mm.yyyy"})
    money = book.add_format({"num_format": "#,##0.00"})
    precise = book.add_format({"num_format": "#,##0.0000"})
    units = book.add_format({"num_format": "#,##0.00000"})
    sheet.write(
        0,
        0,
        f"{history.fund.code} — {history.fund.name}: СЧА и стоимость пая",
        book.add_format({"bold": True}),
    )
    sheet.write(1, 0, "Наша оценка, не официальная СЧА. Сглаженная — фильтр Калмана (решение 168).")
    headers = [*HEADERS, *([f"{history.benchmark}, ₽"] if history.benchmark else [])]
    for column, title in enumerate(headers):
        sheet.write(FIRST_ROW - 1, column, title, head)
    previous: Decimal | None = None
    for offset, point in enumerate(history.points):
        row = FIRST_ROW + offset
        sheet.write_datetime(row, 0, datetime(point.day.year, point.day.month, point.day.day), day)
        _number(sheet, row, 1, point.unit_price, money)
        _number(sheet, row, 2, point.smoothed, precise)
        if previous is not None and point.unit_price is not None:
            # Строки Excel считаются с единицы: B{row + 1} — эта строка, B{row} — прошлая.
            sheet.write_formula(
                row, 3, f"=B{row + 1}-B{row}", money, float(point.unit_price - previous)
            )
        sheet.write_number(row, 4, float(point.nav), money)
        sheet.write_number(row, 5, float(point.units), units)
        if history.benchmark:
            _number(sheet, row, 6, point.benchmark, precise)
        previous = point.unit_price
    sheet.set_column(0, 0, 12)
    sheet.set_column(1, len(headers) - 1, 18)
    sheet.freeze_panes(FIRST_ROW, 1)
    book.close()
    return out.getvalue()


def _number(sheet: Worksheet, row: int, column: int, value: Decimal | None, style: Format) -> None:
    """Число в ячейку; нет значения — ячейка пустая."""
    if value is not None:
        sheet.write_number(row, column, float(value), style)


def filename(history: NavHistory) -> str:
    """«nav-OPIF1-2026-12-03.xlsx» — по фонду и последнему дню в книге; латиницей для заголовка."""
    last: date = history.points[-1].day if history.points else history.today
    return f"nav-{history.fund.code}-{last.isoformat()}.xlsx"
