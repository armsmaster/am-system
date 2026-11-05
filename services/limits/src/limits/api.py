"""HTTP API лимитов. Правила — в docs/design/09-limits.md."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Path, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints, model_validator

from am_common.app import AppResources, DbSession
from am_common.auth import Principal, Role, require_any_role
from am_common.events import Actor
from am_common.service_token import ServiceToken, realm_token_url
from limits.clients import (
    BookingClient,
    Fund,
    PortfolioClient,
    RefdataClient,
    ServiceRefusedError,
    ServiceUnavailableError,
)
from limits.desk import BreachView, CheckView, DayView, Desk, ReportView, SetRecord
from limits.engine import CheckResult, Status, Totals
from limits.errors import NotFoundError, RuleViolationError
from limits.model import SetBody
from limits.settings import LimitsSettings
from limits.whatif import Change, Trade

# Лимиты смотрят трейдинг, сопровождение и риск-менеджмент; набор ведёт риск-менеджмент.
Viewer = Annotated[Principal, Depends(require_any_role(Role.TRADING, Role.OPERATIONS, Role.RISK))]
Risk = Annotated[Principal, Depends(require_any_role(Role.RISK))]
# Проверку сделки до ввода видят трейдинг и риск-менеджмент.
Trader = Annotated[Principal, Depends(require_any_role(Role.TRADING, Role.RISK))]
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
    "keycloak": "Вход служебной учётной записи лимитов",
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
        service_token=_service_token(request, resources, settings),
    )


def _service_token(
    request: Request, resources: AppResources, settings: LimitsSettings
) -> ServiceToken | None:
    """Токен am-limits — один на приложение: Keycloak не спрашиваем на каждую проверку."""
    secret = settings.keycloak_client_secret
    if secret is None:
        return None
    state = request.app.state
    if getattr(state, "service_token", None) is None:
        state.service_token = ServiceToken(
            resources.http,
            token_url=realm_token_url(settings.keycloak_url, settings.keycloak_realm),
            client_id=settings.keycloak_client_id,
            client_secret=secret.get_secret_value(),
        )
    return cast(ServiceToken, state.service_token)


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


class TradeIn(BaseModel):
    """Сделка из формы — ещё не введённая."""

    fund_id: UUID
    kind: Literal["outright", "repo"]
    side: Literal["buy", "sell"]  # у РЕПО: buy — обратное, sell — прямое
    amount: Annotated[Decimal, Field(gt=0)]  # у РЕПО — сумма первой части
    instrument_id: UUID | None = None
    secid: Annotated[str, StringConstraints(max_length=50)] = ""
    quantity: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def _paper(self) -> TradeIn:
        if self.kind == "outright" and self.instrument_id is None:
            raise ValueError("У покупки и продажи нужна бумага.")
        return self


class ChangeOut(BaseModel):
    code: str
    name: str
    label: str | None
    type: str
    metric: str | None
    group: str | None  # группа бумаги сделки: эмитент, выпуск, все бумаги проверки
    before: Decimal | None
    after: Decimal | None
    before_status: Status
    after_status: Status
    maximum: Decimal | None
    minimum: Decimal | None
    reasons: list[str]


class WhatIfOut(BaseModel):
    fund_id: UUID  # код фонда трейдер может не видеть: доступ бывает только к портфелю
    date: date
    limit_set: SetHeader | None  # нет набора — проверять нечем
    calculated: bool  # есть ли расчёт фонда за сегодня
    before: TotalsOut | None
    after: TotalsOut | None
    changes: list[ChangeOut]  # только проверки, которые сделка меняет


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


@router.post("/check", tags=["limits"])
async def check(
    body: TradeIn, _user: Trader, desk: Desks, authorization: Authorization
) -> WhatIfOut:
    """Лимиты после сделки, «что если»: только информация — сделку не запрещает."""
    trade = Trade(
        side=body.side,
        amount=body.amount,
        instrument_id=body.instrument_id if body.kind == "outright" else None,
        secid=body.secid,
        quantity=body.quantity,
    )
    return _what_if_out(await desk.check(body.fund_id, trade, authorization))


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

    async def refused(_request: Request, exc: Exception) -> Response:
        service = cast(ServiceRefusedError, exc).service
        return problem(
            403,
            f"{service}_refused",
            f"{SERVICE_NAMES.get(service, service)} не отдал данные фонда: к ним нет доступа.",
        )

    app.add_exception_handler(RuleViolationError, rule_violation)
    app.add_exception_handler(NotFoundError, not_found)
    app.add_exception_handler(ServiceUnavailableError, unavailable)
    app.add_exception_handler(ServiceRefusedError, refused)


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
        totals=None if usage is None else _totals_out(usage.totals),
        checks=[] if usage is None else [_check_out(c) for c in usage.checks],
        open_breaches=view.open_breaches,
        overdue_breaches=view.overdue_breaches,
    )


def _totals_out(totals: Totals) -> TotalsOut:
    return TotalsOut(
        assets=totals.assets.quantize(TWO),
        liabilities=totals.liabilities.quantize(TWO),
        nav=totals.nav.quantize(TWO),
        leverage=_round(totals.leverage),
        duration=_round(totals.duration),
    )


def _change_out(change: Change) -> ChangeOut:
    found = change.check
    return ChangeOut(
        code=found.code,
        name=found.name,
        label=found.label,
        type=found.type,
        metric=found.metric,
        group=change.group,
        before=_round(change.before),
        after=_round(change.after),
        before_status=change.before_status,
        after_status=change.after_status,
        maximum=found.maximum,
        minimum=found.minimum,
        reasons=change.reasons,
    )


def _what_if_out(view: CheckView) -> WhatIfOut:
    return WhatIfOut(
        fund_id=view.fund_id,
        date=view.day,
        limit_set=None if view.limit_set is None else SetHeader(**_header(view.limit_set)),
        calculated=view.before is not None,
        before=None if view.before is None else _totals_out(view.before.totals),
        after=None if view.after is None else _totals_out(view.after.totals),
        changes=[_change_out(c) for c in view.changes],
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
