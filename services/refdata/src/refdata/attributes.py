"""Эмитенты, рейтинги и классификаторы: записи и что действует на дату (design/09).

Записи только дописываются: изменился рейтинг — новая запись с датой, прежняя
остаётся в истории. Ошибочную запись отменяют с комментарием — она остаётся
в истории, но на дату больше не действует. Из записей одной даты действует
поздняя (по порядку записи).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID


class Target(StrEnum):
    """К чему относится классификатор или рейтинг."""

    INSTRUMENT = "instrument"
    ISSUER = "issuer"


class RatingAction(StrEnum):
    ASSIGNED = "assigned"  # присвоен или изменён
    WITHDRAWN = "withdrawn"  # отозван
    UNRATED = "unrated"  # рейтинга нет ни у одного агентства: риск-менеджмент проверил


class RatingStatus(StrEnum):
    RATED = "rated"
    UNRATED = "unrated"  # рейтинга нет — по политике группы нет
    UNKNOWN = "unknown"  # рейтинг ещё не вводили — «нет данных», а не «нет рейтинга»


@dataclass(frozen=True, slots=True)
class Subject:
    """Бумага или эмитент — чей рейтинг или значение классификатора."""

    target: Target
    id: UUID


@dataclass(frozen=True, slots=True)
class IssuerRecord:
    id: UUID
    iss_id: int
    name: str
    inn: str | None


@dataclass(frozen=True, slots=True)
class RatingRecord:
    id: UUID
    seq: int
    subject: Subject
    action: RatingAction
    agency: str | None
    rating: str | None  # запись агентства: AA-(RU)
    grade: str | None  # ступень общей шкалы: AA-
    valid_from: date
    comment: str | None
    author: str
    created_at: datetime
    cancelled_by: str | None
    cancelled_at: datetime | None
    cancel_comment: str | None


@dataclass(frozen=True, slots=True)
class AgencyRating:
    agency: str
    rating: str
    grade: str
    since: date


@dataclass(frozen=True, slots=True)
class RatingsOn:
    status: RatingStatus
    ratings: tuple[AgencyRating, ...]  # действующие рейтинги — по агентствам


@dataclass(frozen=True, slots=True)
class ValueRecord:
    id: UUID
    name: str
    position: int


@dataclass(frozen=True, slots=True)
class ClassifierRecord:
    id: UUID
    name: str
    target: Target
    description: str | None
    created_by: str
    created_at: datetime
    values: tuple[ValueRecord, ...]

    def value(self, value_id: UUID) -> ValueRecord | None:
        return next((v for v in self.values if v.id == value_id), None)


@dataclass(frozen=True, slots=True)
class ClassificationRecord:
    id: UUID
    seq: int
    classifier_id: UUID
    subject: Subject
    value_id: UUID
    valid_from: date | None  # None — с начала обращения
    comment: str | None
    author: str
    created_at: datetime
    cancelled_by: str | None
    cancelled_at: datetime | None
    cancel_comment: str | None


def ratings_on(records: Iterable[RatingRecord], day: date) -> RatingsOn:
    """Рейтинги на дату: по каждому агентству — его последняя запись не позже ``day``.

    Действующих рейтингов нет, а записи есть (отзыв или «рейтинга нет») — рейтинга
    нет. Записей нет вовсе — неизвестно: рейтинг ещё не вводили.
    """
    live = [r for r in records if r.cancelled_at is None and r.valid_from <= day]
    latest: dict[str, RatingRecord] = {}
    for record in sorted(live, key=lambda r: (r.valid_from, r.seq)):
        if record.agency is not None:
            latest[record.agency] = record
    active = tuple(
        AgencyRating(r.agency, r.rating, r.grade, r.valid_from)
        for r in latest.values()
        if r.action is RatingAction.ASSIGNED
        and r.agency is not None
        and r.rating is not None
        and r.grade is not None
    )
    if active:
        return RatingsOn(RatingStatus.RATED, tuple(sorted(active, key=lambda a: a.agency)))
    return RatingsOn(RatingStatus.UNRATED if live else RatingStatus.UNKNOWN, ())


def classification_on(
    records: Iterable[ClassificationRecord], day: date
) -> dict[UUID, ClassificationRecord]:
    """Значения классификаторов на дату: по каждому — последняя запись не позже ``day``."""
    live = [
        r
        for r in records
        if r.cancelled_at is None and (r.valid_from is None or r.valid_from <= day)
    ]
    found: dict[UUID, ClassificationRecord] = {}
    for record in sorted(live, key=lambda r: (r.valid_from or date.min, r.seq)):
        found[record.classifier_id] = record
    return found
