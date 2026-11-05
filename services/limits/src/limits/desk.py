"""Наборы лимитов фонда и использование лимитов за день (design/09, часть 9.2).

Набор сохраняется новой версией с датой начала действия: прежние остаются
в истории. Ссылки набора — на классификаторы, их значения, эмитентов и бумаги
справочника — проверяются при сохранении: опечатка в наборе не должна молча
превращать лимит в «соблюдён».

Использование считается при чтении: позиции, состав СЧА и сделки дня — у сервиса
сделок, признаки бумаг — у справочника. Сохранять результат незачем: правка
сделки или поздняя цена пересчитают фонд, и сохранённое стало бы неправдой.

Сохраняются нарушения (часть 9.3): чтение прошедшего дня проверяет его и пропущенные
дни до него и пишет журнал — limits.journal.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Row, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from am_common.events import Actor, Event
from am_common.outbox import Outbox
from limits.clients import BookingClient, Fund, PortfolioClient, RefdataClient
from limits.db import limit_set, outbox
from limits.engine import Attributes, Usage, evaluate
from limits.errors import NotFoundError, RuleViolationError
from limits.journal import LOCK_BITS, OVERDUE, BreachRecord, Journal, month_bounds, overlaps
from limits.model import Condition, EligibleCheck, PurchaseCheck, SetBody, ShareCheck

SERVICE = "limits"
GAP_DAYS = 31  # сколько пропущенных дней до прочитанного проверяет чтение

__all__ = ["Desk", "NotFoundError", "RuleViolationError"]


@dataclass(frozen=True, slots=True)
class SetRecord:
    id: UUID
    fund_id: UUID
    version: int
    valid_from: date
    body: SetBody
    comment: str
    author: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DayView:
    fund: Fund
    day: date
    today: bool  # сегодня — по текущим ценам, как СЧА
    limit_set: SetRecord | None  # набор, действующий на день; нет — проверять нечем
    usage: Usage | None  # None — расчёта фонда за день ещё нет
    open_breaches: int = 0  # открытые нарушения фонда в журнале, с просроченными
    overdue_breaches: int = 0


@dataclass(frozen=True, slots=True)
class BreachView:
    fund: Fund
    record: BreachRecord
    state: str  # open | overdue | eliminated


@dataclass(frozen=True, slots=True)
class ReportView:
    month: str
    view: DayView  # использование на конец месяца — или на вчера, если месяц идёт
    breaches: list[BreachView] = field(default_factory=list)


class Desk:
    def __init__(
        self,
        session: AsyncSession,
        clock: Callable[[], datetime],
        *,
        portfolio: PortfolioClient,
        booking: BookingClient,
        refdata: RefdataClient,
    ) -> None:
        self._session = session
        self._clock = clock
        self._portfolio = portfolio
        self._booking = booking
        self._refdata = refdata
        self._outbox = Outbox(outbox)
        self._journal = Journal(session, clock, refdata)

    async def funds(self, authorization: str) -> list[Fund]:
        found = await self._portfolio.funds(authorization)
        return sorted(found.values(), key=lambda f: f.code)

    async def sets(self, fund_id: UUID, authorization: str) -> list[SetRecord]:
        await self._fund(fund_id, authorization)
        return await self._sets(fund_id)

    async def save(
        self,
        fund_id: UUID,
        body: SetBody,
        *,
        valid_from: date,
        comment: str,
        actor: Actor,
        authorization: str,
    ) -> SetRecord:
        """Новая версия набора с даты: не раньше действующей, ссылки — на справочник."""
        fund = await self._fund(fund_id, authorization)
        attributes = await self._refdata.attributes(valid_from, authorization)
        agencies = await self._refdata.agencies(authorization)
        _check_references(body, attributes, agencies)
        async with self._session.begin():
            # Версии нумеруются по порядку — сохранения одного фонда идут по очереди.
            await self._session.execute(select(func.pg_advisory_xact_lock(fund_id.int & LOCK_BITS)))
            history = await self._sets(fund_id)
            latest = history[-1] if history else None
            if latest is not None and valid_from < latest.valid_from:
                raise RuleViolationError(
                    "valid_from_too_early",
                    f"Версия {latest.version} действует с {latest.valid_from:%d.%m.%Y}: "
                    "новая версия — не раньше.",
                )
            record = SetRecord(
                id=uuid4(),
                fund_id=fund_id,
                version=1 if latest is None else latest.version + 1,
                valid_from=valid_from,
                body=body,
                comment=comment,
                author=actor.username,
                created_at=self._clock(),
            )
            await self._session.execute(
                insert(limit_set).values(
                    id=record.id,
                    fund_id=fund_id,
                    version=record.version,
                    valid_from=valid_from,
                    body=body.model_dump(mode="json"),
                    comment=comment,
                    author=record.author,
                    created_at=record.created_at,
                )
            )
            await self._outbox.add(
                self._session,
                Event(
                    service=SERVICE,
                    type="limit_set.saved",
                    actor=actor,
                    entity_type="limit_set",
                    entity_id=str(fund_id),
                    data={
                        "fund": {"code": fund.code},
                        "before": None if latest is None else _journal(latest),
                        "after": _journal(record),
                    },
                    comment=comment,
                ),
            )
        return record

    async def day(self, fund_id: UUID, day: date, authorization: str) -> DayView:
        """Использование лимитов за день по набору, действующему на этот день.

        Прошедший день ещё и проверяется в журнал — вместе с пропущенными днями до него.
        """
        fund = await self._fund(fund_id, authorization)
        today = self._journal.today()
        history = await self._sets(fund_id)
        await self._session.commit()
        if day < today:
            await self._check_gap(fund, day, history, authorization)
        record = _set_on(history, day)
        usage = None
        if record is not None:
            found = await self._evaluate(fund, day, record, authorization)
            if found is not None:
                usage = found[0]
                if day < today:
                    await self._record(fund, day, record, found, authorization)
        states = [b.state(today) for b in await self._journal.breaches(fund_id, open_only=True)]
        await self._session.commit()
        return DayView(
            fund,
            day,
            day == today,
            record,
            usage,
            open_breaches=len(states),
            overdue_breaches=states.count(OVERDUE),
        )

    async def breaches(
        self, fund_id: UUID, states: set[str] | None, authorization: str
    ) -> list[BreachView]:
        """Журнал нарушений фонда; states — отбор: open, overdue, eliminated."""
        fund = await self._fund(fund_id, authorization)
        today = self._journal.today()
        found = [BreachView(fund, b, b.state(today)) for b in await self._journal.breaches(fund_id)]
        await self._session.commit()
        return [b for b in found if states is None or b.state in states]

    async def review(
        self, breach_id: UUID, *, kind: str, comment: str, actor: Actor, authorization: str
    ) -> BreachView:
        """Вид нарушения и комментарий — риск-менеджмент."""
        fund, found, record = await self._breach(breach_id, authorization)
        updated = await self._journal.review(
            found,
            fund,
            kind=kind,
            comment=comment,
            deadlines=record.body.settings.deadlines,
            actor=actor,
            authorization=authorization,
        )
        return BreachView(fund, updated, updated.state(self._journal.today()))

    async def extend(
        self,
        breach_id: UUID,
        *,
        deadline: date,
        decision: str,
        comment: str,
        actor: Actor,
        authorization: str,
    ) -> BreachView:
        """Продление срока пассивного нарушения по решению генерального директора."""
        fund, found, record = await self._breach(breach_id, authorization)
        updated = await self._journal.extend(
            found,
            fund,
            deadline=deadline,
            decision=decision,
            comment=comment,
            months=record.body.settings.deadlines.extension_months,
            actor=actor,
        )
        return BreachView(fund, updated, updated.state(self._journal.today()))

    async def report(self, fund_id: UUID, month: str, authorization: str) -> ReportView:
        """Отчёт за месяц (п. 8.8): использование на конец месяца и нарушения месяца."""
        first, last = month_bounds(month)
        yesterday = self._journal.today() - timedelta(days=1)
        if first > yesterday:
            raise RuleViolationError(
                "month_not_started", "За этот месяц ещё нет ни одного прошедшего дня."
            )
        view = await self.day(fund_id, min(last, yesterday), authorization)
        found = await self.breaches(fund_id, None, authorization)
        return ReportView(month, view, [b for b in found if overlaps(b.record, first, last)])

    async def _check_gap(
        self, fund: Fund, day: date, history: list[SetRecord], authorization: str
    ) -> None:
        """Пропущенные дни до прочитанного: нарушение не ждёт, пока день откроют."""
        if not history:
            return
        last = await self._journal.last_checked(fund.id)
        await self._session.commit()
        start = history[0].valid_from if last is None else last + timedelta(days=1)
        current = max(start, day - timedelta(days=GAP_DAYS))
        while current < day:
            # Проверки идут с первой версии набора — версия на день есть всегда.
            record = _set_on(history, current) or history[0]
            found = await self._evaluate(fund, current, record, authorization)
            if found is not None:
                await self._record(fund, current, record, found, authorization)
            current += timedelta(days=1)

    async def _evaluate(
        self, fund: Fund, day: date, record: SetRecord, authorization: str
    ) -> tuple[Usage, bool] | None:
        """Расчёт дня и были ли сделки; None — расчёта фонда за день нет."""
        figures = await self._booking.figures(fund.id, day, authorization)
        if figures is None:
            return None
        positions = await self._booking.positions(fund.id, day, authorization)
        deals, traded_any = await self._booking.deals(fund.id, day, authorization)
        attributes = await self._refdata.attributes(day, authorization)
        return evaluate(record.body, figures, positions, deals, attributes), traded_any

    async def _record(
        self,
        fund: Fund,
        day: date,
        record: SetRecord,
        found: tuple[Usage, bool],
        authorization: str,
    ) -> None:
        usage, traded_any = found
        await self._journal.record(
            fund,
            day,
            version=record.version,
            usage=usage,
            traded_any=traded_any,
            deadlines=record.body.settings.deadlines,
            authorization=authorization,
        )

    async def _breach(
        self, breach_id: UUID, authorization: str
    ) -> tuple[Fund, BreachRecord, SetRecord]:
        """Нарушение, его фонд — если пользователь его видит — и набор дня нарушения."""
        found = await self._journal.get(breach_id)
        history = await self._sets(found.fund_id)
        await self._session.commit()
        fund = await self._fund(found.fund_id, authorization)
        # Нарушение записано по набору — набор на день нарушения есть всегда.
        return fund, found, _set_on(history, found.first_day) or history[0]

    async def _fund(self, fund_id: UUID, authorization: str) -> Fund:
        found = (await self._portfolio.funds(authorization)).get(fund_id)
        if found is None:
            raise NotFoundError("fund")
        return found

    async def _sets(self, fund_id: UUID) -> list[SetRecord]:
        query = (
            select(limit_set).where(limit_set.c.fund_id == fund_id).order_by(limit_set.c.version)
        )
        return [_record(row) for row in await self._session.execute(query)]


def _set_on(history: list[SetRecord], day: date) -> SetRecord | None:
    """Версия набора, действующая на день."""
    return next((s for s in reversed(history) if s.valid_from <= day), None)


def _check_references(body: SetBody, attributes: Attributes, agencies: set[str]) -> None:
    """Набор ссылается только на то, что есть в справочнике, и без повторов кодов."""
    codes = [limit.code for limit in body.limits]
    if len(set(codes)) != len(codes):
        raise RuleViolationError("duplicate_code", "Коды лимитов в наборе повторяются.")
    unknown = sorted(set(body.settings.ratings.agencies) - agencies)
    if unknown:
        raise RuleViolationError(
            "unknown_agency", f"Агентств {', '.join(unknown)} нет в справочнике."
        )
    grades = set(attributes.grades)
    wrong = sorted(set(body.settings.ratings.groups) - grades)
    if wrong:
        raise RuleViolationError(
            "unknown_grade", f"Ступеней {', '.join(wrong)} нет в общей шкале рейтингов."
        )
    for rule in body.settings.ratings.rules:
        if any(c.attribute == "rating_group" for c in rule.filter):
            raise RuleViolationError(
                "rating_group_in_rating_rule",
                "Правило рейтинга не может зависеть от рейтинговой группы — она из него и следует.",
            )
    for condition in _conditions(body):
        _check_condition(condition, attributes, body)
    for check in (c for limit in body.limits for c in limit.checks):
        if isinstance(check, ShareCheck):
            for issuer_id in check.exclude_issuers:
                if issuer_id not in attributes.issuers:
                    raise RuleViolationError(
                        "unknown_issuer", f"Эмитента {issuer_id} нет в справочнике."
                    )


def _check_condition(condition: Condition, attributes: Attributes, body: SetBody) -> None:
    attribute, values = condition.attribute, condition.values
    if attribute.startswith("class:"):
        classifier = attributes.classifiers.get(UUID(attribute[6:]))
        if classifier is None:
            raise RuleViolationError(
                "unknown_classifier", f"Классификатора {attribute[6:]} нет в справочнике."
            )
        known = {str(v) for v in classifier.values}
        if not set(values) <= known:
            raise RuleViolationError(
                "unknown_value", f"У классификатора «{classifier.name}» нет таких значений."
            )
    elif attribute == "issuer" and not {str(i) for i in attributes.issuers} >= set(values):
        raise RuleViolationError(
            "unknown_issuer", "В фильтре — эмитент, которого нет в справочнике."
        )
    elif attribute == "instrument" and not {str(i) for i in attributes.papers} >= set(values):
        raise RuleViolationError(
            "unknown_instrument", "В фильтре — бумага, которой нет в справочнике."
        )
    elif attribute == "rating_group":
        groups = set(body.settings.ratings.groups.values()) | {"none"}
        if not set(values) <= groups:
            raise RuleViolationError(
                "unknown_group", "В фильтре — рейтинговая группа, которой нет в настройках набора."
            )


def _conditions(body: SetBody) -> Iterator[Condition]:
    for rating_rule in body.settings.ratings.rules:
        yield from rating_rule.filter
    for check in (c for limit in body.limits for c in limit.checks):
        if isinstance(check, ShareCheck):
            yield from check.filter
        elif isinstance(check, EligibleCheck):
            for rule in check.rules:
                yield from rule.filter
        elif isinstance(check, PurchaseCheck):
            yield from check.filter
            yield from check.condition


def _journal(record: SetRecord) -> dict[str, Any]:
    document = record.body.document
    return {
        "version": record.version,
        "valid_from": record.valid_from.isoformat(),
        "document": " ".join(filter(None, [document.name, document.version])),
        "limits": ", ".join(limit.code for limit in record.body.limits),
    }


def _record(row: Row[Any]) -> SetRecord:
    return SetRecord(
        id=row.id,
        fund_id=row.fund_id,
        version=row.version,
        valid_from=row.valid_from,
        body=SetBody.model_validate(row.body),
        comment=row.comment,
        author=row.author,
        created_at=row.created_at,
    )
