"""Сделки: ввод, правка, отмена, подтверждение (design/05, «Сделка»).

Сделка не меняется и не удаляется: каждое изменение — новая версия.
Изменение, отметка «фонд пересчитать с даты» и событие для журнала
пишутся одной транзакцией. Запросы к соседним сервисам — до транзакции:
держать её открытой во время сетевого запроса нельзя.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from am_common.auth import Principal, Role
from am_common.calendar import add_trading_days, is_trading_day
from am_common.clock import MOSCOW
from am_common.events import Actor, Event
from am_common.outbox import Outbox
from booking.clients import (
    MarketdataClient,
    PortfolioClient,
    PortfolioRef,
    RefdataClient,
    ServiceUnavailableError,
)
from booking.db import outbox
from booking.repository import CANCELLED, Repository
from booking.roll import HUNDRED, ZERO, InstrumentKind, Side, trade_amount

SERVICE = "booking"
ENTERED, CONFIRMED = "entered", "confirmed"


class RuleViolationError(Exception):
    """Нарушено правило ввода сделки. ``status`` — код ответа HTTP."""

    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True, slots=True)
class TradeInput:
    portfolio_id: UUID
    instrument_id: UUID
    side: Side
    quantity: int
    price: Decimal
    trade_at: datetime
    settle_date: date
    face_value: Decimal | None = None
    accrued: Decimal | None = None
    fee: Decimal = ZERO
    venue: str = "exchange"
    counterparty: str | None = None
    external_id: str | None = None
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class Defaults:
    """Подсказка для формы сделки: дата расчётов T+1, номинал и НКД на неё (решение 86)."""

    instrument_id: UUID
    kind: str
    secid: str
    short_name: str
    settle_date: date
    face_value: Decimal | None
    accrued: Decimal | None


@dataclass(frozen=True, slots=True)
class TradeView:
    versions: list[Row[Any]]
    portfolio: PortfolioRef | None
    warnings: list[str] = field(default_factory=list)

    @property
    def current(self) -> Row[Any]:
        return self.versions[-1]


class TradeDesk:
    def __init__(
        self,
        session: AsyncSession,
        principal: Principal,
        authorization: str,
        *,
        refdata: RefdataClient,
        marketdata: MarketdataClient,
        portfolios: PortfolioClient,
        clock: Callable[[], datetime],
    ) -> None:
        self._session = session
        self._principal = principal
        self._actor = Actor.from_principal(principal)
        self._authorization = authorization
        self._refdata = refdata
        self._marketdata = marketdata
        self._portfolios = portfolios
        self._clock = clock
        self._repo = Repository(session)
        self._outbox = Outbox(outbox)

    # --- подсказка для формы --------------------------------------------------

    async def defaults(self, instrument_id: UUID, trade_date: date) -> Defaults:
        history = await self._refdata.history(instrument_id)
        if history is None:
            raise RuleViolationError("unknown_instrument", "Бумаги нет в справочнике.")
        exceptions = await self._refdata.calendar_around(trade_date)
        settle = add_trading_days(trade_date, 1, exceptions)
        face = accrued = None
        if history.kind == InstrumentKind.BOND:
            face = await self._face_value(instrument_id, history.version_on(settle), settle)
            accrued = await self._on_date(instrument_id, "accrued-interest", settle)
        return Defaults(
            instrument_id, history.kind, history.secid, history.short_name, settle, face, accrued
        )

    # --- изменения ------------------------------------------------------------

    async def enter(self, entry: TradeInput) -> TradeView:
        visible = await self._portfolios.visible(self._authorization)
        values, warnings = await self._prepare(entry, visible, None)
        trade_id = uuid4()
        now = self._clock()
        async with self._session.begin():
            await self._repo.add_trade(
                {"id": trade_id, "created_at": now, "created_by": self._actor.username}
            )
            version = self._version(
                trade_id, 1, status=ENTERED, values=values, comment=entry.comment, now=now
            )
            await self._repo.add_version(version)
            await self._repo.mark_dirty(values["fund_id"], values["trade_date"], now)
            await self._emit(
                "trade.entered",
                trade_id,
                before=None,
                after=version,
                visible=visible,
                comment=entry.comment,
            )
            versions = await self._repo.versions(trade_id)
        return TradeView(versions, visible.get(entry.portfolio_id), warnings)

    async def amend(self, trade_id: UUID, entry: TradeInput) -> TradeView:
        visible = await self._portfolios.visible(self._authorization)
        async with self._session.begin():
            before = (await self._repo.versions(trade_id))[-1]
        self._check_change(before, visible)
        values, warnings = await self._prepare(entry, visible, trade_id)
        now = self._clock()
        async with self._session.begin():
            # Сделку могли изменить, пока шли проверки: проверяем ещё раз под блокировкой.
            before = await self._latest(trade_id, visible)
            self._check_change(before, visible)
            version = self._version(
                trade_id,
                before.number + 1,
                status=ENTERED,
                values=values,
                comment=entry.comment,
                now=now,
            )
            await self._repo.add_version(version)
            since = min(before.trade_date, values["trade_date"])
            for fund_id in {before.fund_id, values["fund_id"]}:
                await self._repo.mark_dirty(fund_id, since, now)
            await self._emit(
                "trade.amended",
                trade_id,
                before=before,
                after=version,
                visible=visible,
                comment=entry.comment,
            )
            versions = await self._repo.versions(trade_id)
        return TradeView(versions, visible.get(entry.portfolio_id), warnings)

    async def cancel(self, trade_id: UUID, comment: str) -> TradeView:
        return await self._restate(trade_id, CANCELLED, "trade.cancelled", comment)

    async def confirm(self, trade_id: UUID, comment: str | None) -> TradeView:
        return await self._restate(trade_id, CONFIRMED, "trade.confirmed", comment)

    async def view(self, trade_id: UUID) -> TradeView:
        visible = await self._portfolios.visible(self._authorization)
        async with self._session.begin():
            versions = await self._repo.versions(trade_id)
        portfolio = visible.get(versions[-1].portfolio_id)
        if portfolio is None:
            raise RuleViolationError("trade_not_found", "Сделка не найдена.", 404)
        return TradeView(versions, portfolio)

    async def _restate(
        self, trade_id: UUID, status: str, event: str, comment: str | None
    ) -> TradeView:
        """Новая версия с теми же полями и другим статусом."""
        visible = await self._portfolios.visible(self._authorization)
        now = self._clock()
        async with self._session.begin():
            before = await self._latest(trade_id, visible)
            self._check_change(before, visible)
            if status == CONFIRMED and before.status == CONFIRMED:
                raise RuleViolationError("already_confirmed", "Сделка уже подтверждена.")
            values = {
                name: getattr(before, name)
                for name in before._fields
                if name not in {"trade_id", "number", "status", "comment", "author", "created_at"}
            }
            version = self._version(
                trade_id, before.number + 1, status=status, values=values, comment=comment, now=now
            )
            await self._repo.add_version(version)
            if status == CANCELLED:
                await self._repo.mark_dirty(before.fund_id, before.trade_date, now)
            await self._emit(
                event, trade_id, before=before, after=version, visible=visible, comment=comment
            )
            versions = await self._repo.versions(trade_id)
        return TradeView(versions, visible.get(before.portfolio_id))

    async def _latest(self, trade_id: UUID, visible: Mapping[UUID, PortfolioRef]) -> Row[Any]:
        latest = (await self._repo.versions(trade_id, lock=True))[-1]
        if latest.portfolio_id not in visible:
            raise RuleViolationError("trade_not_found", "Сделка не найдена.", 404)
        return latest

    def _check_change(self, before: Row[Any], visible: Mapping[UUID, PortfolioRef]) -> None:
        if before.portfolio_id not in visible:
            raise RuleViolationError("trade_not_found", "Сделка не найдена.", 404)
        if before.status == CANCELLED:
            raise RuleViolationError("trade_cancelled", "Сделка отменена — её уже не изменить.")
        if before.status == CONFIRMED and not self._principal.has_role(Role.OPERATIONS):
            raise RuleViolationError(
                "confirmed_trade",
                "Сделка подтверждена — исправить или отменить её может сопровождение.",
                403,
            )

    # --- проверка и расчёт полей ----------------------------------------------

    async def _prepare(
        self,
        entry: TradeInput,
        visible: Mapping[UUID, PortfolioRef],
        trade_id: UUID | None,
    ) -> tuple[dict[str, Any], list[str]]:
        portfolio = visible.get(entry.portfolio_id)
        if portfolio is None:
            raise RuleViolationError(
                "portfolio_unavailable", "Нет такого портфеля или к нему нет доступа."
            )
        if portfolio.kind != "physical":
            raise RuleViolationError(
                "portfolio_not_physical",
                f"Портфель {portfolio.code} — составной; сделки вводятся в портфель со счётом.",
            )
        trade_date = entry.trade_at.astimezone(MOSCOW).date()
        if trade_date > self._clock().astimezone(MOSCOW).date():
            raise RuleViolationError("future_trade", "Дата сделки позже сегодняшней.")
        if entry.settle_date < trade_date:
            raise RuleViolationError("settle_before_trade", "Дата расчётов раньше даты заключения.")
        history = await self._refdata.history(entry.instrument_id)
        if history is None:
            raise RuleViolationError("unknown_instrument", "Бумаги нет в справочнике.")
        exceptions = await self._refdata.calendar_around(entry.settle_date)
        if not is_trading_day(entry.settle_date, exceptions):
            raise RuleViolationError(
                "settle_not_trading_day",
                f"{entry.settle_date:%d.%m.%Y} — не торговый день: расчётов нет.",
            )
        version = history.version_on(entry.settle_date)
        if version is None:
            raise RuleViolationError("not_issued", "На дату расчётов бумага ещё не выпущена.")
        maturity = version[1].get("maturity_date")
        if maturity is not None and date.fromisoformat(maturity) < entry.settle_date:
            raise RuleViolationError("matured", f"Бумага погашена {maturity}.")

        kind = InstrumentKind(history.kind)
        face = accrued = None
        if kind is InstrumentKind.BOND:
            face = entry.face_value or await self._face_value(
                entry.instrument_id, version, entry.settle_date
            )
            if face is None:
                raise RuleViolationError(
                    "face_value_required",
                    "Номинал на дату расчётов неизвестен — укажите его по подтверждению брокера.",
                )
            accrued = entry.accrued
            if accrued is None:
                accrued = await self._on_date(
                    entry.instrument_id, "accrued-interest", entry.settle_date
                )
            if accrued is None:
                raise RuleViolationError(
                    "accrued_required",
                    "НКД на дату расчётов неизвестен — укажите его по подтверждению брокера.",
                )
        values = {
            # У physical ровно один фонд — это проверяет сервис портфелей.
            "fund_id": cast(UUID, portfolio.fund_id),
            "portfolio_id": entry.portfolio_id,
            "instrument_id": entry.instrument_id,
            "instrument_kind": kind.value,
            "secid": history.secid,
            "short_name": history.short_name,
            "side": entry.side.value,
            "quantity": entry.quantity,
            "price": entry.price,
            "face_value": face,
            "accrued": accrued,
            "amount": trade_amount(kind, entry.quantity, entry.price, face, accrued),
            "fee": entry.fee,
            "trade_at": entry.trade_at,
            "trade_date": trade_date,
            "settle_date": entry.settle_date,
            "venue": entry.venue,
            "counterparty": entry.counterparty,
            "external_id": entry.external_id,
        }
        return values, await self._warnings(entry, trade_date, trade_id)

    async def _warnings(
        self, entry: TradeInput, trade_date: date, trade_id: UUID | None
    ) -> list[str]:
        """Продажа больше позиции — не запрет, а предупреждение (решение 89)."""
        if entry.side is not Side.SELL:
            return []
        async with self._session.begin():
            rows = await self._repo.current(
                portfolio_ids=[entry.portfolio_id], include_cancelled=False
            )
        position = sum(
            r.quantity if r.side == Side.BUY else -r.quantity
            for r in rows
            if r.instrument_id == entry.instrument_id
            and r.trade_date <= trade_date
            and r.trade_id != trade_id
        )
        if position >= entry.quantity:
            return []
        return [
            f"Продажа больше позиции: на {trade_date:%d.%m.%Y} в портфеле {position} шт., "
            f"продаётся {entry.quantity}. Дефицит к расчётам покажет лестница."
        ]

    async def _face_value(
        self, instrument_id: UUID, version: tuple[Any, Mapping[str, Any]] | None, day: date
    ) -> Decimal | None:
        """Номинал на дату расчётов: из рыночных данных, иначе — по графику погашений."""
        found = await self._on_date(instrument_id, "face-value", day)
        if found is not None or version is None:
            return found
        params = version[1]
        if params.get("nominal_indexation", "none") != "none":
            return None
        if params.get("initial_face_value") is None:
            return None
        face = Decimal(params["initial_face_value"])
        repaid = ZERO
        for row in params.get("amortizations", []):
            if date.fromisoformat(row["date"]) > day or row.get("is_maturity"):
                continue
            if row.get("amount") is not None:
                repaid += Decimal(row["amount"])
            elif row.get("percent") is not None:
                repaid += Decimal(row["percent"]) * face / HUNDRED
        return face - repaid

    async def _on_date(self, instrument_id: UUID, what: str, day: date) -> Decimal | None:
        try:
            return await self._marketdata.on_date(instrument_id, what, day)
        except ServiceUnavailableError:
            return None

    # --- версии и журнал ------------------------------------------------------

    def _version(
        self,
        trade_id: UUID,
        number: int,
        *,
        status: str,
        values: Mapping[str, Any],
        comment: str | None,
        now: datetime,
    ) -> dict[str, Any]:
        return {
            **values,
            "trade_id": trade_id,
            "number": number,
            "status": status,
            "comment": comment,
            "author": self._actor.username,
            "created_at": now,
        }

    async def _emit(
        self,
        event_type: str,
        trade_id: UUID,
        *,
        before: Row[Any] | None,
        after: Mapping[str, Any],
        visible: Mapping[UUID, PortfolioRef],
        comment: str | None,
    ) -> None:
        portfolio = visible.get(after["portfolio_id"])

        def code(portfolio_id: UUID) -> str | None:
            found = visible.get(portfolio_id)
            return None if found is None else found.code

        await self._outbox.add(
            self._session,
            Event(
                service=SERVICE,
                type=event_type,
                actor=self._actor,
                entity_type="trade",
                entity_id=str(trade_id),
                data={
                    "trade": {
                        "secid": after["secid"],
                        "portfolio": None if portfolio is None else portfolio.code,
                    },
                    "before": None
                    if before is None
                    else journal_view(dict(before._mapping), code(before.portfolio_id)),
                    "after": journal_view(after, code(after["portfolio_id"])),
                },
                comment=comment,
            ),
        )


JOURNAL_FIELDS: Sequence[str] = (
    "status",
    "side",
    "quantity",
    "price",
    "accrued",
    "amount",
    "fee",
    "trade_date",
    "settle_date",
    "secid",
)


def journal_view(values: Mapping[str, Any], portfolio: str | None) -> dict[str, Any]:
    """Поля сделки для журнала: портфель — кодом, числа и даты — строками."""
    view = {
        name: None if values.get(name) is None else str(values[name]) for name in JOURNAL_FIELDS
    }
    return {"portfolio": portfolio, **view}
