"""Импорт сделок пакетом: спецификация docs/api/trade-import (design/05, «Импорт сделок»).

Ключ сделки — источник и номер сделки в нём. Новый номер — новая сделка,
тот же номер с другими полями — новая версия, с теми же — ничего, признак
«отменена» — версия «отменена». Каждая сделка — своя транзакция: плохая
отклоняется отдельно, остальные принимаются, повтор пакета безопасен.
Правила ввода — те же, что у формы: импорт идёт через TradeDesk.
Сделка РЕПО (design/07) — с ``kind: repo``; код ``KSU`` — РЕПО с КСУ.
Проверка (``dry_run``) проходит те же правила и говорит, что случится со сделкой,
но ничего не записывает: так загрузка из отчёта QUIK показывает результат до записи.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from booking.clients import PortfolioRef, RefdataClient
from booking.repository import CANCELLED, ClosedPeriodError, Repository
from booking.roll import ZERO, Side
from booking.trades import TRACKED, RuleViolationError, TradeDesk, TradeInput

# Код обеспечения РЕПО с КСУ: бумаги нет, только деньги (решение 117).
KSU_CODES = frozenset({"KSU", "КСУ"})


@dataclass(frozen=True, slots=True)
class ImportItem:
    """Сделка из пакета — как её прислал источник."""

    external_id: str
    portfolio: str  # код портфеля
    code: str  # SECID или ISIN; KSU — РЕПО с КСУ
    side: Side
    trade_at: datetime
    settle_date: date
    quantity: int = 0
    price: Decimal = ZERO
    accrued: Decimal | None = None
    face_value: Decimal | None = None
    fee: Decimal = ZERO
    venue: str = "exchange"
    counterparty: str | None = None
    comment: str | None = None
    cancelled: bool = False
    kind: str = "outright"
    amount: Decimal | None = None  # РЕПО с КСУ: сумма первой части
    repo_rate: Decimal | None = None
    second_date: date | None = None
    second_amount: Decimal | None = None


class Result(StrEnum):
    CREATED = "created"
    AMENDED = "amended"
    CANCELLED = "cancelled"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class Imported:
    index: int
    external_id: str
    result: Result
    trade_id: UUID | None  # None — новая сделка при проверке: её ещё нет
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class Rejected:
    index: int
    external_id: str
    error: str
    message: str


@dataclass(slots=True)
class ImportReport:
    trades: list[Imported] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)

    def count(self, result: Result) -> int:
        return sum(1 for t in self.trades if t.result is result)


class TradeImport:
    def __init__(self, session: AsyncSession, desk: TradeDesk, refdata: RefdataClient) -> None:
        self._session = session
        self._desk = desk
        self._refdata = refdata
        self._repo = Repository(session)

    async def run(
        self, source: str, items: Sequence[ImportItem], *, dry_run: bool = False
    ) -> ImportReport:
        visible = await self._desk.visible()
        portfolios = {p.code.casefold(): p for p in visible.values()}
        codes = await self._refdata.codes()
        repeated = {k for k, n in Counter(i.external_id for i in items).items() if n > 1}
        report = ImportReport()
        for index, item in enumerate(items):
            try:
                if item.external_id in repeated:
                    raise RuleViolationError(
                        "duplicate_in_batch",
                        "Номер сделки повторяется в пакете — оставьте одну запись.",
                    )
                result, trade_id, warnings = await self._one(
                    source, item, visible, portfolios, codes, dry_run=dry_run
                )
            except RuleViolationError as exc:
                report.rejected.append(Rejected(index, item.external_id, exc.code, exc.message))
                continue
            except ClosedPeriodError as exc:
                report.rejected.append(Rejected(index, item.external_id, "day_closed", exc.message))
                continue
            report.trades.append(Imported(index, item.external_id, result, trade_id, warnings))
        return report

    async def _one(
        self,
        source: str,
        item: ImportItem,
        visible: Mapping[UUID, PortfolioRef],
        portfolios: Mapping[str, PortfolioRef],
        codes: Mapping[str, UUID],
        *,
        dry_run: bool,
    ) -> tuple[Result, UUID | None, list[str]]:
        async with self._session.begin():
            trade_id = await self._repo.trade_by_key(source, item.external_id)
            latest = None if trade_id is None else (await self._repo.versions(trade_id))[-1]
        if latest is not None and latest.portfolio_id not in visible:
            raise RuleViolationError(
                "portfolio_unavailable",
                "Сделка с этим номером — в портфеле, к которому нет доступа.",
            )
        if item.cancelled:
            return await self._cancel(source, item, latest, dry_run=dry_run)
        entry = _entry(item, portfolios, codes)
        if latest is None:
            if dry_run:
                _, warnings = await self._desk.check(entry, None)
                return Result.CREATED, None, warnings
            comment = item.comment or f"Загружена из {source}."
            view = await self._desk.enter(
                replace(entry, comment=comment), key=(source, item.external_id)
            )
            return Result.CREATED, view.current.trade_id, view.warnings
        if latest.status != CANCELLED:
            values, _ = await self._desk.prepare(entry, latest.trade_id)
            if all(values[name] == getattr(latest, name) for name in TRACKED):
                return Result.UNCHANGED, latest.trade_id, []
        # Отменённую и подтверждённую сделку правка не пропустит — с кодом и причиной.
        if dry_run:
            _, warnings = await self._desk.check(entry, latest.trade_id)
            return Result.AMENDED, latest.trade_id, warnings
        comment = item.comment or f"Исправлена в источнике {source}."
        view = await self._desk.amend(latest.trade_id, replace(entry, comment=comment))
        return Result.AMENDED, latest.trade_id, view.warnings

    async def _cancel(
        self, source: str, item: ImportItem, latest: Row[Any] | None, *, dry_run: bool
    ) -> tuple[Result, UUID | None, list[str]]:
        if latest is None:
            raise RuleViolationError(
                "unknown_trade", "Отменять нечего: сделки с этим номером в системе нет."
            )
        if latest.status == CANCELLED:
            return Result.UNCHANGED, latest.trade_id, []
        if dry_run:
            await self._desk.check_cancel(latest.trade_id)
        else:
            comment = item.comment or f"Отменена в источнике {source}."
            await self._desk.cancel(latest.trade_id, comment)
        return Result.CANCELLED, latest.trade_id, []


def _entry(
    item: ImportItem, portfolios: Mapping[str, PortfolioRef], codes: Mapping[str, UUID]
) -> TradeInput:
    portfolio = portfolios.get(item.portfolio.casefold())
    if portfolio is None:
        raise RuleViolationError(
            "portfolio_unavailable",
            f"Портфеля {item.portfolio} нет или к нему нет доступа.",
        )
    ksu = item.code.upper() in KSU_CODES
    instrument_id = None if ksu else codes.get(item.code.upper())
    if instrument_id is None and not ksu:
        raise RuleViolationError(
            "unknown_instrument",
            f"Бумаги {item.code} нет в справочнике. Загрузите её в разделе «Справочник».",
        )
    return TradeInput(
        portfolio_id=portfolio.id,
        instrument_id=instrument_id,
        side=item.side,
        quantity=item.quantity,
        price=item.price,
        trade_at=item.trade_at,
        settle_date=item.settle_date,
        face_value=item.face_value,
        accrued=item.accrued,
        fee=item.fee,
        venue=item.venue,
        counterparty=item.counterparty,
        external_id=item.external_id,
        kind=item.kind,
        amount=item.amount,
        repo_rate=item.repo_rate,
        second_date=item.second_date,
        second_amount=item.second_amount,
    )
