"""Чтение и запись справочника в БД."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Row, delete, extract, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from refdata.attributes import (
    ClassificationRecord,
    ClassifierRecord,
    IssuerRecord,
    RatingAction,
    RatingRecord,
    Subject,
    Target,
    ValueRecord,
)
from refdata.db import (
    classification,
    classifier,
    classifier_value,
    instrument,
    instrument_version,
    issuer,
    rating,
    sync_run,
    trading_day,
)
from refdata.domain import InstrumentKind, Params, Version
from refdata.iss import IssIssuer


class NotFoundError(Exception):
    def __init__(self, what: str, key: object) -> None:
        super().__init__(f"{what} {key} not found")
        self.what = what
        self.key = key


@dataclass(frozen=True, slots=True)
class InstrumentRecord:
    id: UUID
    secid: str
    isin: str | None
    kind: InstrumentKind
    name: str
    short_name: str
    added_by: str
    added_at: datetime
    iss_snapshot: Params | None
    last_synced_at: datetime | None
    last_sync_error: str | None
    issuer_id: UUID | None = None
    iss_type: str | None = None


@dataclass(frozen=True, slots=True)
class CalendarDay:
    day: date
    is_trading: bool
    comment: str | None


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- инструменты ------------------------------------------------------------

    async def find_by_code(self, code: str) -> InstrumentRecord | None:
        wanted = code.strip().upper()
        row = (
            await self._session.execute(
                select(instrument).where(
                    or_(
                        func.upper(instrument.c.secid) == wanted,
                        func.upper(instrument.c.isin) == wanted,
                    )
                )
            )
        ).one_or_none()
        return None if row is None else _record(row)

    async def get(self, instrument_id: UUID, *, lock: bool = False) -> InstrumentRecord:
        query = select(instrument).where(instrument.c.id == instrument_id)
        if lock:
            # Версии бумаги нумеруются по порядку — правки одной бумаги идут по очереди.
            query = query.with_for_update()
        row = (await self._session.execute(query)).one_or_none()
        if row is None:
            raise NotFoundError("instrument", instrument_id)
        return _record(row)

    async def search(self, query: str | None, limit: int = 200) -> list[InstrumentRecord]:
        statement = select(instrument).order_by(instrument.c.secid).limit(limit)
        if query:
            pattern = f"%{query.strip()}%"
            statement = statement.where(
                or_(
                    instrument.c.secid.ilike(pattern),
                    instrument.c.isin.ilike(pattern),
                    instrument.c.name.ilike(pattern),
                    instrument.c.short_name.ilike(pattern),
                )
            )
        return [_record(row) for row in await self._session.execute(statement)]

    async def all_ids(self) -> list[UUID]:
        rows = await self._session.execute(select(instrument.c.id).order_by(instrument.c.secid))
        return [row.id for row in rows]

    async def all_instruments(self) -> list[InstrumentRecord]:
        rows = await self._session.execute(select(instrument).order_by(instrument.c.secid))
        return [_record(row) for row in rows]

    async def all_versions(self) -> dict[UUID, list[Version]]:
        rows = await self._session.execute(
            select(instrument_version).order_by(
                instrument_version.c.instrument_id, instrument_version.c.number
            )
        )
        found: dict[UUID, list[Version]] = {}
        for row in rows:
            found.setdefault(row.instrument_id, []).append(_version(row))
        return found

    async def link_issuer(
        self, instrument_id: UUID, found: IssIssuer | None, iss_type: str | None
    ) -> None:
        """Эмитент и тип бумаги — как их видит ISS; эмитента заводит по коду ISS.

        ISS не сообщил эмитента или тип — прежние остаются: к эмитенту привязаны
        рейтинги и классификаторы, терять их из-за неполного ответа нельзя.
        """
        values: dict[str, Any] = {}
        if found is not None:
            values["issuer_id"] = await self._upsert_issuer(found)
        if iss_type is not None:
            values["iss_type"] = iss_type
        if values:
            await self._session.execute(
                update(instrument).where(instrument.c.id == instrument_id).values(**values)
            )

    async def _upsert_issuer(self, found: IssIssuer) -> UUID:
        result = await self._session.execute(
            insert(issuer)
            .values(id=uuid4(), iss_id=found.iss_id, name=found.name, inn=found.inn)
            .on_conflict_do_update(
                index_elements=[issuer.c.iss_id], set_={"name": found.name, "inn": found.inn}
            )
            .returning(issuer.c.id)
        )
        issuer_id: UUID = result.scalar_one()
        return issuer_id

    # --- эмитенты ---------------------------------------------------------------

    async def issuers(self, query: str | None, limit: int = 200) -> list[tuple[IssuerRecord, int]]:
        """Эмитенты с числом их бумаг в справочнике."""
        count = (
            select(func.count())
            .where(instrument.c.issuer_id == issuer.c.id)
            .scalar_subquery()
            .label("instruments")
        )
        statement = select(issuer, count).order_by(issuer.c.name).limit(limit)
        if query:
            text = query.strip()
            statement = statement.where(or_(issuer.c.name.ilike(f"%{text}%"), issuer.c.inn == text))
        return [(_issuer(row), row.instruments) for row in await self._session.execute(statement)]

    async def get_issuer(self, issuer_id: UUID, *, lock: bool = False) -> IssuerRecord:
        query = select(issuer).where(issuer.c.id == issuer_id)
        if lock:
            query = query.with_for_update()
        row = (await self._session.execute(query)).one_or_none()
        if row is None:
            raise NotFoundError("issuer", issuer_id)
        return _issuer(row)

    async def all_issuers(self) -> list[IssuerRecord]:
        return [
            _issuer(row)
            for row in await self._session.execute(select(issuer).order_by(issuer.c.name))
        ]

    async def instruments_of(self, issuer_id: UUID) -> list[InstrumentRecord]:
        rows = await self._session.execute(
            select(instrument)
            .where(instrument.c.issuer_id == issuer_id)
            .order_by(instrument.c.secid)
        )
        return [_record(row) for row in rows]

    # --- рейтинги ---------------------------------------------------------------

    async def ratings(self, subject: Subject | None = None) -> list[RatingRecord]:
        statement = select(rating).order_by(rating.c.seq)
        if subject is not None:
            statement = statement.where(_subject_column(rating, subject) == subject.id)
        return [_rating(row) for row in await self._session.execute(statement)]

    async def get_rating(self, rating_id: UUID) -> RatingRecord:
        row = (
            await self._session.execute(select(rating).where(rating.c.id == rating_id))
        ).one_or_none()
        if row is None:
            raise NotFoundError("rating", rating_id)
        return _rating(row)

    async def add_rating(self, record: RatingRecord) -> None:
        await self._session.execute(
            insert(rating).values(
                id=record.id,
                **_subject_values(record.subject),
                action=record.action.value,
                agency=record.agency,
                rating=record.rating,
                grade=record.grade,
                valid_from=record.valid_from,
                comment=record.comment,
                author=record.author,
                created_at=record.created_at,
            )
        )

    async def cancel_rating(self, rating_id: UUID, by: str, at: datetime, comment: str) -> None:
        await self._session.execute(
            update(rating)
            .where(rating.c.id == rating_id)
            .values(cancelled_by=by, cancelled_at=at, cancel_comment=comment)
        )

    # --- классификаторы ---------------------------------------------------------

    async def classifiers(self) -> list[ClassifierRecord]:
        heads = (await self._session.execute(select(classifier).order_by(classifier.c.name))).all()
        values = await self._session.execute(
            select(classifier_value).order_by(classifier_value.c.position)
        )
        by_classifier: dict[UUID, list[ValueRecord]] = {}
        for row in values:
            by_classifier.setdefault(row.classifier_id, []).append(
                ValueRecord(row.id, row.name, row.position)
            )
        return [_classifier(row, by_classifier.get(row.id, [])) for row in heads]

    async def get_classifier(self, classifier_id: UUID, *, lock: bool = False) -> ClassifierRecord:
        query = select(classifier).where(classifier.c.id == classifier_id)
        if lock:
            # Значения нумеруются по порядку — правки одного классификатора идут по очереди.
            query = query.with_for_update()
        row = (await self._session.execute(query)).one_or_none()
        if row is None:
            raise NotFoundError("classifier", classifier_id)
        values = await self._session.execute(
            select(classifier_value)
            .where(classifier_value.c.classifier_id == classifier_id)
            .order_by(classifier_value.c.position)
        )
        return _classifier(row, [ValueRecord(v.id, v.name, v.position) for v in values])

    async def classifier_named(self, name: str) -> UUID | None:
        found: UUID | None = (
            await self._session.execute(
                select(classifier.c.id).where(func.lower(classifier.c.name) == name.lower())
            )
        ).scalar_one_or_none()
        return found

    async def add_classifier(self, record: ClassifierRecord) -> None:
        await self._session.execute(
            insert(classifier).values(
                id=record.id,
                name=record.name,
                target=record.target.value,
                description=record.description,
                created_by=record.created_by,
                created_at=record.created_at,
            )
        )
        for value in record.values:
            await self.add_value(record.id, value)

    async def update_classifier(
        self, classifier_id: UUID, name: str, description: str | None
    ) -> None:
        await self._session.execute(
            update(classifier)
            .where(classifier.c.id == classifier_id)
            .values(name=name, description=description)
        )

    async def add_value(self, classifier_id: UUID, value: ValueRecord) -> None:
        await self._session.execute(
            insert(classifier_value).values(
                id=value.id, classifier_id=classifier_id, name=value.name, position=value.position
            )
        )

    async def rename_value(self, value_id: UUID, name: str) -> None:
        await self._session.execute(
            update(classifier_value).where(classifier_value.c.id == value_id).values(name=name)
        )

    # --- значения классификаторов -----------------------------------------------

    async def classifications(self, subject: Subject | None = None) -> list[ClassificationRecord]:
        statement = select(classification).order_by(classification.c.seq)
        if subject is not None:
            statement = statement.where(_subject_column(classification, subject) == subject.id)
        return [_classification(row) for row in await self._session.execute(statement)]

    async def get_classification(self, classification_id: UUID) -> ClassificationRecord:
        row = (
            await self._session.execute(
                select(classification).where(classification.c.id == classification_id)
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("classification", classification_id)
        return _classification(row)

    async def add_classification(self, record: ClassificationRecord) -> None:
        await self._session.execute(
            insert(classification).values(
                id=record.id,
                classifier_id=record.classifier_id,
                **_subject_values(record.subject),
                value_id=record.value_id,
                valid_from=record.valid_from,
                comment=record.comment,
                author=record.author,
                created_at=record.created_at,
            )
        )

    async def cancel_classification(
        self, classification_id: UUID, by: str, at: datetime, comment: str
    ) -> None:
        await self._session.execute(
            update(classification)
            .where(classification.c.id == classification_id)
            .values(cancelled_by=by, cancelled_at=at, cancel_comment=comment)
        )

    async def insert(self, record: InstrumentRecord) -> bool:
        """Добавляет бумагу; False — такая уже есть (её успел добавить кто-то другой)."""
        result = await self._session.execute(
            insert(instrument)
            .values(
                id=record.id,
                secid=record.secid,
                isin=record.isin,
                kind=record.kind.value,
                name=record.name,
                short_name=record.short_name,
                added_by=record.added_by,
                iss_snapshot=_json(record.iss_snapshot),
                last_synced_at=record.last_synced_at,
            )
            .on_conflict_do_nothing(index_elements=[instrument.c.secid])
            .returning(instrument.c.id)
        )
        return result.one_or_none() is not None

    async def set_names(self, instrument_id: UUID, params: Params) -> None:
        await self._session.execute(
            update(instrument)
            .where(instrument.c.id == instrument_id)
            .values(name=params.name, short_name=params.short_name)
        )

    async def set_sync_state(
        self,
        instrument_id: UUID,
        *,
        synced_at: datetime | None,
        snapshot: Params | None,
        error: str | None,
    ) -> None:
        values: dict[str, Any] = {"last_sync_error": error}
        if snapshot is not None:
            values.update(iss_snapshot=_json(snapshot), last_synced_at=synced_at)
        await self._session.execute(
            update(instrument).where(instrument.c.id == instrument_id).values(**values)
        )

    # --- версии -----------------------------------------------------------------

    async def versions(self, instrument_id: UUID) -> list[Version]:
        rows = await self._session.execute(
            select(instrument_version)
            .where(instrument_version.c.instrument_id == instrument_id)
            .order_by(instrument_version.c.number)
        )
        return [_version(row) for row in rows]

    async def latest_version(self, instrument_id: UUID) -> Version:
        # У бумаги всегда есть хотя бы первая версия — она создаётся вместе с бумагой.
        return (await self.versions(instrument_id))[-1]

    async def add_version(self, instrument_id: UUID, version: Version) -> None:
        await self._session.execute(
            insert(instrument_version).values(
                instrument_id=instrument_id,
                number=version.number,
                valid_from=version.valid_from,
                source=version.source,
                author=version.author,
                comment=version.comment,
                created_at=version.created_at,
                pinned=sorted(version.pinned),
                params=_json(version.params),
            )
        )

    # --- календарь --------------------------------------------------------------

    async def calendar_exceptions(self, year: int | None = None) -> list[CalendarDay]:
        statement = select(trading_day).order_by(trading_day.c.day)
        if year is not None:
            statement = statement.where(extract("year", trading_day.c.day) == year)
        return [
            CalendarDay(row.day, row.is_trading, row.comment)
            for row in await self._session.execute(statement)
        ]

    async def calendar_day(self, day: date) -> CalendarDay | None:
        row = (
            await self._session.execute(select(trading_day).where(trading_day.c.day == day))
        ).one_or_none()
        return None if row is None else CalendarDay(row.day, row.is_trading, row.comment)

    async def set_calendar_day(self, entry: CalendarDay, updated_by: str) -> None:
        values = {
            "is_trading": entry.is_trading,
            "comment": entry.comment,
            "updated_by": updated_by,
        }
        await self._session.execute(
            insert(trading_day)
            .values(day=entry.day, **values)
            .on_conflict_do_update(
                index_elements=[trading_day.c.day], set_={**values, "updated_at": func.now()}
            )
        )

    async def clear_calendar_day(self, day: date) -> None:
        await self._session.execute(delete(trading_day).where(trading_day.c.day == day))

    # --- ежедневная синхронизация -----------------------------------------------

    async def claim_sync_run(self, run_date: date, now: datetime, retry_after: timedelta) -> bool:
        """Забирает сегодняшнюю синхронизацию.

        Через ``retry_after`` после начала запуск забирается заново, если он
        не закончился (экземпляр упал посередине) или закончился с ошибками
        (ISS не ответил по части бумаг).
        """
        statement = (
            insert(sync_run)
            .values(run_date=run_date, started_at=now)
            .on_conflict_do_update(
                index_elements=[sync_run.c.run_date],
                set_={"started_at": now, "finished_at": None},
                where=(sync_run.c.finished_at.is_(None) | (sync_run.c.failed > 0))
                & (sync_run.c.started_at < now - retry_after),
            )
            .returning(sync_run.c.run_date)
        )
        return (await self._session.execute(statement)).one_or_none() is not None

    async def finish_sync_run(
        self, run_date: date, now: datetime, checked: int, changed: int, failed: int
    ) -> None:
        await self._session.execute(
            update(sync_run)
            .where(sync_run.c.run_date == run_date)
            .values(finished_at=now, checked=checked, changed=changed, failed=failed)
        )


def _json(params: Params | None) -> dict[str, Any] | None:
    return None if params is None else params.model_dump(mode="json")


def _record(row: Row[Any]) -> InstrumentRecord:
    return InstrumentRecord(
        id=row.id,
        secid=row.secid,
        isin=row.isin,
        kind=InstrumentKind(row.kind),
        name=row.name,
        short_name=row.short_name,
        added_by=row.added_by,
        added_at=row.added_at,
        iss_snapshot=None if row.iss_snapshot is None else Params.model_validate(row.iss_snapshot),
        last_synced_at=row.last_synced_at,
        last_sync_error=row.last_sync_error,
        issuer_id=row.issuer_id,
        iss_type=row.iss_type,
    )


def _issuer(row: Row[Any]) -> IssuerRecord:
    return IssuerRecord(id=row.id, iss_id=row.iss_id, name=row.name, inn=row.inn)


def _subject_column(table: Any, subject: Subject) -> Any:
    return table.c.instrument_id if subject.target is Target.INSTRUMENT else table.c.issuer_id


def _subject_values(subject: Subject) -> dict[str, UUID | None]:
    if subject.target is Target.INSTRUMENT:
        return {"instrument_id": subject.id, "issuer_id": None}
    return {"instrument_id": None, "issuer_id": subject.id}


def _subject_of(row: Row[Any]) -> Subject:
    if row.instrument_id is not None:
        return Subject(Target.INSTRUMENT, row.instrument_id)
    return Subject(Target.ISSUER, row.issuer_id)


def _rating(row: Row[Any]) -> RatingRecord:
    return RatingRecord(
        id=row.id,
        seq=row.seq,
        subject=_subject_of(row),
        action=RatingAction(row.action),
        agency=row.agency,
        rating=row.rating,
        grade=row.grade,
        valid_from=row.valid_from,
        comment=row.comment,
        author=row.author,
        created_at=row.created_at,
        cancelled_by=row.cancelled_by,
        cancelled_at=row.cancelled_at,
        cancel_comment=row.cancel_comment,
    )


def _classifier(row: Row[Any], values: list[ValueRecord]) -> ClassifierRecord:
    return ClassifierRecord(
        id=row.id,
        name=row.name,
        target=Target(row.target),
        description=row.description,
        created_by=row.created_by,
        created_at=row.created_at,
        values=tuple(values),
    )


def _classification(row: Row[Any]) -> ClassificationRecord:
    return ClassificationRecord(
        id=row.id,
        seq=row.seq,
        classifier_id=row.classifier_id,
        subject=_subject_of(row),
        value_id=row.value_id,
        valid_from=row.valid_from,
        comment=row.comment,
        author=row.author,
        created_at=row.created_at,
        cancelled_by=row.cancelled_by,
        cancelled_at=row.cancelled_at,
        cancel_comment=row.cancel_comment,
    )


def _version(row: Row[Any]) -> Version:
    return Version(
        number=row.number,
        valid_from=row.valid_from,
        source=row.source,
        author=row.author,
        comment=row.comment,
        created_at=row.created_at,
        pinned=frozenset(row.pinned),
        params=Params.model_validate(row.params),
    )
