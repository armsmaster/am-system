"""Выписки и принятые расхождения (design/08): приём, история, сверка фонда за день.

Выписку присылает скрипт брокера или вводит сопровождение. При приёме: счёт
есть в системе, бумаги — в справочнике, остаток на начало плюс движения дают
остаток на конец (решение 119). Плохая выписка отклоняется отдельно, остальные
в пакете принимаются; пакет хранится как пришёл. Та же выписка ещё раз — ничего
не меняет, другая за тот же счёт и день — становится действующей.

Сверка не хранится: её считают при каждом запросе из выписок, дней счёта
в сервисе сделок и принятых расхождений. Принятое расхождение ждёт пары
и в следующие дни, поэтому сверку дня D считают подряд с самого раннего дня
с принятыми расхождениями (решение 123).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Row, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from am_common.auth import Principal
from am_common.clock import MOSCOW
from am_common.events import Actor, Event
from am_common.outbox import Outbox
from reconciliation.clients import (
    Account,
    BookingClient,
    Closing,
    Portfolio,
    PortfolioClient,
    RefdataClient,
)
from reconciliation.db import acceptance, checkpoint, outbox, statement, statement_batch
from reconciliation.matching import (
    RESOLVED,
    ZERO,
    Acceptance,
    DayReconciliation,
    MovementKey,
    OpenItem,
    Statement,
    StatementMovement,
    StatementTrade,
    reconcile,
)

SERVICE = "reconciliation"
MANUAL = "manual"
# Код обеспечения РЕПО с КСУ: бумаги нет (решение 117).
KSU_CODES = frozenset({"KSU", "КСУ"})
KOPECK = Decimal("0.01")
ONE_DAY = timedelta(days=1)
# Статусы счёта — словами, для отказа в закрытии дня.
ACCOUNT_STATUSES: Mapping[str, str] = {
    "discrepancies": "есть расхождения",
    "no_statement": "нет выписки за день",
    "recalculating": "фонд пересчитывается",
    "not_calculated": "расчёта за день ещё нет",
}


class RuleViolationError(Exception):
    """Нарушено правило приёма или сверки. ``status`` — код ответа HTTP."""

    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class NotFoundError(Exception):
    def __init__(self, what: str, key: object) -> None:
        super().__init__(f"{what} {key} not found")
        self.what = what


# --- что приходит ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TradeEntry:
    external_id: str
    side: str
    security: str
    settle_date: date
    amount: Decimal
    kind: str = "outright"
    quantity: int = 0
    price: Decimal = ZERO
    accrued: Decimal | None = None
    fee: Decimal = ZERO
    repo_rate: Decimal | None = None
    second_date: date | None = None
    second_amount: Decimal | None = None


@dataclass(frozen=True, slots=True)
class MovementEntry:
    kind: str
    amount: Decimal
    trade: str | None = None
    security: str | None = None
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class StatementEntry:
    """Выписка по счёту за день; в пакете скрипта счёт — кодом."""

    day: date
    opening_cash: Decimal
    closing_cash: Decimal
    account: str | None = None
    trades: Sequence[TradeEntry] = ()
    movements: Sequence[MovementEntry] = ()


class Result(StrEnum):
    CREATED = "created"
    REPLACED = "replaced"  # за счёт и день уже была другая выписка
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class Received:
    index: int
    account: str
    day: date
    statement_id: UUID
    result: Result


@dataclass(frozen=True, slots=True)
class Rejected:
    index: int
    account: str | None
    day: date
    error: str
    message: str


@dataclass(slots=True)
class Report:
    batch_id: UUID
    received: list[Received] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)

    def count(self, result: Result) -> int:
        return sum(1 for r in self.received if r.result is result)


# --- что отдаём -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccountReconciliation:
    account: Account
    portfolios: tuple[str, ...]
    waiting_since: date | None
    day: DayReconciliation
    previous: Statement | None  # последняя выписка до дня: для «движений нет»

    @property
    def status(self) -> str:
        found = self.day
        if found.statement is None:
            return "no_statement" if found.system.trading else "non_trading"
        if self.waiting_since is not None and self.waiting_since <= found.day:
            return "recalculating"
        if not found.system.calculated:
            return "not_calculated"
        return "discrepancies" if found.unresolved else "reconciled"


@dataclass(frozen=True, slots=True)
class FundDay:
    fund: Portfolio
    day: date
    accounts: list[AccountReconciliation]
    closing: Closing

    @property
    def closed(self) -> bool:
        through = self.closing.closed_through
        return through is not None and self.day <= through

    @property
    def trading(self) -> bool:
        return any(a.day.system.trading for a in self.accounts)

    @property
    def reconciled(self) -> bool:
        """Все счета фонда сверены — день можно закрыть (решение 48)."""
        return all(a.status in {"reconciled", "non_trading"} for a in self.accounts)


class Desk:
    """Выписки и сверка одного запроса: пользователь или скрипт брокера."""

    def __init__(
        self,
        session: AsyncSession,
        principal: Principal,
        authorization: str,
        *,
        portfolios: PortfolioClient,
        booking: BookingClient,
        refdata: RefdataClient,
        clock: Callable[[], datetime],
    ) -> None:
        self._session = session
        self._actor = Actor.from_principal(principal)
        self._authorization = authorization
        self._portfolios = portfolios
        self._booking = booking
        self._refdata = refdata
        self._clock = clock
        self._outbox = Outbox(outbox)
        self._codes: dict[str, tuple[UUID, str]] | None = None

    # --- приём ----------------------------------------------------------------

    async def receive(
        self, source: str, entries: Sequence[StatementEntry], payload: Mapping[str, Any]
    ) -> Report:
        """Пакет выписок: каждая принимается или отклоняется отдельно."""
        accounts = await self._portfolios.accounts(self._authorization)
        by_code = {a.code.upper(): a for a in accounts.values()}
        repeated = {
            key
            for key, n in Counter(((e.account or "").upper(), e.day) for e in entries).items()
            if n > 1
        }
        now = self._clock()
        report = Report(uuid4())
        prepared: list[tuple[int, Account, dict[str, Any]]] = []
        for index, entry in enumerate(entries):
            try:
                if ((entry.account or "").upper(), entry.day) in repeated:
                    raise RuleViolationError(
                        "repeated_statement",
                        f"В пакете несколько выписок по счёту {entry.account} "
                        f"за {entry.day:%d.%m.%Y} — оставьте одну.",
                    )
                account = by_code.get((entry.account or "").upper())
                if account is None:
                    raise RuleViolationError(
                        "unknown_account",
                        f"Счёта {entry.account} нет в системе или к нему нет доступа.",
                    )
                prepared.append((index, account, await self._prepare(entry, account)))
            except RuleViolationError as exc:
                report.rejected.append(
                    Rejected(index, entry.account, entry.day, exc.code, exc.message)
                )
        async with self._session.begin():
            await self._session.execute(
                insert(statement_batch).values(
                    id=report.batch_id,
                    source=source,
                    payload=dict(payload),
                    received_by=self._actor.username,
                    received_at=now,
                )
            )
            for index, account, values in prepared:
                statement_id, result = await self._store(report.batch_id, account, values, now)
                report.received.append(
                    Received(index, account.code, values["day"], statement_id, result)
                )
        return report

    async def enter(
        self, account_id: UUID, entry: StatementEntry, payload: Mapping[str, Any]
    ) -> Received:
        """Выписка вручную: по выписке банка или отчёту брокера (решение 47)."""
        account = await self._account(account_id)
        values = await self._prepare(entry, account)
        now = self._clock()
        batch_id = uuid4()
        async with self._session.begin():
            await self._session.execute(
                insert(statement_batch).values(
                    id=batch_id,
                    source=MANUAL,
                    payload=dict(payload),
                    received_by=self._actor.username,
                    received_at=now,
                )
            )
            statement_id, result = await self._store(batch_id, account, values, now)
        return Received(0, account.code, entry.day, statement_id, result)

    async def _prepare(self, entry: StatementEntry, account: Account) -> dict[str, Any]:
        """Проверки выписки и бумаги из справочника; поля для записи."""
        today = self._clock().astimezone(MOSCOW).date()
        if entry.day > today:
            raise RuleViolationError(
                "future_statement", f"Выписка за {entry.day:%d.%m.%Y} — позже сегодняшнего дня."
            )
        moved = sum((m.amount for m in entry.movements), ZERO)
        if entry.opening_cash + moved != entry.closing_cash:
            raise RuleViolationError(
                "statement_does_not_add_up",
                f"{account.code}, {entry.day:%d.%m.%Y}: остаток на начало "
                f"{_rubles(entry.opening_cash)} и движения {_rubles(moved)} дают "
                f"{_rubles(entry.opening_cash + moved)}, а остаток на конец — "
                f"{_rubles(entry.closing_cash)}. Не хватает движений на "
                f"{_rubles(entry.closing_cash - entry.opening_cash - moved)}.",
            )
        repeated = sorted(
            n for n, k in Counter(t.external_id for t in entry.trades).items() if k > 1
        )
        if repeated:
            raise RuleViolationError(
                "repeated_trade", f"В выписке несколько сделок с номером {repeated[0]}."
            )
        trades = [await self._trade(t) for t in entry.trades]
        movements = [await self._movement(m) for m in entry.movements]
        return {
            "day": entry.day,
            "opening_cash": entry.opening_cash,
            "closing_cash": entry.closing_cash,
            "trades": trades,
            "movements": movements,
        }

    async def _trade(self, entry: TradeEntry) -> dict[str, Any]:
        if entry.security.upper() in KSU_CODES:
            if entry.kind != "repo":
                raise RuleViolationError(
                    "ksu_not_repo", f"Сделка {entry.external_id}: КСУ бывает только в РЕПО."
                )
            instrument_id = None
        else:
            instrument_id = await self._instrument(entry.security)
        return {
            "external_id": entry.external_id,
            "kind": entry.kind,
            "side": entry.side,
            "code": entry.security,
            "instrument_id": None if instrument_id is None else str(instrument_id),
            "settle_date": entry.settle_date.isoformat(),
            "quantity": entry.quantity,
            "price": str(entry.price),
            "accrued": None if entry.accrued is None else _kopecks(entry.accrued),
            "amount": _kopecks(entry.amount),
            "fee": _kopecks(entry.fee),
            "repo_rate": None if entry.repo_rate is None else str(entry.repo_rate),
            "second_date": None if entry.second_date is None else entry.second_date.isoformat(),
            "second_amount": None if entry.second_amount is None else _kopecks(entry.second_amount),
        }

    async def _movement(self, entry: MovementEntry) -> dict[str, Any]:
        instrument_id = None if entry.security is None else await self._instrument(entry.security)
        return {
            "kind": entry.kind,
            "amount": _kopecks(entry.amount),
            "trade": entry.trade,
            "code": entry.security,
            "instrument_id": None if instrument_id is None else str(instrument_id),
            "comment": entry.comment,
        }

    async def _instrument(self, code: str) -> UUID:
        if self._codes is None:
            self._codes = await self._refdata.codes(self._authorization)
        found = self._codes.get(code.upper())
        if found is None:
            raise RuleViolationError("unknown_security", f"Бумаги {code} нет в справочнике.")
        return found[0]

    async def _store(
        self, batch_id: UUID, account: Account, values: Mapping[str, Any], now: datetime
    ) -> tuple[UUID, Result]:
        current = await self._current(account.id, values["day"])
        if current is not None and _same(current, values):
            return current.id, Result.UNCHANGED
        statement_id = uuid4()
        await self._session.execute(
            insert(statement).values(
                id=statement_id,
                batch_id=batch_id,
                account_id=account.id,
                fund_id=account.fund_id,
                received_by=self._actor.username,
                received_at=now,
                **values,
            )
        )
        result = Result.CREATED if current is None else Result.REPLACED
        await self._outbox.add(
            self._session,
            Event(
                service=SERVICE,
                type="statement.received",
                actor=self._actor,
                entity_type="statement",
                entity_id=str(statement_id),
                data={
                    "account": {"code": account.code},
                    "date": values["day"].isoformat(),
                    "before": None if current is None else _journal(dict(current._mapping)),
                    "after": _journal(values),
                },
            ),
        )
        return statement_id, result

    async def _current(self, account_id: UUID, day: date) -> Row[Any] | None:
        result = await self._session.execute(
            select(statement)
            .where(statement.c.account_id == account_id, statement.c.day == day)
            .order_by(statement.c.seq.desc())
            .limit(1)
        )
        return result.one_or_none()

    # --- история --------------------------------------------------------------

    async def history(self, account_id: UUID, day: date) -> list[Row[Any]]:
        """Все выписки по счёту за день, от последней к первой; первая — действующая."""
        await self._account(account_id)
        async with self._session.begin():
            result = await self._session.execute(
                select(statement, statement_batch.c.source)
                .join(statement_batch, statement_batch.c.id == statement.c.batch_id)
                .where(statement.c.account_id == account_id, statement.c.day == day)
                .order_by(statement.c.seq.desc())
            )
            return list(result)

    async def batch(self, batch_id: UUID) -> Row[Any]:
        """Пакет как пришёл — если в нём есть выписка по видимому счёту."""
        accounts = await self._portfolios.accounts(self._authorization)
        async with self._session.begin():
            found = (
                await self._session.execute(
                    select(statement_batch).where(statement_batch.c.id == batch_id)
                )
            ).one_or_none()
            seen = (
                await self._session.execute(
                    select(statement.c.account_id).where(statement.c.batch_id == batch_id)
                )
            ).scalars()
            if found is None or not set(seen) & accounts.keys():
                raise NotFoundError("batch", batch_id)
            return found

    # --- сверка ---------------------------------------------------------------

    async def fund_day(self, fund_id: UUID, day: date) -> FundDay:
        """Сверка фонда за день по всем его счетам."""
        portfolios = await self._portfolios.portfolios(self._authorization)
        fund = portfolios.get(fund_id)
        if fund is None or not fund.is_fund:
            raise NotFoundError("fund", fund_id)
        accounts = await self._portfolios.accounts(self._authorization)
        used = {
            p.account_id
            for p in portfolios.values()
            if p.kind == "physical" and p.fund_id == fund_id and p.account_id is not None
        }
        # Счета видимых physical сервис портфелей отдаёт всегда.
        mine = sorted((accounts[a] for a in used), key=lambda a: a.code)
        closing = await self._booking.closing(fund_id, self._authorization)
        found = [await self._reconcile(a, day, closing.closed_through) for a in mine]
        return FundDay(fund, day, found, closing)

    async def close(self, fund_id: UUID, day: date) -> FundDay:
        """Закрыть день фонда: все счета сверены (решение 48). Точка отсчёта — что ждёт
        пары на конец дня — пишется до закрытия: не закроется день — она не действует."""
        found = await self.fund_day(fund_id, day)
        if found.closed:
            raise RuleViolationError("day_closed", f"{day:%d.%m.%Y} уже закрыт.")
        if not found.reconciled:
            pending = "; ".join(
                f"{a.account.code} — {ACCOUNT_STATUSES[a.status]}"
                for a in found.accounts
                if a.status not in {"reconciled", "non_trading"}
            )
            raise RuleViolationError(
                "not_reconciled",
                f"Закрыть {day:%d.%m.%Y} нельзя: не все счета фонда сверены. {pending}.",
            )
        now = self._clock()
        async with self._session.begin():
            for a in found.accounts:
                await self._session.execute(
                    insert(checkpoint).values(
                        account_id=a.account.id,
                        fund_id=fund_id,
                        day=day,
                        waiting=[_item_json(w) for w in a.day.waiting],
                        created_at=now,
                    )
                )
        # Отказ сервиса сделок — по порядку дней, пересчёту — уходит пользователю как есть.
        await self._booking.close(fund_id, day, found.fund.code, self._actor)
        return await self.fund_day(fund_id, day)

    async def accept(
        self, account_id: UUID, day: date, line: str, comment: str
    ) -> AccountReconciliation:
        """Принять строку сверки, которая не сошлась (решение 123). Закрытый день
        не меняется: сначала его открывает администратор."""
        account = await self._account(account_id)
        closing = await self._booking.closing(account.fund_id, self._authorization)
        if closing.closed_through is not None and day <= closing.closed_through:
            raise RuleViolationError(
                "day_closed",
                f"{day:%d.%m.%Y} закрыт: принять расхождение можно после повторного открытия "
                "дня — его открывает администратор.",
            )
        found = await self._reconcile(account, day, closing.closed_through)
        target = found.day.lines.get(line)
        if target is None:
            raise RuleViolationError(
                "line_not_found", "Такой строки в сверке счёта за этот день нет.", 404
            )
        if target.status in RESOLVED:
            raise RuleViolationError(
                "nothing_to_accept", "Строка сходится или уже принята — принимать нечего."
            )
        acceptance_id = uuid4()
        async with self._session.begin():
            await self._session.execute(
                insert(acceptance).values(
                    id=acceptance_id,
                    account_id=account.id,
                    fund_id=account.fund_id,
                    day=day,
                    line=line,
                    signature=target.signature(),
                    comment=comment,
                    accepted_by=self._actor.username,
                    accepted_at=self._clock(),
                )
            )
            await self._outbox.add(
                self._session,
                Event(
                    service=SERVICE,
                    type="discrepancy.accepted",
                    actor=self._actor,
                    entity_type="acceptance",
                    entity_id=str(acceptance_id),
                    data={
                        "account": {"code": account.code},
                        "date": day.isoformat(),
                        "before": None,
                        "after": {"line": line, **target.signature()},
                    },
                    comment=comment,
                ),
            )
        return await self._reconcile(account, day, closing.closed_through)

    async def _account(self, account_id: UUID) -> Account:
        accounts = await self._portfolios.accounts(self._authorization)
        found = accounts.get(account_id)
        if found is None:
            raise NotFoundError("account", account_id)
        return found

    async def _reconcile(
        self, account: Account, day: date, closed_through: date | None
    ) -> AccountReconciliation:
        """Сверка счёта за день — подряд с точки отсчёта на последнем закрытом дне до дня;
        точки нет — с самого раннего дня с принятыми расхождениями."""
        async with self._session.begin():
            point = await self._checkpoint(account.id, day, closed_through)
            if point is not None:
                first, carried = point.day + ONE_DAY, [_item(w) for w in point.waiting]
            else:
                earliest = (
                    await self._session.execute(
                        select(func.min(acceptance.c.day)).where(
                            acceptance.c.account_id == account.id, acceptance.c.day < day
                        )
                    )
                ).scalar_one_or_none()
                first, carried = (day if earliest is None else earliest), []
            accepted = await self._acceptances(account.id, first, day)
            statements = await self._statements(account.id, first, day)
            previous = await self._previous(account.id, day)
        found = await self._booking.days(account.id, first, day, self._authorization)
        days = reconcile(found.days, statements, accepted, carried)
        return AccountReconciliation(
            account=account,
            portfolios=found.portfolios,
            waiting_since=found.waiting_since,
            day=days[-1],
            previous=previous,
        )

    async def _checkpoint(
        self, account_id: UUID, day: date, closed_through: date | None
    ) -> Row[Any] | None:
        """Последняя точка отсчёта до дня на закрытом дне. Точки за дни, которые потом
        открыли заново, не действуют: они позже границы."""
        if closed_through is None:
            return None
        result = await self._session.execute(
            select(checkpoint)
            .where(
                checkpoint.c.account_id == account_id,
                checkpoint.c.day < day,
                checkpoint.c.day <= closed_through,
            )
            .order_by(checkpoint.c.day.desc(), checkpoint.c.seq.desc())
            .limit(1)
        )
        return result.one_or_none()

    async def _acceptances(self, account_id: UUID, first: date, last: date) -> list[Acceptance]:
        result = await self._session.execute(
            select(acceptance)
            .where(
                acceptance.c.account_id == account_id,
                acceptance.c.day >= first,
                acceptance.c.day <= last,
            )
            .order_by(acceptance.c.seq)
        )
        return [
            Acceptance(
                id=r.id,
                day=r.day,
                line=r.line,
                signature=r.signature,
                comment=r.comment,
                accepted_by=r.accepted_by,
                accepted_at=r.accepted_at,
            )
            for r in result
        ]

    async def _statements(self, account_id: UUID, first: date, last: date) -> dict[date, Statement]:
        """Действующие выписки по дням: последняя пришедшая за день."""
        result = await self._session.execute(
            select(statement, statement_batch.c.source)
            .join(statement_batch, statement_batch.c.id == statement.c.batch_id)
            .where(
                statement.c.account_id == account_id,
                statement.c.day >= first,
                statement.c.day <= last,
            )
            .order_by(statement.c.seq)
        )
        return {row.day: parse(row) for row in result}

    async def _previous(self, account_id: UUID, day: date) -> Statement | None:
        result = await self._session.execute(
            select(statement, statement_batch.c.source)
            .join(statement_batch, statement_batch.c.id == statement.c.batch_id)
            .where(statement.c.account_id == account_id, statement.c.day < day)
            .order_by(statement.c.day.desc(), statement.c.seq.desc())
            .limit(1)
        )
        row = result.one_or_none()
        return None if row is None else parse(row)


def parse(row: Any) -> Statement:
    """Выписка из БД — для сверки."""
    return Statement(
        id=row.id,
        account_id=row.account_id,
        day=row.day,
        opening=row.opening_cash,
        closing=row.closing_cash,
        trades=tuple(
            StatementTrade(
                external_id=t["external_id"],
                kind=t["kind"],
                side=t["side"],
                code=t["code"],
                instrument_id=None if t["instrument_id"] is None else UUID(t["instrument_id"]),
                settle_date=date.fromisoformat(t["settle_date"]),
                quantity=t["quantity"],
                price=Decimal(t["price"]),
                accrued=None if t["accrued"] is None else Decimal(t["accrued"]),
                amount=Decimal(t["amount"]),
                fee=Decimal(t["fee"]),
                repo_rate=None if t["repo_rate"] is None else Decimal(t["repo_rate"]),
                second_date=None
                if t["second_date"] is None
                else date.fromisoformat(t["second_date"]),
                second_amount=None if t["second_amount"] is None else Decimal(t["second_amount"]),
            )
            for t in row.trades
        ),
        movements=tuple(
            StatementMovement(
                kind=m["kind"],
                amount=Decimal(m["amount"]),
                trade=m["trade"],
                code=m["code"],
                instrument_id=None if m["instrument_id"] is None else UUID(m["instrument_id"]),
                comment=m["comment"],
            )
            for m in row.movements
        ),
        source=row.source,
        received_by=row.received_by,
        received_at=row.received_at,
    )


def _item_json(item: OpenItem) -> dict[str, Any]:
    """Принятое расхождение, которое ждёт пары, — для точки отсчёта."""
    return {
        "day": item.day.isoformat(),
        "kind": item.key.kind,
        "trade": item.key.trade,
        "instrument_id": None if item.key.instrument_id is None else str(item.key.instrument_id),
        "discrepancy": str(item.discrepancy),
        "comment": item.comment,
        "secid": item.secid,
    }


def _item(found: Mapping[str, Any]) -> OpenItem:
    instrument = found["instrument_id"]
    return OpenItem(
        day=date.fromisoformat(found["day"]),
        key=MovementKey(
            found["kind"], found["trade"], None if instrument is None else UUID(instrument)
        ),
        discrepancy=Decimal(found["discrepancy"]),
        comment=found["comment"],
        secid=found["secid"],
    )


def _journal(values: Mapping[str, Any]) -> dict[str, Any]:
    """Выписка для журнала: остатки и сколько в ней сделок и движений."""
    return {
        "opening_cash": str(values["opening_cash"]),
        "closing_cash": str(values["closing_cash"]),
        "trades": len(values["trades"]),
        "movements": len(values["movements"]),
    }


def _same(row: Row[Any], values: Mapping[str, Any]) -> bool:
    """Та же выписка: остатки, сделки и движения совпадают."""
    return (
        row.opening_cash == values["opening_cash"]
        and row.closing_cash == values["closing_cash"]
        and _canonical(row.trades) == _canonical(values["trades"])
        and _canonical(row.movements) == _canonical(values["movements"])
    )


def _canonical(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Числа — как числа: «52.60» и «52.6» — одна цена."""
    return [
        {k: Decimal(v) if k in _NUMBERS and v is not None else v for k, v in item.items()}
        for item in items
    ]


_NUMBERS = frozenset({"price", "accrued", "amount", "fee", "repo_rate", "second_amount"})


def _kopecks(value: Decimal) -> str:
    """Сумма до копейки: «-20550» → «-20550.00», как суммы сервиса сделок."""
    return str(value.quantize(KOPECK))


def _rubles(value: Decimal) -> str:
    """Сумма, как её пишет интерфейс: 60 000 000,00 ₽, минус — знаком минуса."""
    text = f"{value:,.2f}".replace(",", " ").replace(".", ",").replace("-", "−")
    return f"{text} ₽"
