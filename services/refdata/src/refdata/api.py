"""HTTP API справочника. Правила — в docs/design/02-refdata.md."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Path, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints

from am_common.app import AppResources, DbSession
from am_common.auth import CurrentPrincipal, Principal, Role, require_any_role
from am_common.events import Actor
from refdata.calendar import add_trading_days
from refdata.clock import moscow_now
from refdata.commands import Commands
from refdata.domain import (
    Alert,
    InstrumentKind,
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
Comment = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]

router = APIRouter()


def get_iss(resources: AppResources) -> IssClient:
    settings = cast(RefdataSettings, resources.settings)
    return IssClient(resources.http, settings.iss_url, timeout=settings.iss_timeout)


Iss = Annotated[IssClient, Depends(get_iss)]


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


class InstrumentOut(InstrumentSummary):
    current: Version
    versions: list[VersionHeader]
    alerts: list[Alert]


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


def _commands(session: DbSession, principal: Principal) -> Commands:
    return Commands(session, Actor.from_principal(principal))


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


async def _instrument_out(session: DbSession, instrument_id: UUID) -> InstrumentOut:
    repo = Repository(session)
    record = await repo.get(instrument_id)
    versions = await repo.versions(instrument_id)
    current = versions[-1]
    return InstrumentOut(
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
