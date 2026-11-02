"""Изменения справочника: загрузка бумаги, правка, синхронизация с ISS, календарь,
рейтинги и классификаторы.

Запрос к ISS — до транзакции: сеть бывает медленной, а держать открытой
транзакцию во время сетевого запроса нельзя. Изменение и событие для журнала
записываются одной транзакцией (outbox).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from am_common.clock import moscow_now
from am_common.db import Database
from am_common.events import Actor, Event
from am_common.outbox import Outbox
from refdata import scale
from refdata.attributes import (
    ClassificationRecord,
    ClassifierRecord,
    RatingAction,
    RatingRecord,
    Subject,
    Target,
    ValueRecord,
    classification_on,
    ratings_on,
)
from refdata.db import outbox
from refdata.domain import (
    FIELD_LABELS,
    InstrumentKind,
    RuleViolationError,
    Version,
    apply_edit,
    changed_fields,
    check_new_version_date,
    issue_terms,
    journal_view,
    merge_from_iss,
)
from refdata.iss import IssClient, IssError
from refdata.repository import CalendarDay, InstrumentRecord, NotFoundError, Repository

SERVICE = "refdata"
SYNC_AUTHOR = "moex-iss"


@dataclass(frozen=True, slots=True)
class Loaded:
    instrument_id: UUID
    created: bool  # False — бумага уже была в справочнике


class Commands:
    def __init__(
        self,
        session: AsyncSession,
        actor: Actor,
        *,
        clock: Callable[[], datetime] = moscow_now,
    ) -> None:
        self._session = session
        self._actor = actor
        self._clock = clock
        self._repo = Repository(session)
        self._outbox = Outbox(outbox)

    # --- бумаги -----------------------------------------------------------------

    async def load(self, code: str, iss: IssClient) -> Loaded:
        """Загружает бумагу из ISS по коду биржи или ISIN."""
        existing = await self._repo.find_by_code(code)
        await self._session.commit()
        if existing is not None:
            return Loaded(existing.id, created=False)

        security = await iss.find(code)
        now = self._clock()
        params = issue_terms(security.params, now.date())
        record = InstrumentRecord(
            id=uuid4(),
            secid=security.secid,
            isin=security.isin,
            kind=security.kind,
            name=params.name,
            short_name=params.short_name,
            added_by=self._actor.username,
            added_at=now,
            iss_snapshot=params,
            last_synced_at=now,
            last_sync_error=None,
        )
        async with self._session.begin():
            if not await self._repo.insert(record):
                # Пока шёл запрос к ISS, ту же бумагу загрузил кто-то другой.
                found = await self._repo.find_by_code(security.secid)
                return Loaded(found.id if found else record.id, created=False)
            await self._repo.link_issuer(record.id, security.issuer, security.iss_type)
            await self._repo.add_version(
                record.id,
                Version(
                    number=1,
                    valid_from=None,
                    source="iss",
                    author=self._actor.username,
                    comment="Загружена из MOEX ISS",
                    created_at=now,
                    params=params,
                ),
            )
            await self._emit(
                "instrument.loaded",
                record,
                {"after": journal_view(params)},
            )
        return Loaded(record.id, created=True)

    async def edit(
        self,
        instrument_id: UUID,
        *,
        valid_from: date,
        comment: str,
        changes: Mapping[str, Any],
        unpin: Sequence[str],
    ) -> Version:
        """Ручная правка сопровождением: новая версия с закреплёнными полями."""
        async with self._session.begin():
            record = await self._repo.get(instrument_id, lock=True)
            latest = await self._repo.latest_version(instrument_id)
            check_new_version_date(latest, valid_from)
            params, pinned = apply_edit(latest, changes, unpin, record.iss_snapshot)
            if params == latest.params and pinned == latest.pinned:
                raise RuleViolationError("no_changes", "Изменений нет — новая версия не нужна.")
            version = Version(
                number=latest.number + 1,
                valid_from=valid_from,
                source="manual",
                author=self._actor.username,
                comment=comment,
                created_at=self._clock(),
                pinned=pinned,
                params=params,
            )
            await self._save_version(record, latest, version)
        return version

    async def sync(self, instrument_id: UUID, iss: IssClient) -> bool:
        """Сверяет бумагу с ISS. True — появилась новая версия."""
        async with self._session.begin():
            record = await self._repo.get(instrument_id)
        try:
            security = await iss.find(record.secid)
        except IssError as exc:
            async with self._session.begin():
                await self._repo.set_sync_state(
                    instrument_id, synced_at=None, snapshot=None, error=str(exc)
                )
            raise

        now = self._clock()
        from_iss = issue_terms(security.params, now.date())
        async with self._session.begin():
            record = await self._repo.get(instrument_id, lock=True)
            latest = await self._repo.latest_version(instrument_id)
            merged = merge_from_iss(latest, from_iss)
            changed = changed_fields(latest.params, merged)
            if changed:
                # Строчная — только первая буква: аббревиатуры вроде ISS остаются как есть.
                labels = ", ".join(_lower_first(FIELD_LABELS[name]) for name in changed)
                # ISS не сообщает дату публикации — новая версия действует с дня,
                # когда изменение заметили (решение 13, опоздание не больше суток).
                today = now.date()
                valid_from = max(today, latest.valid_from) if latest.valid_from else today
                version = Version(
                    number=latest.number + 1,
                    valid_from=valid_from,
                    source="iss",
                    author=SYNC_AUTHOR,
                    comment=f"Синхронизация с MOEX ISS: изменено — {labels}",
                    created_at=now,
                    pinned=latest.pinned,
                    params=merged,
                )
                await self._save_version(record, latest, version)
            await self._repo.set_sync_state(
                instrument_id, synced_at=now, snapshot=from_iss, error=None
            )
            # Бумагам, загруженным до эмитентов, эмитент приходит при сверке.
            await self._repo.link_issuer(instrument_id, security.issuer, security.iss_type)
        return bool(changed)

    async def _save_version(
        self, record: InstrumentRecord, latest: Version, version: Version
    ) -> None:
        await self._repo.add_version(record.id, version)
        await self._repo.set_names(record.id, version.params)
        changed = changed_fields(latest.params, version.params)
        await self._emit(
            "instrument.version_added",
            record,
            {
                "version": version.number,
                "valid_from": None
                if version.valid_from is None
                else version.valid_from.isoformat(),
                "source": version.source,
                "before": journal_view(latest.params, changed),
                "after": journal_view(version.params, changed),
                "pinned": sorted(version.pinned),
            },
            comment=version.comment,
        )

    # --- календарь --------------------------------------------------------------

    async def set_calendar_day(self, day: date, is_trading: bool, comment: str | None) -> None:
        async with self._session.begin():
            before = await self._repo.calendar_day(day)
            entry = CalendarDay(day, is_trading, comment)
            await self._repo.set_calendar_day(entry, self._actor.username)
            await self._emit_calendar("calendar.day_set", day, before, entry)

    async def clear_calendar_day(self, day: date) -> None:
        async with self._session.begin():
            before = await self._repo.calendar_day(day)
            if before is None:
                raise RuleViolationError(
                    "no_exception", "Для этой даты нет исключения в календаре."
                )
            await self._repo.clear_calendar_day(day)
            await self._emit_calendar("calendar.day_cleared", day, before, None)

    # --- рейтинги ---------------------------------------------------------------

    async def add_rating(
        self,
        subject: Subject,
        action: RatingAction,
        *,
        agency: str | None,
        rating: str | None,
        valid_from: date,
        comment: str | None,
    ) -> RatingRecord:
        """Рейтинг присвоен или изменён, отозван, или рейтинга нет ни у кого (design/09)."""
        self._not_in_future(valid_from)
        grade: str | None = None
        if action is RatingAction.UNRATED:
            agency = rating = None
        else:
            if agency is None:
                raise RuleViolationError("agency_required", "Укажите агентство.")
            scale.agency(agency)
            if action is RatingAction.ASSIGNED:
                if not rating:
                    raise RuleViolationError("rating_required", "Укажите рейтинг.")
                grade = scale.grade_of(agency, rating)
                rating = scale.notation(agency, grade)
            else:
                rating = None
        async with self._session.begin():
            label, name = await self._subject(subject, lock=True, rated=True)
            current = ratings_on(await self._repo.ratings(subject), valid_from)
            active = {r.agency: r for r in current.ratings}
            if action is RatingAction.WITHDRAWN and agency not in active:
                raise RuleViolationError(
                    "nothing_to_withdraw",
                    f"На {valid_from:%d.%m.%Y} у {name} нет рейтинга "
                    f"{scale.agency(agency or '').name} — отзывать нечего.",
                )
            if action is RatingAction.UNRATED and active:
                listed = ", ".join(
                    f"{scale.agency(r.agency).name} {r.rating}" for r in current.ratings
                )
                raise RuleViolationError(
                    "rated",
                    f"На {valid_from:%d.%m.%Y} у {name} действуют рейтинги: {listed}. "
                    "Сначала отметьте их отзыв.",
                )
            record = RatingRecord(
                id=uuid4(),
                seq=0,
                subject=subject,
                action=action,
                agency=agency,
                rating=rating,
                grade=grade,
                valid_from=valid_from,
                comment=comment,
                author=self._actor.username,
                created_at=self._clock(),
                cancelled_by=None,
                cancelled_at=None,
                cancel_comment=None,
            )
            await self._repo.add_rating(record)
            await self._emit_on(
                "rating.added",
                subject,
                label,
                {"before": None, "after": _rating_view(record)},
                comment,
            )
        return record

    async def cancel_rating(self, rating_id: UUID, comment: str) -> RatingRecord:
        """Ошибочная запись: остаётся в истории, но больше не действует."""
        async with self._session.begin():
            record = await self._repo.get_rating(rating_id)
            if record.cancelled_at is not None:
                raise RuleViolationError("already_cancelled", "Запись уже отменена.")
            label, _name = await self._subject(record.subject, lock=True)
            await self._repo.cancel_rating(rating_id, self._actor.username, self._clock(), comment)
            await self._emit_on(
                "rating.cancelled",
                record.subject,
                label,
                {"before": _rating_view(record), "after": None},
                comment,
            )
        return record

    # --- классификаторы ---------------------------------------------------------

    async def create_classifier(
        self, name: str, target: Target, description: str | None, values: Sequence[str]
    ) -> UUID:
        names = _distinct(values)
        record = ClassifierRecord(
            id=uuid4(),
            name=name,
            target=target,
            description=description,
            created_by=self._actor.username,
            created_at=self._clock(),
            values=tuple(
                ValueRecord(uuid4(), value, position) for position, value in enumerate(names)
            ),
        )
        async with self._session.begin():
            await self._name_is_free(name)
            await self._repo.add_classifier(record)
            await self._emit_classifier(
                "classifier.created",
                record,
                None,
                {
                    "name": name,
                    "target": target.value,
                    "description": description,
                    "values": ", ".join(names),
                },
            )
        return record.id

    async def update_classifier(
        self, classifier_id: UUID, name: str, description: str | None
    ) -> None:
        async with self._session.begin():
            record = await self._repo.get_classifier(classifier_id, lock=True)
            if (record.name, record.description) == (name, description):
                raise RuleViolationError("no_changes", "Изменений нет.")
            if name.lower() != record.name.lower():
                await self._name_is_free(name)
            await self._repo.update_classifier(classifier_id, name, description)
            await self._emit_classifier(
                "classifier.updated",
                record,
                {"name": record.name, "description": record.description},
                {"name": name, "description": description},
            )

    async def add_value(self, classifier_id: UUID, name: str) -> None:
        async with self._session.begin():
            record = await self._repo.get_classifier(classifier_id, lock=True)
            _value_is_free(record, name)
            position = max((v.position for v in record.values), default=-1) + 1
            await self._repo.add_value(classifier_id, ValueRecord(uuid4(), name, position))
            await self._emit_classifier(
                "classifier.value_added", record, None, {"class_value": name}
            )

    async def rename_value(self, classifier_id: UUID, value_id: UUID, name: str) -> None:
        async with self._session.begin():
            record = await self._repo.get_classifier(classifier_id, lock=True)
            value = record.value(value_id)
            if value is None:
                raise NotFoundError("classifier value", value_id)
            if value.name == name:
                raise RuleViolationError("no_changes", "Изменений нет.")
            if value.name.lower() != name.lower():
                _value_is_free(record, name)
            await self._repo.rename_value(value_id, name)
            await self._emit_classifier(
                "classifier.value_renamed",
                record,
                {"class_value": value.name},
                {"class_value": name},
            )

    # --- значения классификаторов -----------------------------------------------

    async def classify(
        self,
        subject: Subject,
        classifier_id: UUID,
        value_id: UUID,
        *,
        valid_from: date | None,
        comment: str | None,
    ) -> ClassificationRecord:
        """Значение классификатора у бумаги или эмитента с даты; без даты — с начала."""
        if valid_from is not None:
            self._not_in_future(valid_from)
        async with self._session.begin():
            found = await self._repo.get_classifier(classifier_id)
            if found.target is not subject.target:
                kind = "эмитентов" if found.target is Target.ISSUER else "бумаг"
                raise RuleViolationError(
                    "wrong_target", f"Классификатор «{found.name}» — для {kind}."
                )
            value = found.value(value_id)
            if value is None:
                raise RuleViolationError(
                    "unknown_value", f"У классификатора «{found.name}» нет такого значения."
                )
            label, _name = await self._subject(subject, lock=True)
            records = await self._repo.classifications(subject)
            before = classification_on(records, valid_from or date.min).get(classifier_id)
            if before is not None and before.value_id == value_id:
                raise RuleViolationError(
                    "no_changes", f"«{found.name}» на эту дату уже «{value.name}»."
                )
            record = ClassificationRecord(
                id=uuid4(),
                seq=0,
                classifier_id=classifier_id,
                subject=subject,
                value_id=value_id,
                valid_from=valid_from,
                comment=comment,
                author=self._actor.username,
                created_at=self._clock(),
                cancelled_by=None,
                cancelled_at=None,
                cancel_comment=None,
            )
            await self._repo.add_classification(record)
            await self._emit_on(
                "classification.set",
                subject,
                {**label, "classifier": {"name": found.name}},
                {
                    "before": None if before is None else _classification_view(found, before),
                    "after": _classification_view(found, record),
                },
                comment,
            )
        return record

    async def cancel_classification(
        self, classification_id: UUID, comment: str
    ) -> ClassificationRecord:
        async with self._session.begin():
            record = await self._repo.get_classification(classification_id)
            if record.cancelled_at is not None:
                raise RuleViolationError("already_cancelled", "Запись уже отменена.")
            found = await self._repo.get_classifier(record.classifier_id)
            label, _name = await self._subject(record.subject, lock=True)
            await self._repo.cancel_classification(
                classification_id, self._actor.username, self._clock(), comment
            )
            await self._emit_on(
                "classification.cancelled",
                record.subject,
                {**label, "classifier": {"name": found.name}},
                {"before": _classification_view(found, record), "after": None},
                comment,
            )
        return record

    # --- общее для рейтингов и классификаторов ----------------------------------

    def _not_in_future(self, valid_from: date) -> None:
        if valid_from > self._clock().date():
            raise RuleViolationError(
                "future_date", "Дата — не позже сегодняшней: вводят то, что уже произошло."
            )

    async def _subject(
        self, subject: Subject, *, lock: bool, rated: bool = False
    ) -> tuple[dict[str, Any], str]:
        """Бумага или эмитент для журнала и сообщений; рейтинг бывает только у облигации."""
        if subject.target is Target.INSTRUMENT:
            record = await self._repo.get(subject.id, lock=lock)
            if rated and record.kind is not InstrumentKind.BOND:
                raise RuleViolationError(
                    "not_rated", "Рейтинг бывает у облигации или эмитента, а не у пая."
                )
            return {"instrument": {"secid": record.secid, "isin": record.isin}}, record.secid
        found = await self._repo.get_issuer(subject.id, lock=lock)
        return {"issuer": {"name": found.name}}, found.name

    async def _name_is_free(self, name: str) -> None:
        if await self._repo.classifier_named(name) is not None:
            raise RuleViolationError("classifier_exists", f"Классификатор «{name}» уже есть.")

    # --- журнал -----------------------------------------------------------------

    async def _emit_on(
        self,
        event_type: str,
        subject: Subject,
        label: dict[str, Any],
        data: dict[str, Any],
        comment: str | None,
    ) -> None:
        await self._outbox.add(
            self._session,
            Event(
                service=SERVICE,
                type=event_type,
                actor=self._actor,
                entity_type=subject.target.value,
                entity_id=str(subject.id),
                data={**label, **data},
                comment=comment,
            ),
        )

    async def _emit_classifier(
        self,
        event_type: str,
        record: ClassifierRecord,
        before: dict[str, Any] | None,
        after: dict[str, Any],
    ) -> None:
        await self._outbox.add(
            self._session,
            Event(
                service=SERVICE,
                type=event_type,
                actor=self._actor,
                entity_type="classifier",
                entity_id=str(record.id),
                data={"classifier": {"name": record.name}, "before": before, "after": after},
            ),
        )

    async def _emit(
        self,
        event_type: str,
        record: InstrumentRecord,
        data: dict[str, Any],
        comment: str | None = None,
    ) -> None:
        await self._outbox.add(
            self._session,
            Event(
                service=SERVICE,
                type=event_type,
                actor=self._actor,
                entity_type="instrument",
                entity_id=str(record.id),
                data={"instrument": {"secid": record.secid, "isin": record.isin}, **data},
                comment=comment,
            ),
        )

    async def _emit_calendar(
        self, event_type: str, day: date, before: CalendarDay | None, after: CalendarDay | None
    ) -> None:
        def state(entry: CalendarDay | None) -> dict[str, Any] | None:
            if entry is None:
                return None
            return {"is_trading": entry.is_trading, "comment": entry.comment}

        await self._outbox.add(
            self._session,
            Event(
                service=SERVICE,
                type=event_type,
                actor=self._actor,
                entity_type="calendar",
                entity_id=day.isoformat(),
                data={"day": day.isoformat(), "before": state(before), "after": state(after)},
            ),
        )


@dataclass(frozen=True, slots=True)
class SyncStats:
    checked: int
    changed: int
    failed: int


async def sync_all(
    database: Database,
    iss: IssClient,
    actor: Actor,
    *,
    clock: Callable[[], datetime] = moscow_now,
) -> SyncStats:
    """Сверяет с ISS все бумаги справочника. Ошибка одной бумаги не мешает остальным."""
    async with database.session() as session:
        ids = await Repository(session).all_ids()
    changed = failed = 0
    for instrument_id in ids:
        async with database.session() as session:
            try:
                changed += await Commands(session, actor, clock=clock).sync(instrument_id, iss)
            except IssError:
                failed += 1
    return SyncStats(checked=len(ids), changed=changed, failed=failed)


def _lower_first(label: str) -> str:
    return label[:1].lower() + label[1:]


def _distinct(values: Sequence[str]) -> list[str]:
    """Значения классификатора: хотя бы одно, без повторов без учёта регистра."""
    if not values:
        raise RuleViolationError("values_required", "Укажите хотя бы одно значение.")
    seen: set[str] = set()
    for value in values:
        if value.lower() in seen:
            raise RuleViolationError("duplicate_value", f"Значение «{value}» указано дважды.")
        seen.add(value.lower())
    return list(values)


def _value_is_free(record: ClassifierRecord, name: str) -> None:
    if any(v.name.lower() == name.lower() for v in record.values):
        raise RuleViolationError(
            "duplicate_value", f"У классификатора «{record.name}» уже есть значение «{name}»."
        )


def _rating_view(record: RatingRecord) -> dict[str, Any]:
    return {
        "action": record.action.value,
        "agency": record.agency,
        "rating": record.rating,
        "valid_from": record.valid_from.isoformat(),
    }


def _classification_view(found: ClassifierRecord, record: ClassificationRecord) -> dict[str, Any]:
    # Значение записи — всегда значение её классификатора (так проверяет classify).
    return {
        "class_value": next(v.name for v in found.values if v.id == record.value_id),
        "valid_from": None if record.valid_from is None else record.valid_from.isoformat(),
    }
