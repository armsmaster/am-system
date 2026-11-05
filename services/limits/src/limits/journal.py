"""Журнал нарушений лимитов (design/09, часть 9.3; пп. 8.3–8.6 политики, решения 138–140).

Нарушение записывается по данным конца дня: сегодняшние цены ещё поменяются.
Нарушение — проверка набора и её группа: эмитент, выпуск, бумага; у показателя
фонда группа одна. Открыто, пока проверка следующего дня не покажет «в пределах».

Вид система предлагает по правилу «без сделок дня лимит был бы соблюдён — активное»,
риск-менеджмент подтверждает или меняет. Срок — в торговых днях календаря справочника
от даты выявления: активное — 1 день, пассивное показателя фонда — 2, остальное
пассивное — 20; числа — параметры набора.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Row, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from am_common.clock import MOSCOW
from am_common.events import SYSTEM_ACTOR, Actor, Event
from am_common.outbox import Outbox
from limits.clients import Fund, RefdataClient
from limits.db import breach, day_check, outbox
from limits.engine import Problem, Status, Usage
from limits.errors import NotFoundError, RuleViolationError
from limits.model import Deadlines

ACTIVE, PASSIVE = "active", "passive"
OPEN, OVERDUE, ELIMINATED = "open", "overdue", "eliminated"
SERVICE = "limits"
LOCK_BITS = (1 << 63) - 1  # ключ advisory-блокировки — 64 бита со знаком
SIX = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class Finding:
    """Нарушение в проверке одного дня."""

    code: str
    label: str
    check_type: str
    key: str
    name: str
    reason: str | None
    value: Decimal | None
    maximum: Decimal | None
    minimum: Decimal | None
    traded: bool  # сделки дня затронули бумаги этой группы
    kind: str  # предложение системы


@dataclass(frozen=True, slots=True)
class BreachRecord:
    id: UUID
    fund_id: UUID
    limit_code: str
    label: str
    check_type: str
    key: str
    name: str
    reason: str | None
    first_day: date
    last_day: date
    detected_on: date
    value: Decimal | None
    peak: Decimal | None
    maximum: Decimal | None
    minimum: Decimal | None
    suggested: str
    kind: str
    kind_by: str | None
    deadline: date
    status: str
    eliminated_on: date | None
    notes: list[dict[str, Any]]
    extensions: list[dict[str, Any]]
    created_at: datetime

    def state(self, today: date) -> str:
        """Открыто, просрочено — открыто и срок прошёл, устранено."""
        return OVERDUE if self.status == OPEN and today > self.deadline else self.status


FIELDS = [f.name for f in fields(BreachRecord)]


def findings(usage: Usage, traded_any: bool) -> list[Finding]:
    """Нарушения проверок дня — по группам; вид — по вкладу сделок дня (решение 139)."""
    found: list[Finding] = []
    for check in usage.checks:
        if check.status is not Status.BREACH:
            continue
        common: dict[str, Any] = {
            "code": check.code,
            "label": check.label or "",
            "check_type": check.type,
            "maximum": check.maximum,
            "minimum": check.minimum,
        }
        if check.type == "metric":
            # Плечо и дюрацию меняют любые сделки и РЕПО.
            found.append(
                Finding(
                    **common,
                    key="value",
                    name="фонд",
                    reason=None,
                    value=_six(check.value),
                    traded=traded_any,
                    kind=ACTIVE if traded_any else PASSIVE,
                )
            )
        elif check.type == "share":
            maximum = check.maximum or Decimal(0)
            found += [
                Finding(
                    **common,
                    key=g.key,
                    name=g.name,
                    reason=None,
                    value=_six(g.value),
                    traded=g.traded > 0,
                    # Без сделок дня доля была бы в пределах — нарушение создала сделка.
                    kind=ACTIVE if g.traded > 0 and g.value - g.traded <= maximum else PASSIVE,
                )
                for g in check.groups
                if g.status is Status.BREACH
            ]
        else:
            papers: dict[str, list[Problem]] = {}
            for problem in check.violations:
                papers.setdefault(problem.secid, []).append(problem)
            found += [
                Finding(
                    **common,
                    key=secid,
                    name=secid,
                    reason="; ".join(p.reason for p in problems),
                    value=None,
                    traded=any(p.by_deal for p in problems),
                    kind=ACTIVE if any(p.by_deal for p in problems) else PASSIVE,
                )
                for secid, problems in papers.items()
            ]
    return found


class Journal:
    def __init__(
        self, session: AsyncSession, clock: Callable[[], datetime], refdata: RefdataClient
    ) -> None:
        self._session = session
        self._clock = clock
        self._refdata = refdata
        self._outbox = Outbox(outbox)

    def today(self) -> date:
        return self._clock().astimezone(MOSCOW).date()

    async def last_checked(self, fund_id: UUID) -> date | None:
        found: date | None = (
            await self._session.execute(
                select(func.max(day_check.c.day)).where(day_check.c.fund_id == fund_id)
            )
        ).scalar_one()
        return found

    async def record(
        self,
        fund: Fund,
        day: date,
        *,
        version: int,
        usage: Usage,
        traded_any: bool,
        deadlines: Deadlines,
        authorization: str,
    ) -> None:
        """Проверка прошедшего дня: новые нарушения — в журнал, исчезнувшие — устранены."""
        today = self.today()
        found = {(f.code, f.label, f.key): f for f in findings(usage, traded_any)}
        # Где не хватает данных, отсутствие нарушения ещё не значит «устранено».
        unknown = {(c.code, c.label or "") for c in usage.checks if c.status is Status.NO_DATA}
        checked_before = await self._session.scalar(
            select(day_check.c.day).where(day_check.c.fund_id == fund.id, day_check.c.day == day)
        )
        await self._session.commit()
        # Первая проверка дня до конца следующего торгового дня видит нарушение в срок:
        # оно выявлено днём нарушения. Позже или при повторной проверке — днём записи.
        detected = today
        if found and checked_before is None and today <= await self._shift(day, 1, authorization):
            detected = day
        due = {
            (kind, check_type): await self._shift(
                detected, _days(kind, check_type, deadlines), authorization
            )
            for kind, check_type in {(f.kind, f.check_type) for f in found.values()}
        }
        async with self._session.begin():
            # Проверки одного фонда идут по очереди — иначе нарушение запишется дважды.
            await self._session.execute(select(func.pg_advisory_xact_lock(fund.id.int & LOCK_BITS)))
            for record in await self.breaches(fund.id, open_only=True):
                finding = found.pop((record.limit_code, record.label, record.key), None)
                if finding is not None:
                    await self._seen(record, day, finding)
                elif day > record.last_day and (record.limit_code, record.label) not in unknown:
                    await self._eliminate(fund, record, day)
            for finding in found.values():
                deadline = due[finding.kind, finding.check_type]
                await self._create(fund, day, detected, finding, deadline)
            now = self._clock()
            await self._session.execute(
                insert(day_check)
                .values(fund_id=fund.id, day=day, set_version=version, checked_at=now)
                .on_conflict_do_update(
                    index_elements=[day_check.c.fund_id, day_check.c.day],
                    set_={"set_version": version, "checked_at": now},
                )
            )

    async def breaches(self, fund_id: UUID, *, open_only: bool = False) -> list[BreachRecord]:
        query = select(breach).where(breach.c.fund_id == fund_id)
        if open_only:
            query = query.where(breach.c.status == OPEN)
        query = query.order_by(
            breach.c.first_day.desc(), breach.c.limit_code, breach.c.label, breach.c.name
        )
        return [_record(row) for row in await self._session.execute(query)]

    async def get(self, breach_id: UUID) -> BreachRecord:
        row = (
            await self._session.execute(select(breach).where(breach.c.id == breach_id))
        ).one_or_none()
        if row is None:
            raise NotFoundError("breach")
        return _record(row)

    async def review(
        self,
        found: BreachRecord,
        fund: Fund,
        *,
        kind: str,
        comment: str,
        deadlines: Deadlines,
        actor: Actor,
        authorization: str,
    ) -> BreachRecord:
        """Риск-менеджмент подтверждает или меняет вид; срок — по подтверждённому виду.

        Продлённый срок вид не меняет: его назначил генеральный директор.
        """
        deadline = found.deadline
        if kind != found.kind and not found.extensions:
            days = _days(kind, found.check_type, deadlines)
            deadline = await self._shift(found.detected_on, days, authorization)
        await self._session.commit()
        async with self._session.begin():
            await self._session.execute(
                update(breach)
                .where(breach.c.id == found.id)
                .values(
                    kind=kind,
                    kind_by=actor.username,
                    deadline=deadline,
                    notes=[*found.notes, self._note(actor.username, comment, kind=kind)],
                )
            )
            await self._emit(
                "breach.reviewed",
                actor,
                fund,
                found,
                before={"breach_kind": found.kind, "deadline": found.deadline.isoformat()},
                after={"breach_kind": kind, "deadline": deadline.isoformat()},
                comment=comment,
            )
        return await self.get(found.id)

    async def extend(
        self,
        found: BreachRecord,
        fund: Fund,
        *,
        deadline: date,
        decision: str,
        comment: str,
        months: int,
        actor: Actor,
    ) -> BreachRecord:
        """Продление срока пассивного нарушения решением генерального директора (п. 8.5)."""
        if found.status != OPEN:
            raise RuleViolationError(
                "breach_eliminated", "Нарушение устранено — продлевать нечего."
            )
        if found.kind != PASSIVE:
            raise RuleViolationError(
                "active_not_extended", "Продлить можно срок только пассивного нарушения (п. 8.5)."
            )
        limit = add_months(found.detected_on, months)
        if not found.deadline < deadline <= limit:
            raise RuleViolationError(
                "bad_deadline",
                f"Новый срок — позже {found.deadline:%d.%m.%Y} и не позже {limit:%d.%m.%Y}: "
                f"продление — до {months} мес. с даты выявления.",
            )
        extension = {
            "deadline": deadline.isoformat(),
            "decision": decision,
            "comment": comment,
            "by": actor.username,
            "at": self._clock().isoformat(),
        }
        await self._session.commit()
        async with self._session.begin():
            await self._session.execute(
                update(breach)
                .where(breach.c.id == found.id)
                .values(deadline=deadline, extensions=[*found.extensions, extension])
            )
            await self._emit(
                "breach.extended",
                actor,
                fund,
                found,
                before={"deadline": found.deadline.isoformat()},
                after={"deadline": deadline.isoformat(), "decision": decision},
                comment=comment,
            )
        return await self.get(found.id)

    # --- внутреннее -------------------------------------------------------------

    async def _shift(self, day: date, days: int, authorization: str) -> date:
        return await self._refdata.add_trading_days(day, days, authorization)

    async def _create(
        self, fund: Fund, day: date, detected: date, finding: Finding, deadline: date
    ) -> None:
        record = BreachRecord(
            id=uuid4(),
            fund_id=fund.id,
            limit_code=finding.code,
            label=finding.label,
            check_type=finding.check_type,
            key=finding.key,
            name=finding.name,
            reason=finding.reason,
            first_day=day,
            last_day=day,
            detected_on=detected,
            value=finding.value,
            peak=finding.value,
            maximum=finding.maximum,
            minimum=finding.minimum,
            suggested=finding.kind,
            kind=finding.kind,
            kind_by=None,
            deadline=deadline,
            status=OPEN,
            eliminated_on=None,
            notes=[],
            extensions=[],
            created_at=self._clock(),
        )
        await self._session.execute(insert(breach).values({f: getattr(record, f) for f in FIELDS}))
        await self._emit(
            "breach.detected",
            SYSTEM_ACTOR,
            fund,
            record,
            before=None,
            after={
                "measured": None if record.value is None else str(record.value),
                "breach_kind": record.kind,
                "deadline": deadline.isoformat(),
            },
            comment=record.reason,
        )

    async def _seen(self, record: BreachRecord, day: date, finding: Finding) -> None:
        values: dict[str, Any] = {}
        if day > record.last_day:
            values = {"last_day": day, "value": finding.value}
            grew = finding.value is None or record.value is None or finding.value > record.value
            if finding.traded and grew:
                # Сделка, которая увеличивает нарушение, запрещена (п. 8.2).
                text = f"нарушение увеличено сделкой {day:%d.%m.%Y}"
                values["notes"] = [*record.notes, self._note(SYSTEM_ACTOR.username, text)]
        if finding.value is not None and (record.peak is None or finding.value > record.peak):
            values["peak"] = finding.value
        if values:
            await self._session.execute(
                update(breach).where(breach.c.id == record.id).values(values)
            )

    async def _eliminate(self, fund: Fund, record: BreachRecord, day: date) -> None:
        await self._session.execute(
            update(breach)
            .where(breach.c.id == record.id)
            .values(status=ELIMINATED, eliminated_on=day)
        )
        await self._emit(
            "breach.eliminated",
            SYSTEM_ACTOR,
            fund,
            record,
            before={"status": OPEN},
            after={"status": ELIMINATED, "eliminated_on": day.isoformat()},
            comment=None,
        )

    def _note(self, by: str, text: str, **extra: str) -> dict[str, Any]:
        return {"by": by, "at": self._clock().isoformat(), "text": text, **extra}

    async def _emit(
        self,
        event_type: str,
        actor: Actor,
        fund: Fund,
        record: BreachRecord,
        *,
        before: dict[str, Any] | None,
        after: dict[str, Any],
        comment: str | None,
    ) -> None:
        await self._outbox.add(
            self._session,
            Event(
                service=SERVICE,
                type=event_type,
                actor=actor,
                entity_type="breach",
                entity_id=str(record.id),
                data={
                    "fund": {"code": fund.code},
                    "breach": {
                        "limit": ", ".join(filter(None, [record.limit_code, record.label])),
                        "name": record.name,
                        "first_day": record.first_day.isoformat(),
                    },
                    "before": before,
                    "after": after,
                },
                comment=comment,
            ),
        )


def month_bounds(month: str) -> tuple[date, date]:
    """«2026-11» → первый и последний день месяца."""
    try:
        year, number = (int(part) for part in month.split("-"))
        first = date(year, number, 1)
    except ValueError as exc:
        raise RuleViolationError("bad_month", "Месяц — в виде ГГГГ-ММ, например 2026-11.") from exc
    return first, date(year, number, calendar.monthrange(year, number)[1])


def overlaps(record: BreachRecord, first: date, last: date) -> bool:
    """Нарушение было открыто хотя бы один день месяца."""
    return record.first_day <= last and (
        record.eliminated_on is None or record.eliminated_on > first
    )


def add_months(day: date, months: int) -> date:
    """Тот же день через N месяцев; нет такого дня — последний день месяца."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _six(value: Decimal | None) -> Decimal | None:
    """Значение — с точностью базы: иначе «выросло» покажет округление."""
    return None if value is None else value.quantize(SIX)


def _days(kind: str, check_type: str, deadlines: Deadlines) -> int:
    if kind == ACTIVE:
        return deadlines.active
    return deadlines.passive_metric if check_type == "metric" else deadlines.passive_other


def _record(row: Row[Any]) -> BreachRecord:
    return BreachRecord(**{f: getattr(row, f) for f in FIELDS})
