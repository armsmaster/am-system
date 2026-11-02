"""Шкала рейтингов и что действует на дату — без базы (design/09)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest

from refdata.attributes import (
    AgencyRating,
    ClassificationRecord,
    RatingAction,
    RatingRecord,
    RatingStatus,
    Subject,
    Target,
    classification_on,
    ratings_on,
)
from refdata.domain import RuleViolationError
from refdata.scale import AGENCIES, GRADES, grade_of, notation, notations

ISSUER = Subject(Target.ISSUER, uuid4())
CREATED = datetime(2026, 11, 17, tzinfo=UTC)


@pytest.mark.parametrize(
    ("agency", "written"),
    [("acra", "AA-(RU)"), ("expert_ra", "ruAA-"), ("nkr", "AA-.ru"), ("nra", "AA-|ru|")],
)
def test_each_agency_writes_its_own_way(agency: str, written: str) -> None:
    assert notation(agency, "AA-") == written
    assert grade_of(agency, written) == "AA-"


def test_typed_ratings_are_understood() -> None:
    # Типографский минус из пресс-релиза и регистр букв национальной шкалы — не помеха.
    assert grade_of("acra", " AA−(ru) ") == "AA-"
    assert grade_of("expert_ra", "RUaaa") == "AAA"
    assert notations("nra")[:3] == ["AAA|ru|", "AA+|ru|", "AA|ru|"]
    assert len(notations("nkr")) == len(GRADES)
    assert [a.name for a in AGENCIES] == ["АКРА", "Эксперт РА", "НКР", "НРА"]


@pytest.mark.parametrize(
    ("agency", "written", "code"),
    [
        ("acra", "ruAA-", "unknown_rating"),  # запись другого агентства
        ("acra", "AAAA(RU)", "unknown_rating"),
        ("moodys", "Aa3", "unknown_agency"),
    ],
)
def test_wrong_ratings_are_refused(agency: str, written: str, code: str) -> None:
    with pytest.raises(RuleViolationError) as refused:
        grade_of(agency, written)
    assert refused.value.code == code


def test_wrong_rating_message_shows_an_example() -> None:
    with pytest.raises(RuleViolationError) as refused:
        grade_of("expert_ra", "AA-(RU)")
    assert refused.value.message == (
        "«AA-(RU)» — не рейтинг Эксперт РА по национальной шкале. Пример записи: ruAA-."
    )


def rating(
    seq: int,
    valid_from: date,
    action: RatingAction = RatingAction.ASSIGNED,
    agency: str | None = "acra",
    grade: str | None = "AA+",
    *,
    cancelled: bool = False,
) -> RatingRecord:
    return RatingRecord(
        id=uuid4(),
        seq=seq,
        subject=ISSUER,
        action=action,
        agency=agency,
        rating=None if grade is None or agency is None else notation(agency, grade),
        grade=grade,
        valid_from=valid_from,
        comment=None,
        author="risk",
        created_at=CREATED,
        cancelled_by="risk" if cancelled else None,
        cancelled_at=CREATED if cancelled else None,
        cancel_comment="ошибка" if cancelled else None,
    )


def test_highest_and_latest_per_agency() -> None:
    records = [
        rating(1, date(2026, 6, 1)),
        rating(2, date(2026, 6, 1), agency="expert_ra"),
        rating(3, date(2026, 11, 11), grade="AA-"),
        # Две записи одного дня: действует поздняя.
        rating(4, date(2026, 11, 12), agency="expert_ra", grade="AA"),
        rating(5, date(2026, 11, 12), agency="expert_ra", grade="AA-"),
    ]

    assert ratings_on(records, date(2026, 11, 11)).ratings == (
        AgencyRating("acra", "AA-(RU)", "AA-", date(2026, 11, 11)),
        AgencyRating("expert_ra", "ruAA+", "AA+", date(2026, 6, 1)),
    )
    assert [r.grade for r in ratings_on(records, date(2026, 11, 12)).ratings] == ["AA-", "AA-"]


def test_unrated_is_not_unknown() -> None:
    withdrawn = [
        rating(1, date(2026, 6, 1)),
        rating(2, date(2026, 11, 13), RatingAction.WITHDRAWN, grade=None),
    ]
    marked = [rating(1, date(2026, 6, 1), RatingAction.UNRATED, agency=None, grade=None)]
    mistaken = [rating(1, date(2026, 6, 1), cancelled=True)]

    assert ratings_on(withdrawn, date(2026, 11, 12)).status is RatingStatus.RATED
    assert ratings_on(withdrawn, date(2026, 11, 13)).status is RatingStatus.UNRATED
    assert ratings_on(marked, date(2026, 11, 13)).status is RatingStatus.UNRATED
    # До первой записи и по отменённой записи рейтинг неизвестен: его ещё не вводили.
    assert ratings_on(withdrawn, date(2026, 5, 31)).status is RatingStatus.UNKNOWN
    assert ratings_on(mistaken, date(2026, 11, 13)).status is RatingStatus.UNKNOWN


def classified(
    seq: int, value: UUID, valid_from: date | None, *, cancelled: bool = False
) -> ClassificationRecord:
    return ClassificationRecord(
        id=uuid4(),
        seq=seq,
        classifier_id=CLASSIFIER,
        subject=ISSUER,
        value_id=value,
        valid_from=valid_from,
        comment=None,
        author="risk",
        created_at=CREATED,
        cancelled_by=None,
        cancelled_at=CREATED if cancelled else None,
        cancel_comment=None,
    )


CLASSIFIER = uuid4()
BANK, OTHER = uuid4(), uuid4()


def test_classification_from_the_start_and_from_a_date() -> None:
    records = [
        classified(1, OTHER, None),
        classified(2, BANK, date(2026, 11, 1)),
        classified(3, OTHER, date(2026, 11, 10), cancelled=True),
    ]

    assert classification_on(records, date(2020, 1, 1))[CLASSIFIER].value_id == OTHER
    assert classification_on(records, date(2026, 11, 12))[CLASSIFIER].value_id == BANK
    assert classification_on(records[1:], date(2026, 10, 31)) == {}
