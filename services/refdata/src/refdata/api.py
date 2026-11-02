"""HTTP API справочника. Правила — в docs/design/02-refdata.md."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Path, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints

from am_common.app import AppResources, DbSession
from am_common.auth import CurrentPrincipal, Principal, Role, require_any_role
from am_common.calendar import add_trading_days
from am_common.clock import moscow_now
from am_common.events import Actor
from refdata import scale
from refdata.attributes import (
    ClassificationRecord,
    ClassifierRecord,
    IssuerRecord,
    RatingAction,
    RatingRecord,
    RatingsOn,
    RatingStatus,
    Subject,
    Target,
    classification_on,
    ratings_on,
)
from refdata.commands import Commands
from refdata.domain import (
    Alert,
    CouponKind,
    InstrumentKind,
    NominalIndexation,
    Params,
    RuleViolationError,
    Version,
    alerts,
    issue_terms,
    version_on,
)
from refdata.iss import IssClient, IssError, NotFoundInIssError, UnsupportedSecurityError
from refdata.repository import InstrumentRecord, NotFoundError, Repository
from refdata.settings import RefdataSettings

# Загрузить бумагу могут трейдинг и сопровождение; править — только сопровождение.
Loader = Annotated[Principal, Depends(require_any_role(Role.TRADING, Role.OPERATIONS))]
Operations = Annotated[Principal, Depends(require_any_role(Role.OPERATIONS))]
# Рейтинги и классификаторы — данные для лимитов, их ведёт риск-менеджмент (design/09).
Risk = Annotated[Principal, Depends(require_any_role(Role.RISK))]
Comment = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
OptionalComment = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

router = APIRouter()


def get_iss(resources: AppResources) -> IssClient:
    settings = cast(RefdataSettings, resources.settings)
    return IssClient(resources.http, settings.iss_url, timeout=settings.iss_timeout)


Iss = Annotated[IssClient, Depends(get_iss)]


def get_clock(request: Request) -> Callable[[], datetime]:
    return cast(Callable[[], datetime], request.app.state.clock)


Clock = Annotated[Callable[[], datetime], Depends(get_clock)]


# --- схемы ----------------------------------------------------------------------


class CandidateOut(BaseModel):
    secid: str
    isin: str | None
    short_name: str
    name: str
    type_name: str | None


class LookupOut(BaseModel):
    secid: str
    isin: str | None
    kind: InstrumentKind
    params: Params
    instrument_id: UUID | None  # уже в справочнике


class InstrumentSummary(BaseModel):
    id: UUID
    secid: str
    isin: str | None
    kind: InstrumentKind
    name: str
    short_name: str
    added_by: str
    last_synced_at: datetime | None
    last_sync_error: str | None


class VersionHeader(BaseModel):
    number: int
    valid_from: date | None
    source: str
    author: str
    comment: str | None
    created_at: datetime
    pinned: list[str]


class IssuerRef(BaseModel):
    id: UUID
    name: str


class InstrumentOut(InstrumentSummary):
    current: Version
    versions: list[VersionHeader]
    alerts: list[Alert]
    issuer: IssuerRef | None = None
    iss_type: str | None = None  # тип бумаги ISS: ofz_bond, exchange_bond…


class LoadIn(BaseModel):
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=20)]


class EditIn(BaseModel):
    valid_from: date
    comment: Comment
    changes: dict[str, Any] = {}
    unpin: list[str] = []


class CalendarDayOut(BaseModel):
    day: date
    is_trading: bool
    comment: str | None


class CalendarDayIn(BaseModel):
    is_trading: bool
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None


class ShiftOut(BaseModel):
    date: date


class IssuerSummary(BaseModel):
    id: UUID
    iss_id: int
    name: str
    inn: str | None
    instruments: int  # бумаг эмитента в справочнике


class IssuerOut(BaseModel):
    id: UUID
    iss_id: int
    name: str
    inn: str | None
    instruments: list[InstrumentSummary]


class AgencyOut(BaseModel):
    code: str
    name: str
    ratings: list[str]  # записи агентства — от высшей ступени к низшей


class ScaleOut(BaseModel):
    agencies: list[AgencyOut]
    grades: list[str]


class AgencyRatingOut(BaseModel):
    agency: str
    agency_name: str
    rating: str
    grade: str
    since: date


class RatingsOnOut(BaseModel):
    status: RatingStatus
    ratings: list[AgencyRatingOut]


class RatingOut(BaseModel):
    id: UUID
    action: RatingAction
    agency: str | None
    agency_name: str | None
    rating: str | None
    grade: str | None
    valid_from: date
    comment: str | None
    author: str
    created_at: datetime
    cancelled_by: str | None
    cancelled_at: datetime | None
    cancel_comment: str | None


class RatingsOut(BaseModel):
    """Рейтинги бумаги или эмитента: что действует сегодня и вся история записей."""

    now: RatingsOnOut
    records: list[RatingOut]


class SubjectIn(BaseModel):
    """Бумага или эмитент: ровно одно из двух."""

    instrument_id: UUID | None = None
    issuer_id: UUID | None = None


class RatingIn(SubjectIn):
    action: RatingAction
    agency: str | None = None
    rating: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = None
    valid_from: date
    comment: OptionalComment = None


class CancelIn(BaseModel):
    comment: Comment


class ValueOut(BaseModel):
    id: UUID
    name: str


class ClassifierOut(BaseModel):
    id: UUID
    name: str
    target: Target
    description: str | None
    created_by: str
    created_at: datetime
    values: list[ValueOut]


class ClassifierIn(BaseModel):
    name: Name
    target: Target
    description: OptionalComment = None
    values: list[Name]


class ClassifierUpdateIn(BaseModel):
    name: Name
    description: OptionalComment = None


class ValueIn(BaseModel):
    name: Name


class ClassificationOut(BaseModel):
    id: UUID
    classifier_id: UUID
    classifier: str
    value_id: UUID
    value: str
    valid_from: date | None
    comment: str | None
    author: str
    created_at: datetime
    cancelled_by: str | None
    cancelled_at: datetime | None
    cancel_comment: str | None


class CurrentClassOut(BaseModel):
    classifier_id: UUID
    classifier: str
    value_id: UUID
    value: str
    since: date | None


class ClassificationsOut(BaseModel):
    """Значения классификаторов бумаги или эмитента: сегодня и вся история."""

    now: list[CurrentClassOut]
    records: list[ClassificationOut]


class ClassificationIn(SubjectIn):
    classifier_id: UUID
    value_id: UUID
    valid_from: date | None = None  # None — с начала обращения
    comment: OptionalComment = None


class InstrumentAttributes(BaseModel):
    id: UUID
    secid: str
    short_name: str
    kind: InstrumentKind
    iss_type: str | None
    issuer_id: UUID | None
    coupon_kind: CouponKind | None
    currency: str
    nominal_indexation: NominalIndexation
    classes: dict[UUID, UUID]  # классификатор → значение
    ratings: RatingsOnOut


class IssuerAttributes(BaseModel):
    id: UUID
    name: str
    classes: dict[UUID, UUID]
    ratings: RatingsOnOut


class AttributesOut(BaseModel):
    """Признаки и рейтинги всех бумаг и эмитентов на дату — для сервиса лимитов."""

    date: date
    classifiers: list[ClassifierOut]
    instruments: list[InstrumentAttributes]
    issuers: list[IssuerAttributes]


# --- бумаги ---------------------------------------------------------------------


@router.get("/lookup", tags=["instruments"])
async def lookup(
    code: Annotated[str, Query(min_length=2, max_length=20)],
    _user: CurrentPrincipal,
    session: DbSession,
    iss: Iss,
) -> LookupOut:
    """Найти бумагу в ISS и показать, что будет загружено."""
    security = await iss.find(code)
    existing = await Repository(session).find_by_code(security.secid)
    return LookupOut(
        secid=security.secid,
        isin=security.isin,
        kind=security.kind,
        params=issue_terms(security.params, moscow_now().date()),
        instrument_id=existing.id if existing else None,
    )


@router.post("/instruments", tags=["instruments"])
async def load(
    body: LoadIn, user: Loader, session: DbSession, iss: Iss, response: Response
) -> InstrumentOut:
    """Загрузить бумагу из ISS. Если она уже в справочнике — вернуть её."""
    loaded = await _commands(session, user).load(body.code, iss)
    response.status_code = status.HTTP_201_CREATED if loaded.created else status.HTTP_200_OK
    return await _instrument_out(session, loaded.instrument_id)


@router.get("/instruments", tags=["instruments"])
async def search(
    _user: CurrentPrincipal,
    session: DbSession,
    query: Annotated[str | None, Query(max_length=50)] = None,
) -> list[InstrumentSummary]:
    return [_summary(record) for record in await Repository(session).search(query)]


@router.get("/instruments/{instrument_id}", tags=["instruments"])
async def instrument(
    instrument_id: UUID, _user: CurrentPrincipal, session: DbSession
) -> InstrumentOut:
    return await _instrument_out(session, instrument_id)


@router.get("/instruments/{instrument_id}/versions/{number}", tags=["instruments"])
async def version(
    instrument_id: UUID, number: int, _user: CurrentPrincipal, session: DbSession
) -> Version:
    versions = await Repository(session).versions(instrument_id)
    return _found(next((v for v in versions if v.number == number), None), "version", number)


@router.get("/instruments/{instrument_id}/as-of/{day}", tags=["instruments"])
async def as_of(
    instrument_id: UUID, day: date, _user: CurrentPrincipal, session: DbSession
) -> Version:
    """Какой была бумага на дату."""
    versions = await Repository(session).versions(instrument_id)
    return _found(version_on(versions, day), "version", day)


@router.post("/instruments/{instrument_id}/versions", tags=["instruments"])
async def edit(
    instrument_id: UUID, body: EditIn, user: Operations, session: DbSession
) -> InstrumentOut:
    """Ручная правка: новая версия, изменённые поля закрепляются."""
    await _commands(session, user).edit(
        instrument_id,
        valid_from=body.valid_from,
        comment=body.comment,
        changes=body.changes,
        unpin=body.unpin,
    )
    return await _instrument_out(session, instrument_id)


@router.post("/instruments/{instrument_id}/sync", tags=["instruments"])
async def sync(
    instrument_id: UUID, user: Operations, session: DbSession, iss: Iss
) -> InstrumentOut:
    """Сверить бумагу с ISS сейчас."""
    await _commands(session, user).sync(instrument_id, iss)
    return await _instrument_out(session, instrument_id)


# --- календарь ------------------------------------------------------------------


@router.get("/calendar/add-trading-days", tags=["calendar"])
async def shift(
    day: Annotated[date, Query(alias="date")],
    days: Annotated[int, Query(ge=0, le=30)],
    _user: CurrentPrincipal,
    session: DbSession,
) -> ShiftOut:
    """Дата через N торговых дней — например, T+1 для даты расчётов."""
    exceptions = {e.day: e.is_trading for e in await Repository(session).calendar_exceptions()}
    return ShiftOut(date=add_trading_days(day, days, exceptions))


@router.get("/calendar/{year}", tags=["calendar"])
async def calendar(
    year: Annotated[int, Path(ge=2000, le=2100)], _user: CurrentPrincipal, session: DbSession
) -> list[CalendarDayOut]:
    """Исключения календаря за год: праздники и рабочие выходные."""
    return [
        CalendarDayOut(day=e.day, is_trading=e.is_trading, comment=e.comment)
        for e in await Repository(session).calendar_exceptions(year)
    ]


@router.put("/calendar/days/{day}", status_code=status.HTTP_204_NO_CONTENT, tags=["calendar"])
async def set_day(day: date, body: CalendarDayIn, user: Operations, session: DbSession) -> None:
    await _commands(session, user).set_calendar_day(day, body.is_trading, body.comment)


@router.delete("/calendar/days/{day}", status_code=status.HTTP_204_NO_CONTENT, tags=["calendar"])
async def clear_day(day: date, user: Operations, session: DbSession) -> None:
    await _commands(session, user).clear_calendar_day(day)


# --- эмитенты -------------------------------------------------------------------


@router.get("/issuers", tags=["issuers"])
async def issuers(
    _user: CurrentPrincipal,
    session: DbSession,
    query: Annotated[str | None, Query(max_length=50)] = None,
) -> list[IssuerSummary]:
    """Эмитенты по названию или ИНН."""
    return [
        IssuerSummary(id=r.id, iss_id=r.iss_id, name=r.name, inn=r.inn, instruments=count)
        for r, count in await Repository(session).issuers(query)
    ]


@router.get("/issuers/{issuer_id}", tags=["issuers"])
async def issuer(issuer_id: UUID, _user: CurrentPrincipal, session: DbSession) -> IssuerOut:
    repo = Repository(session)
    found = await repo.get_issuer(issuer_id)
    return IssuerOut(
        id=found.id,
        iss_id=found.iss_id,
        name=found.name,
        inn=found.inn,
        instruments=[_summary(r) for r in await repo.instruments_of(issuer_id)],
    )


# --- рейтинги -------------------------------------------------------------------


@router.get("/ratings/scale", tags=["ratings"])
async def rating_scale(_user: CurrentPrincipal) -> ScaleOut:
    """Агентства и их записи рейтингов — для формы ввода."""
    return ScaleOut(
        agencies=[
            AgencyOut(code=a.code, name=a.name, ratings=scale.notations(a.code))
            for a in scale.AGENCIES
        ],
        grades=list(scale.GRADES),
    )


@router.get("/ratings", tags=["ratings"])
async def ratings(
    _user: CurrentPrincipal,
    session: DbSession,
    clock: Clock,
    instrument_id: UUID | None = None,
    issuer_id: UUID | None = None,
) -> RatingsOut:
    subject = _subject_of(SubjectIn(instrument_id=instrument_id, issuer_id=issuer_id))
    return await _ratings_out(session, subject, clock().date())


@router.post("/ratings", tags=["ratings"])
async def add_rating(body: RatingIn, user: Risk, session: DbSession, clock: Clock) -> RatingsOut:
    """Рейтинг присвоен или изменён, отозван, или рейтинга нет."""
    subject = _subject_of(body)
    await _commands(session, user, clock).add_rating(
        subject,
        body.action,
        agency=body.agency,
        rating=body.rating,
        valid_from=body.valid_from,
        comment=body.comment or None,
    )
    return await _ratings_out(session, subject, clock().date())


@router.post("/ratings/{rating_id}/cancel", tags=["ratings"])
async def cancel_rating(
    rating_id: UUID, body: CancelIn, user: Risk, session: DbSession, clock: Clock
) -> RatingsOut:
    """Отменить ошибочную запись: она остаётся в истории и больше не действует."""
    record = await _commands(session, user, clock).cancel_rating(rating_id, body.comment)
    return await _ratings_out(session, record.subject, clock().date())


# --- классификаторы -------------------------------------------------------------


@router.get("/classifiers", tags=["classifiers"])
async def classifiers(_user: CurrentPrincipal, session: DbSession) -> list[ClassifierOut]:
    return [_classifier_out(r) for r in await Repository(session).classifiers()]


@router.post("/classifiers", status_code=status.HTTP_201_CREATED, tags=["classifiers"])
async def create_classifier(
    body: ClassifierIn, user: Risk, session: DbSession, clock: Clock
) -> ClassifierOut:
    classifier_id = await _commands(session, user, clock).create_classifier(
        body.name, body.target, body.description or None, body.values
    )
    return _classifier_out(await Repository(session).get_classifier(classifier_id))


@router.put("/classifiers/{classifier_id}", tags=["classifiers"])
async def update_classifier(
    classifier_id: UUID, body: ClassifierUpdateIn, user: Risk, session: DbSession, clock: Clock
) -> ClassifierOut:
    """Название и описание; к чему относится классификатор, не меняется."""
    await _commands(session, user, clock).update_classifier(
        classifier_id, body.name, body.description or None
    )
    return _classifier_out(await Repository(session).get_classifier(classifier_id))


@router.post("/classifiers/{classifier_id}/values", tags=["classifiers"])
async def add_value(
    classifier_id: UUID, body: ValueIn, user: Risk, session: DbSession, clock: Clock
) -> ClassifierOut:
    await _commands(session, user, clock).add_value(classifier_id, body.name)
    return _classifier_out(await Repository(session).get_classifier(classifier_id))


@router.put("/classifiers/{classifier_id}/values/{value_id}", tags=["classifiers"])
async def rename_value(
    classifier_id: UUID,
    value_id: UUID,
    body: ValueIn,
    user: Risk,
    *,
    session: DbSession,
    clock: Clock,
) -> ClassifierOut:
    await _commands(session, user, clock).rename_value(classifier_id, value_id, body.name)
    return _classifier_out(await Repository(session).get_classifier(classifier_id))


@router.get("/classifications", tags=["classifiers"])
async def classifications(
    _user: CurrentPrincipal,
    session: DbSession,
    clock: Clock,
    instrument_id: UUID | None = None,
    issuer_id: UUID | None = None,
) -> ClassificationsOut:
    subject = _subject_of(SubjectIn(instrument_id=instrument_id, issuer_id=issuer_id))
    return await _classifications_out(session, subject, clock().date())


@router.post("/classifications", tags=["classifiers"])
async def classify(
    body: ClassificationIn, user: Risk, session: DbSession, clock: Clock
) -> ClassificationsOut:
    """Значение классификатора у бумаги или эмитента с даты; без даты — с начала."""
    subject = _subject_of(body)
    await _commands(session, user, clock).classify(
        subject,
        body.classifier_id,
        body.value_id,
        valid_from=body.valid_from,
        comment=body.comment or None,
    )
    return await _classifications_out(session, subject, clock().date())


@router.post("/classifications/{classification_id}/cancel", tags=["classifiers"])
async def cancel_classification(
    classification_id: UUID, body: CancelIn, user: Risk, session: DbSession, clock: Clock
) -> ClassificationsOut:
    record = await _commands(session, user, clock).cancel_classification(
        classification_id, body.comment
    )
    return await _classifications_out(session, record.subject, clock().date())


# --- признаки на дату -----------------------------------------------------------


@router.get("/attributes", tags=["attributes"])
async def attributes(
    day: Annotated[date, Query(alias="date")], _user: CurrentPrincipal, session: DbSession
) -> AttributesOut:
    """Признаки, классификаторы и рейтинги всех бумаг и эмитентов на дату (design/09)."""
    repo = Repository(session)
    instruments = await repo.all_instruments()
    versions = await repo.all_versions()
    found_issuers = await repo.all_issuers()
    rating_records = await repo.ratings()
    class_records = await repo.classifications()

    def classes(subject: Subject) -> dict[UUID, UUID]:
        mine = [r for r in class_records if r.subject == subject]
        return {k: r.value_id for k, r in classification_on(mine, day).items()}

    def rated(subject: Subject) -> RatingsOnOut:
        return _ratings_on_out(ratings_on((r for r in rating_records if r.subject == subject), day))

    shown: list[InstrumentAttributes] = []
    for record in instruments:
        # Первая версия действует с начала обращения — версия на дату есть всегда.
        on_day = cast(Version, version_on(versions[record.id], day))
        subject = Subject(Target.INSTRUMENT, record.id)
        shown.append(
            InstrumentAttributes(
                id=record.id,
                secid=record.secid,
                short_name=record.short_name,
                kind=record.kind,
                iss_type=record.iss_type,
                issuer_id=record.issuer_id,
                coupon_kind=on_day.params.coupon_kind,
                currency=on_day.params.currency,
                nominal_indexation=on_day.params.nominal_indexation,
                classes=classes(subject),
                ratings=rated(subject),
            )
        )
    return AttributesOut(
        date=day,
        classifiers=[_classifier_out(r) for r in await repo.classifiers()],
        instruments=shown,
        issuers=[
            IssuerAttributes(
                id=i.id,
                name=i.name,
                classes=classes(Subject(Target.ISSUER, i.id)),
                ratings=rated(Subject(Target.ISSUER, i.id)),
            )
            for i in found_issuers
        ],
    )


# --- ошибки → ответы HTTP -------------------------------------------------------


def install_error_handlers(app: FastAPI) -> None:
    def problem(status_code: int, code: str, message: str, **extra: Any) -> Response:
        return JSONResponse(
            status_code=status_code, content={"detail": {"code": code, "message": message, **extra}}
        )

    async def rule_violation(_request: Request, exc: Exception) -> Response:
        violation = cast(RuleViolationError, exc)
        return problem(422, violation.code, violation.message)

    async def not_found(_request: Request, exc: Exception) -> Response:
        missing = cast(NotFoundError, exc)
        return problem(404, "not_found", f"Не найдено: {missing.what}")

    async def not_in_iss(_request: Request, exc: Exception) -> Response:
        missing = cast(NotFoundInIssError, exc)
        candidates = [
            CandidateOut(
                secid=c.secid,
                isin=c.isin,
                short_name=c.short_name,
                name=c.name,
                type_name=c.type_name,
            ).model_dump()
            for c in missing.candidates[:10]
        ]
        return problem(
            404,
            "not_in_iss",
            f"В MOEX ISS нет бумаги с кодом «{missing.code}».",
            candidates=candidates,
        )

    async def unsupported(_request: Request, exc: Exception) -> Response:
        security = cast(UnsupportedSecurityError, exc)
        return problem(
            422,
            "unsupported_security",
            "Бумаги такого типа справочник не поддерживает: "
            f"{security.type_name or 'тип неизвестен'}.",
        )

    async def iss_unavailable(_request: Request, _exc: Exception) -> Response:
        return problem(503, "iss_unavailable", "MOEX ISS недоступен. Повторите позже.")

    app.add_exception_handler(RuleViolationError, rule_violation)
    app.add_exception_handler(NotFoundError, not_found)
    app.add_exception_handler(NotFoundInIssError, not_in_iss)
    app.add_exception_handler(UnsupportedSecurityError, unsupported)
    app.add_exception_handler(IssError, iss_unavailable)


# --- вспомогательное ------------------------------------------------------------


def _found(value: Version | None, what: str, key: object) -> Version:
    if value is None:
        raise NotFoundError(what, key)
    return value


def _commands(
    session: DbSession, principal: Principal, clock: Callable[[], datetime] = moscow_now
) -> Commands:
    return Commands(session, Actor.from_principal(principal), clock=clock)


def _summary(record: InstrumentRecord) -> InstrumentSummary:
    return InstrumentSummary(
        id=record.id,
        secid=record.secid,
        isin=record.isin,
        kind=record.kind,
        name=record.name,
        short_name=record.short_name,
        added_by=record.added_by,
        last_synced_at=record.last_synced_at,
        last_sync_error=record.last_sync_error,
    )


def _subject_of(body: SubjectIn) -> Subject:
    if (body.instrument_id is None) == (body.issuer_id is None):
        raise RuleViolationError("subject_required", "Укажите бумагу или эмитента — одно из двух.")
    if body.instrument_id is not None:
        return Subject(Target.INSTRUMENT, body.instrument_id)
    return Subject(Target.ISSUER, cast(UUID, body.issuer_id))


async def _check_subject(repo: Repository, subject: Subject) -> None:
    if subject.target is Target.INSTRUMENT:
        await repo.get(subject.id)
    else:
        await repo.get_issuer(subject.id)


def _ratings_on_out(found: RatingsOn) -> RatingsOnOut:
    return RatingsOnOut(
        status=found.status,
        ratings=[
            AgencyRatingOut(
                agency=r.agency,
                agency_name=scale.agency(r.agency).name,
                rating=r.rating,
                grade=r.grade,
                since=r.since,
            )
            for r in found.ratings
        ],
    )


async def _ratings_out(session: DbSession, subject: Subject, today: date) -> RatingsOut:
    repo = Repository(session)
    await _check_subject(repo, subject)
    records = await repo.ratings(subject)
    return RatingsOut(
        now=_ratings_on_out(ratings_on(records, today)),
        records=[_rating_out(r) for r in records],
    )


def _rating_out(record: RatingRecord) -> RatingOut:
    return RatingOut(
        id=record.id,
        action=record.action,
        agency=record.agency,
        agency_name=None if record.agency is None else scale.agency(record.agency).name,
        rating=record.rating,
        grade=record.grade,
        valid_from=record.valid_from,
        comment=record.comment,
        author=record.author,
        created_at=record.created_at,
        cancelled_by=record.cancelled_by,
        cancelled_at=record.cancelled_at,
        cancel_comment=record.cancel_comment,
    )


def _classifier_out(record: ClassifierRecord) -> ClassifierOut:
    return ClassifierOut(
        id=record.id,
        name=record.name,
        target=record.target,
        description=record.description,
        created_by=record.created_by,
        created_at=record.created_at,
        values=[ValueOut(id=v.id, name=v.name) for v in record.values],
    )


async def _classifications_out(
    session: DbSession, subject: Subject, today: date
) -> ClassificationsOut:
    repo = Repository(session)
    await _check_subject(repo, subject)
    records = await repo.classifications(subject)
    by_id = {c.id: c for c in await repo.classifiers()}

    def names(record: ClassificationRecord) -> tuple[str, str]:
        found = by_id[record.classifier_id]
        return found.name, next(v.name for v in found.values if v.id == record.value_id)

    now = classification_on(records, today)
    return ClassificationsOut(
        now=[
            CurrentClassOut(
                classifier_id=r.classifier_id,
                classifier=names(r)[0],
                value_id=r.value_id,
                value=names(r)[1],
                since=r.valid_from,
            )
            for r in sorted(now.values(), key=lambda r: names(r)[0])
        ],
        records=[
            ClassificationOut(
                id=r.id,
                classifier_id=r.classifier_id,
                classifier=names(r)[0],
                value_id=r.value_id,
                value=names(r)[1],
                valid_from=r.valid_from,
                comment=r.comment,
                author=r.author,
                created_at=r.created_at,
                cancelled_by=r.cancelled_by,
                cancelled_at=r.cancelled_at,
                cancel_comment=r.cancel_comment,
            )
            for r in records
        ],
    )


async def _issuer_ref(repo: Repository, issuer_id: UUID | None) -> IssuerRef | None:
    if issuer_id is None:
        return None
    found: IssuerRecord = await repo.get_issuer(issuer_id)
    return IssuerRef(id=found.id, name=found.name)


async def _instrument_out(session: DbSession, instrument_id: UUID) -> InstrumentOut:
    repo = Repository(session)
    record = await repo.get(instrument_id)
    versions = await repo.versions(instrument_id)
    current = versions[-1]
    return InstrumentOut(
        issuer=await _issuer_ref(repo, record.issuer_id),
        iss_type=record.iss_type,
        **_summary(record).model_dump(),
        current=current,
        versions=[
            VersionHeader(
                number=v.number,
                valid_from=v.valid_from,
                source=v.source,
                author=v.author,
                comment=v.comment,
                created_at=v.created_at,
                pinned=sorted(v.pinned),
            )
            for v in versions
        ],
        alerts=alerts(current.params, current.pinned, record.iss_snapshot),
    )
