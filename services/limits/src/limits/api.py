"""HTTP API лимитов. Правила — в docs/design/09-limits.md."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Path, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints

from am_common.app import AppResources, DbSession
from am_common.auth import Principal, Role, require_any_role
from am_common.events import Actor
from limits.clients import (
    BookingClient,
    Fund,
    PortfolioClient,
    RefdataClient,
    ServiceUnavailableError,
)
from limits.desk import BreachView, DayView, Desk, ReportView, SetRecord
from limits.engine import CheckResult, Status
from limits.errors import NotFoundError, RuleViolationError
from limits.model import SetBody
from limits.settings import LimitsSettings

# Лимиты смотрят трейдинг, сопровождение и риск-менеджмент; набор ведёт риск-менеджмент.
Viewer = Annotated[Principal, Depends(require_any_role(Role.TRADING, Role.OPERATIONS, Role.RISK))]
Risk = Annotated[Principal, Depends(require_any_role(Role.RISK))]
Authorization = Annotated[str, Header()]
Comment = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
Decision = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]
Month = Annotated[str, Path(pattern=r"^\d{4}-\d{2}$")]

# Отбор журнала: открытые — вместе с просроченными.
STATES = {"open": {"open", "overdue"}, "overdue": {"overdue"}, "eliminated": {"eliminated"}}

router = APIRouter()

SERVICE_NAMES = {
    "portfolio": "Сервис портфелей",
    "booking": "Сервис сделок",
    "refdata": "Справочник",
}
FOUR = Decimal("0.0001")
TWO = Decimal("0.01")


def get_desk(request: Request, resources: AppResources, session: DbSession) -> Desk:
    settings = cast(LimitsSettings, resources.settings)
    return Desk(
        session,
        cast(Callable[[], datetime], request.app.state.clock),
        portfolio=PortfolioClient(resources.http, settings.portfolio_url),
        booking=BookingClient(resources.http, settings.booking_url),
        refdata=RefdataClient(resources.http, settings.refdata_url),
    )


Desks = Annotated[Desk, Depends(get_desk)]


# --- схемы ----------------------------------------------------------------------


class FundOut(BaseModel):
    id: UUID
    code: str
    name: str


class SetHeader(BaseModel):
    id: UUID
    version: int
    valid_from: date
    comment: str
    author: str
    created_at: datetime


class SetOut(SetHeader):
    body: SetBody


class SetIn(BaseModel):
    valid_from: date
    comment: Comment
    body: SetBody


class GroupOut(BaseModel):
    key: str
    name: str
    value: Decimal  # % активов
    status: Status


class ProblemOut(BaseModel):
    secid: str
    reason: str


class CheckOut(BaseModel):
    code: str
    name: str
    clause: str | None
    label: str | None
    type: str
    metric: str | None  # у показателя фонда: leverage | duration
    status: Status
    value: Decimal | None
    maximum: Decimal | None
    minimum: Decimal | None
    usage: Decimal | None  # % лимита
    groups: list[GroupOut]
    violations: list[ProblemOut]
    missing: list[ProblemOut]


class TotalsOut(BaseModel):
    assets: Decimal
    liabilities: Decimal
    nav: Decimal
    leverage: Decimal | None
    duration: Decimal | None


class DayOut(BaseModel):
    fund: FundOut
    date: date
    today: bool  # сегодня — по текущим ценам
    limit_set: SetHeader | None  # нет набора на этот день — проверять нечем
    calculated: bool  # есть ли расчёт фонда за день
    totals: TotalsOut | None
    checks: list[CheckOut]
    open_breaches: int  # открытые нарушения фонда в журнале, с просроченными
    overdue_breaches: int


class NoteOut(BaseModel):
    by: str
    at: datetime
    text: str
    kind: str | None = None  # вид, который подтвердил риск-менеджмент


class ExtensionOut(BaseModel):
    deadline: date
    decision: str
    comment: str
    by: str
    at: datetime


class BreachOut(BaseModel):
    id: UUID
    fund: FundOut
    limit_code: str
    label: str
    check_type: str
    key: str
    name: str  # группа: эмитент, выпуск, бумага; у показателя — «фонд»
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
    state: str  # open | overdue | eliminated
    eliminated_on: date | None
    notes: list[NoteOut]
    extensions: list[ExtensionOut]


class ReviewIn(BaseModel):
    kind: Literal["active", "passive"]
    comment: Comment


class ExtensionIn(BaseModel):
    deadline: date
    decision: Decision  # номер и дата решения генерального директора
    comment: Comment


class ReportOut(BaseModel):
    month: str
    day: DayOut  # использование на конец месяца — или на вчера, если месяц идёт
    breaches: list[BreachOut]  # нарушения, открытые хотя бы день месяца


# --- маршруты -------------------------------------------------------------------


@router.get("/funds", tags=["limits"])
async def funds(_user: Viewer, desk: Desks, authorization: Authorization) -> list[FundOut]:
    """Фонды, которые видит пользователь."""
    return [_fund_out(f) for f in await desk.funds(authorization)]


@router.get("/funds/{fund_id}/sets", tags=["limits"])
async def sets(
    fund_id: UUID, _user: Viewer, desk: Desks, authorization: Authorization
) -> list[SetOut]:
    """Версии набора лимитов фонда — от первой к последней."""
    return [_set_out(r) for r in await desk.sets(fund_id, authorization)]


@router.post("/funds/{fund_id}/sets", status_code=status.HTTP_201_CREATED, tags=["limits"])
async def save_set(
    fund_id: UUID, body: SetIn, user: Risk, desk: Desks, authorization: Authorization
) -> SetOut:
    """Новая версия набора с даты начала действия — риск-менеджмент."""
    record = await desk.save(
        fund_id,
        body.body,
        valid_from=body.valid_from,
        comment=body.comment,
        actor=Actor.from_principal(user),
        authorization=authorization,
    )
    return _set_out(record)


@router.get("/funds/{fund_id}/days/{day}", tags=["limits"])
async def fund_day(
    fund_id: UUID, day: date, _user: Viewer, desk: Desks, authorization: Authorization
) -> DayOut:
    """Использование лимитов за день: сегодня — по текущим ценам."""
    return _day_out(await desk.day(fund_id, day, authorization))


@router.get("/breaches", tags=["breaches"])
async def breaches(
    fund_id: UUID,
    _user: Viewer,
    desk: Desks,
    authorization: Authorization,
    state: Annotated[Literal["open", "overdue", "eliminated"] | None, Query(alias="status")] = None,
) -> list[BreachOut]:
    """Журнал нарушений фонда: открытые — с просроченными; без отбора — все."""
    found = await desk.breaches(fund_id, None if state is None else STATES[state], authorization)
    return [_breach_out(b) for b in found]


@router.post("/breaches/{breach_id}/review", tags=["breaches"])
async def review(
    breach_id: UUID, body: ReviewIn, user: Risk, desk: Desks, authorization: Authorization
) -> BreachOut:
    """Вид нарушения и комментарий — риск-менеджмент."""
    found = await desk.review(
        breach_id,
        kind=body.kind,
        comment=body.comment,
        actor=Actor.from_principal(user),
        authorization=authorization,
    )
    return _breach_out(found)


@router.post("/breaches/{breach_id}/extension", tags=["breaches"])
async def extension(
    breach_id: UUID, body: ExtensionIn, user: Risk, desk: Desks, authorization: Authorization
) -> BreachOut:
    """Продление срока пассивного нарушения по решению генерального директора (п. 8.5)."""
    found = await desk.extend(
        breach_id,
        deadline=body.deadline,
        decision=body.decision,
        comment=body.comment,
        actor=Actor.from_principal(user),
        authorization=authorization,
    )
    return _breach_out(found)


@router.get("/funds/{fund_id}/reports/{month}", tags=["limits"])
async def report(
    fund_id: UUID, month: Month, _user: Viewer, desk: Desks, authorization: Authorization
) -> ReportOut:
    """Отчёт за месяц: использование на конец месяца и нарушения месяца (п. 8.8)."""
    return _report_out(await desk.report(fund_id, month, authorization))


# --- ошибки → ответы HTTP -------------------------------------------------------


def install_error_handlers(app: FastAPI) -> None:
    def problem(status_code: int, code: str, message: str) -> Response:
        return JSONResponse(
            status_code=status_code, content={"detail": {"code": code, "message": message}}
        )

    async def rule_violation(_request: Request, exc: Exception) -> Response:
        violation = cast(RuleViolationError, exc)
        return problem(422, violation.code, violation.message)

    async def not_found(_request: Request, exc: Exception) -> Response:
        return problem(404, "not_found", f"Не найдено: {cast(NotFoundError, exc).what}")

    async def unavailable(_request: Request, exc: Exception) -> Response:
        service = cast(ServiceUnavailableError, exc).service
        return problem(
            503,
            f"{service}_unavailable",
            f"{SERVICE_NAMES.get(service, service)} недоступен. Повторите позже.",
        )

    app.add_exception_handler(RuleViolationError, rule_violation)
    app.add_exception_handler(NotFoundError, not_found)
    app.add_exception_handler(ServiceUnavailableError, unavailable)


# --- вспомогательное ------------------------------------------------------------


def _fund_out(fund: Fund) -> FundOut:
    return FundOut(id=fund.id, code=fund.code, name=fund.name)


def _header(record: SetRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "version": record.version,
        "valid_from": record.valid_from,
        "comment": record.comment,
        "author": record.author,
        "created_at": record.created_at,
    }


def _set_out(record: SetRecord) -> SetOut:
    return SetOut(**_header(record), body=record.body)


def _round(value: Decimal | None, step: Decimal = FOUR) -> Decimal | None:
    return None if value is None else value.quantize(step)


def _plain(value: Decimal | None) -> Decimal | None:
    """Порог, как в наборе: 2.500000 из базы → 2.5, 10.000000 → 10."""
    if value is None:
        return None
    found = value.normalize()
    return found.quantize(Decimal(1)) if found == found.to_integral_value() else found


def _check_out(result: CheckResult) -> CheckOut:
    return CheckOut(
        code=result.code,
        name=result.name,
        clause=result.clause,
        label=result.label,
        type=result.type,
        metric=result.metric,
        status=result.status,
        value=_round(result.value),
        maximum=result.maximum,
        minimum=result.minimum,
        usage=_round(result.usage, TWO),
        groups=[
            GroupOut(key=g.key, name=g.name, value=g.value.quantize(FOUR), status=g.status)
            for g in result.groups
        ],
        violations=[ProblemOut(secid=p.secid, reason=p.reason) for p in result.violations],
        missing=[ProblemOut(secid=p.secid, reason=p.reason) for p in result.missing],
    )


def _day_out(view: DayView) -> DayOut:
    usage = view.usage
    return DayOut(
        fund=_fund_out(view.fund),
        date=view.day,
        today=view.today,
        limit_set=None if view.limit_set is None else SetHeader(**_header(view.limit_set)),
        calculated=usage is not None,
        totals=None
        if usage is None
        else TotalsOut(
            assets=usage.totals.assets.quantize(TWO),
            liabilities=usage.totals.liabilities.quantize(TWO),
            nav=usage.totals.nav.quantize(TWO),
            leverage=_round(usage.totals.leverage),
            duration=_round(usage.totals.duration),
        ),
        checks=[] if usage is None else [_check_out(c) for c in usage.checks],
        open_breaches=view.open_breaches,
        overdue_breaches=view.overdue_breaches,
    )


def _breach_out(view: BreachView) -> BreachOut:
    record = view.record
    return BreachOut(
        id=record.id,
        fund=_fund_out(view.fund),
        limit_code=record.limit_code,
        label=record.label,
        check_type=record.check_type,
        key=record.key,
        name=record.name,
        reason=record.reason,
        first_day=record.first_day,
        last_day=record.last_day,
        detected_on=record.detected_on,
        value=_round(record.value),
        peak=_round(record.peak),
        maximum=_plain(record.maximum),
        minimum=_plain(record.minimum),
        suggested=record.suggested,
        kind=record.kind,
        kind_by=record.kind_by,
        deadline=record.deadline,
        state=view.state,
        eliminated_on=record.eliminated_on,
        notes=[NoteOut.model_validate(n) for n in record.notes],
        extensions=[ExtensionOut.model_validate(e) for e in record.extensions],
    )


def _report_out(found: ReportView) -> ReportOut:
    return ReportOut(
        month=found.month,
        day=_day_out(found.view),
        breaches=[_breach_out(b) for b in found.breaches],
    )
