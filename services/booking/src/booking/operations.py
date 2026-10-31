"""Операции фонда и переводы (design/05, «Операции фонда и переводы»).

Операция, как и сделка, не меняется и не удаляется: отметка расчёта по паям
и отмена — новые версии. Паи и расходы проводятся в казначейский портфель
фонда (решение 35), переводы — только между портфелями одного фонда
(решение 37). Изменение, отметка «фонд пересчитать с даты» и событие для
журнала пишутся одной транзакцией; запросы к соседям — до неё.
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

from am_common.auth import Principal
from am_common.calendar import is_trading_day
from am_common.clock import MOSCOW
from am_common.events import Actor, Event
from am_common.outbox import Outbox
from booking.clients import PortfolioClient, PortfolioRef, RefdataClient
from booking.db import outbox
from booking.repository import CANCELLED, Repository
from booking.roll import InstrumentKind, OperationKind, Side
from booking.trades import SERVICE, RuleViolationError

ACTIVE = "active"
UNITS = frozenset({OperationKind.UNIT_ISSUE, OperationKind.UNIT_REDEMPTION})
EXPENSES = frozenset({OperationKind.EXPENSE_ACCRUAL, OperationKind.EXPENSE_PAYMENT})
# Виды расходов — короткий список (вопрос 94).
EXPENSE_KINDS: Sequence[str] = ("specdep", "registrar", "auditor", "exchange_broker", "other")


@dataclass(frozen=True, slots=True)
class OperationInput:
    """Что вводят. Какие поля нужны какому виду, проверяет схема API."""

    kind: OperationKind
    op_date: date
    fund_id: UUID | None = None  # паи и расходы
    portfolio_id: UUID | None = None  # перевод: отправитель
    receiver_id: UUID | None = None  # перевод: получатель
    amount: Decimal | None = None
    units: Decimal | None = None
    planned_date: date | None = None
    expense_kind: str | None = None
    instrument_id: UUID | None = None
    quantity: int | None = None
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class OperationView:
    versions: list[Row[Any]]
    visible: Mapping[UUID, PortfolioRef]
    warnings: list[str] = field(default_factory=list)

    @property
    def current(self) -> Row[Any]:
        return self.versions[-1]


class OperationDesk:
    def __init__(
        self,
        session: AsyncSession,
        principal: Principal,
        authorization: str,
        *,
        refdata: RefdataClient,
        portfolios: PortfolioClient,
        clock: Callable[[], datetime],
    ) -> None:
        self._session = session
        self._actor = Actor.from_principal(principal)
        self._authorization = authorization
        self._refdata = refdata
        self._portfolios = portfolios
        self._clock = clock
        self._repo = Repository(session)
        self._outbox = Outbox(outbox)

    async def enter(self, entry: OperationInput) -> OperationView:
        visible = await self._portfolios.visible(self._authorization)
        values, warnings = await self._prepare(entry, visible)
        operation_id = uuid4()
        now = self._clock()
        async with self._session.begin():
            await self._repo.add_operation(
                {"id": operation_id, "created_at": now, "created_by": self._actor.username}
            )
            version = self._version(
                operation_id, 1, status=ACTIVE, values=values, comment=entry.comment, now=now
            )
            await self._repo.add_operation_version(version)
            await self._repo.mark_dirty(values["fund_id"], values["op_date"], now)
            await self._emit(
                "operation.entered",
                before=None,
                after=version,
                visible=visible,
                comment=entry.comment,
            )
            versions = await self._repo.operation_versions(operation_id)
        return OperationView(versions, visible, warnings)

    async def settle(self, operation_id: UUID, settled: date, comment: str | None) -> OperationView:
        """Сопровождение отмечает расчёт по паям: деньги пришли или ушли (решение 42)."""
        visible = await self._portfolios.visible(self._authorization)
        exceptions = await self._refdata.calendar_around(settled)
        now = self._clock()
        async with self._session.begin():
            before = await self._latest(operation_id, visible)
            if OperationKind(before.kind) not in UNITS:
                raise RuleViolationError(
                    "not_unit_operation", "Расчёт отмечают только по выдаче и погашению паёв."
                )
            if before.settled_date is not None:
                raise RuleViolationError(
                    "already_settled", f"Расчёт уже отмечен {before.settled_date:%d.%m.%Y}."
                )
            self._check_money_day(settled, before.op_date, exceptions, "Дата расчёта")
            values = {**_values(before), "settled_date": settled}
            version = self._version(
                operation_id,
                before.number + 1,
                status=ACTIVE,
                values=values,
                comment=comment,
                now=now,
            )
            await self._repo.add_operation_version(version)
            await self._repo.mark_dirty(before.fund_id, settled, now)
            await self._emit(
                "operation.settled", before=before, after=version, visible=visible, comment=comment
            )
            versions = await self._repo.operation_versions(operation_id)
        return OperationView(versions, visible)

    async def cancel(self, operation_id: UUID, comment: str) -> OperationView:
        visible = await self._portfolios.visible(self._authorization)
        now = self._clock()
        async with self._session.begin():
            before = await self._latest(operation_id, visible)
            version = self._version(
                operation_id,
                before.number + 1,
                status=CANCELLED,
                values=_values(before),
                comment=comment,
                now=now,
            )
            await self._repo.add_operation_version(version)
            await self._repo.mark_dirty(before.fund_id, before.op_date, now)
            await self._emit(
                "operation.cancelled",
                before=before,
                after=version,
                visible=visible,
                comment=comment,
            )
            versions = await self._repo.operation_versions(operation_id)
        return OperationView(versions, visible)

    async def view(self, operation_id: UUID) -> OperationView:
        visible = await self._portfolios.visible(self._authorization)
        async with self._session.begin():
            versions = await self._repo.operation_versions(operation_id)
        if versions[-1].portfolio_id not in visible:
            raise RuleViolationError("operation_not_found", "Операция не найдена.", 404)
        return OperationView(versions, visible)

    async def _latest(self, operation_id: UUID, visible: Mapping[UUID, PortfolioRef]) -> Row[Any]:
        latest = (await self._repo.operation_versions(operation_id, lock=True))[-1]
        if latest.portfolio_id not in visible:
            raise RuleViolationError("operation_not_found", "Операция не найдена.", 404)
        if latest.status == CANCELLED:
            raise RuleViolationError(
                "operation_cancelled", "Операция отменена — её уже не изменить."
            )
        return latest

    # --- проверка и расчёт полей ----------------------------------------------

    async def _prepare(
        self, entry: OperationInput, visible: Mapping[UUID, PortfolioRef]
    ) -> tuple[dict[str, Any], list[str]]:
        if entry.op_date > self._clock().astimezone(MOSCOW).date():
            raise RuleViolationError("future_operation", "Дата операции позже сегодняшней.")
        if entry.kind in UNITS | EXPENSES:
            return await self._fund_operation(entry, visible), []
        return await self._transfer(entry, visible)

    async def _fund_operation(
        self, entry: OperationInput, visible: Mapping[UUID, PortfolioRef]
    ) -> dict[str, Any]:
        """Паи и расходы — в казначейский портфель фонда (решение 35)."""
        fund = None if entry.fund_id is None else visible.get(entry.fund_id)
        if fund is None or not fund.is_fund:
            raise RuleViolationError("fund_unavailable", "Нет такого фонда или к нему нет доступа.")
        if fund.treasury_id is None:
            raise RuleViolationError(
                "no_treasury",
                f"У фонда {fund.code} не назначен казначейский портфель — "
                "операции фонда проводятся в него.",
            )
        values: dict[str, Any] = {
            "kind": entry.kind.value,
            "fund_id": fund.id,
            "portfolio_id": fund.treasury_id,
            "op_date": entry.op_date,
            "amount": entry.amount,
        }
        if entry.kind in UNITS:
            planned = cast(date, entry.planned_date)
            exceptions = await self._refdata.calendar_around(planned)
            self._check_money_day(planned, entry.op_date, exceptions, "Плановая дата расчёта")
            return values | {"units": entry.units, "planned_date": planned}
        if entry.kind is OperationKind.EXPENSE_PAYMENT:
            # Начисление — без движения денег, в любой день; выплата — в торговый.
            exceptions = await self._refdata.calendar_around(entry.op_date)
            self._check_money_day(entry.op_date, entry.op_date, exceptions, "Дата выплаты")
        return values | {"expense_kind": entry.expense_kind}

    async def _transfer(
        self, entry: OperationInput, visible: Mapping[UUID, PortfolioRef]
    ) -> tuple[dict[str, Any], list[str]]:
        """Перевод — между physical-портфелями одного фонда (решения 37, 45)."""
        sender = None if entry.portfolio_id is None else visible.get(entry.portfolio_id)
        receiver = None if entry.receiver_id is None else visible.get(entry.receiver_id)
        if sender is None or receiver is None:
            raise RuleViolationError(
                "portfolio_unavailable", "Нет такого портфеля или к нему нет доступа."
            )
        for ref in (sender, receiver):
            if ref.kind != "physical":
                raise RuleViolationError(
                    "portfolio_not_physical",
                    f"Портфель {ref.code} — составной; переводят между портфелями со счётом.",
                )
        if sender.id == receiver.id:
            raise RuleViolationError("same_portfolio", "Отправитель и получатель совпадают.")
        if sender.fund_id != receiver.fund_id:
            raise RuleViolationError(
                "other_fund",
                f"{sender.code} и {receiver.code} — в разных фондах: переводят только "
                "внутри фонда.",
            )
        exceptions = await self._refdata.calendar_around(entry.op_date)
        self._check_money_day(entry.op_date, entry.op_date, exceptions, "Дата перевода")
        values: dict[str, Any] = {
            "kind": entry.kind.value,
            "fund_id": sender.fund_id,
            "portfolio_id": sender.id,
            "receiver_id": receiver.id,
            "op_date": entry.op_date,
        }
        if entry.kind is OperationKind.CASH_TRANSFER:
            return values | {"amount": entry.amount}, []
        instrument_id, quantity = cast(UUID, entry.instrument_id), cast(int, entry.quantity)
        history = await self._refdata.history(instrument_id)
        if history is None:
            raise RuleViolationError("unknown_instrument", "Бумаги нет в справочнике.")
        version = history.version_on(entry.op_date)
        if version is None:
            raise RuleViolationError("not_issued", "На дату перевода бумага ещё не выпущена.")
        maturity = version[1].get("maturity_date")
        if maturity is not None and date.fromisoformat(maturity) < entry.op_date:
            raise RuleViolationError("matured", f"Бумага погашена {maturity}.")
        values |= {
            "instrument_id": instrument_id,
            "instrument_kind": InstrumentKind(history.kind).value,
            "secid": history.secid,
            "short_name": history.short_name,
            "quantity": quantity,
        }
        return values, await self._warnings(sender, instrument_id, entry.op_date, quantity)

    async def _warnings(
        self, sender: PortfolioRef, instrument_id: UUID, day: date, quantity: int
    ) -> list[str]:
        """Перевести больше, чем на счёте депо, — не запрет: дефицит покажет лестница."""
        async with self._session.begin():
            trades = await self._repo.current(portfolio_ids=[sender.id], include_cancelled=False)
            operations = await self._repo.operations(portfolio_ids=[sender.id])
        position = sum(
            r.quantity if r.side == Side.BUY else -r.quantity
            for r in trades
            if r.instrument_id == instrument_id and r.settle_date <= day
        ) + sum(
            r.quantity if r.receiver_id == sender.id else -r.quantity
            for r in operations
            if r.status != CANCELLED and r.instrument_id == instrument_id and r.op_date <= day
        )
        if position >= quantity:
            return []
        return [
            f"Переводится больше, чем на счёте депо: на {day:%d.%m.%Y} в портфеле "
            f"{sender.code} {position} шт., переводится {quantity}. Дефицит покажет лестница."
        ]

    @staticmethod
    def _check_money_day(
        day: date, not_before: date, exceptions: Mapping[date, bool], what: str
    ) -> None:
        if day < not_before:
            raise RuleViolationError("date_before_operation", f"{what} раньше даты операции.")
        if not is_trading_day(day, exceptions):
            raise RuleViolationError(
                "not_trading_day",
                f"{day:%d.%m.%Y} — не торговый день: деньги и бумаги в этот день не переводят.",
            )

    # --- версии и журнал ------------------------------------------------------

    def _version(
        self,
        operation_id: UUID,
        number: int,
        *,
        status: str,
        values: Mapping[str, Any],
        comment: str | None,
        now: datetime,
    ) -> dict[str, Any]:
        return {
            **values,
            "operation_id": operation_id,
            "number": number,
            "status": status,
            "comment": comment,
            "author": self._actor.username,
            "created_at": now,
        }

    async def _emit(
        self,
        event_type: str,
        *,
        before: Row[Any] | None,
        after: Mapping[str, Any],
        visible: Mapping[UUID, PortfolioRef],
        comment: str | None,
    ) -> None:
        def code(portfolio_id: UUID | None) -> str | None:
            found = None if portfolio_id is None else visible.get(portfolio_id)
            return None if found is None else found.code

        await self._outbox.add(
            self._session,
            Event(
                service=SERVICE,
                type=event_type,
                actor=self._actor,
                entity_type="operation",
                entity_id=str(after["operation_id"]),
                data={
                    "operation": {"kind": after["kind"], "portfolio": code(after["portfolio_id"])},
                    "before": None
                    if before is None
                    else operation_journal(dict(before._mapping), code),
                    "after": operation_journal(after, code),
                },
                comment=comment,
            ),
        )


_FIXED = {"operation_id", "number", "status", "comment", "author", "created_at"}

JOURNAL_FIELDS: Sequence[str] = (
    "status",
    "kind",
    "op_date",
    "units",
    "amount",
    "planned_date",
    "settled_date",
    "expense_kind",
    "secid",
    "quantity",
)


def _values(row: Row[Any]) -> dict[str, Any]:
    """Поля версии без служебных — основа следующей версии."""
    return {name: getattr(row, name) for name in row._fields if name not in _FIXED}


def operation_journal(
    values: Mapping[str, Any], code: Callable[[UUID | None], str | None]
) -> dict[str, Any]:
    """Поля операции для журнала: портфели — кодами, числа и даты — строками."""
    view = {
        name: None if values.get(name) is None else str(values[name]) for name in JOURNAL_FIELDS
    }
    return {
        "portfolio": code(values["portfolio_id"]),
        "receiver": code(values.get("receiver_id")),
        **view,
    }
