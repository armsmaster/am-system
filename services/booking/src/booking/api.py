"""HTTP API сделок и позиций. Правила — в docs/design/05-booking.md."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, Field, StringConstraints

from am_common.app import AppResources, DbSession
from am_common.auth import Principal, Role, require_any_role
from am_common.clock import MOSCOW
from booking.clients import (
    MarketdataClient,
    PortfolioClient,
    RefdataClient,
    ServiceUnavailableError,
    physical_under,
    service_token,
)
from booking.queries import PortfolioState, PositionLine, positions
from booking.repository import NotFoundError, Repository
from booking.roll import Side
from booking.settings import BookingSettings
from booking.trades import Defaults, RuleViolationError, TradeDesk, TradeInput, TradeView

# Сделки вводят все авторизованные пользователи (решение 25); скрипт цен — нет.
Staff = Annotated[Principal, Depends(require_any_role(Role.TRADING, Role.OPERATIONS))]
Operations = Annotated[Principal, Depends(require_any_role(Role.OPERATIONS))]
Authorization = Annotated[str, Header(alias="Authorization")]
Comment = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

router = APIRouter()


class Clients:
    def __init__(self, resources: AppResources) -> None:
        settings = cast(BookingSettings, resources.settings)
        token = service_token(resources)
        self.refdata = RefdataClient(resources.http, settings.refdata_url, token)
        self.marketdata = MarketdataClient(resources.http, settings.marketdata_url, token)
        self.portfolios = PortfolioClient(resources.http, settings.portfolio_url)


def get_clients(request: Request, resources: AppResources) -> Clients:
    """Клиенты живут всё время работы сервиса: они хранят токен и кэш календаря."""
    clients: Clients | None = getattr(request.app.state, "clients", None)
    if clients is None:
        clients = request.app.state.clients = Clients(resources)
    return clients


def get_now(request: Request) -> datetime:
    clock: Callable[[], datetime] = request.app.state.clock
    return clock()


Services = Annotated[Clients, Depends(get_clients)]
Now = Annotated[datetime, Depends(get_now)]


def desk(
    session: DbSession,
    principal: Principal,
    authorization: str,
    services: Clients,
    now: datetime,
) -> TradeDesk:
    return TradeDesk(
        session,
        principal,
        authorization,
        refdata=services.refdata,
        marketdata=services.marketdata,
        portfolios=services.portfolios,
        clock=lambda: now,
    )


# --- схемы ----------------------------------------------------------------------


class TradeIn(BaseModel):
    portfolio_id: UUID
    instrument_id: UUID
    side: Side
    quantity: Annotated[int, Field(gt=0, le=10**12)]
    price: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
    trade_at: AwareDatetime
    settle_date: date
    face_value: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)] | None = None
    accrued: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)] | None = None
    fee: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)] = Decimal(0)
    venue: Literal["exchange", "otc"] = "exchange"
    counterparty: Text | None = None
    external_id: Text | None = None
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None

    def entry(self) -> TradeInput:
        return TradeInput(**self.model_dump())


class AmendIn(TradeIn):
    comment: Comment  # исправление — всегда с причиной


class CommentIn(BaseModel):
    comment: Comment


class ConfirmIn(BaseModel):
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class VersionOut(BaseModel):
    number: int
    status: str
    author: str
    created_at: datetime
    comment: str | None
    changed: list[str]  # какие поля изменились относительно предыдущей версии


class TradeOut(BaseModel):
    id: UUID
    number: int
    status: str
    portfolio_id: UUID
    portfolio_code: str | None
    instrument_id: UUID
    instrument_kind: str
    secid: str
    short_name: str
    side: Side
    quantity: int
    price: Decimal
    face_value: Decimal | None
    accrued: Decimal | None
    amount: Decimal
    fee: Decimal
    trade_at: datetime
    trade_date: date
    settle_date: date
    venue: str
    counterparty: str | None
    external_id: str | None
    comment: str | None
    author: str
    created_at: datetime
    versions: list[VersionOut] = []
    warnings: list[str] = []


class DefaultsOut(BaseModel):
    instrument_id: UUID
    kind: str
    secid: str
    short_name: str
    settle_date: date
    face_value: Decimal | None
    accrued: Decimal | None


class LotOut(BaseModel):
    portfolio_code: str
    trade_id: UUID
    opened_at: datetime
    quantity: int
    price: Decimal
    face: Decimal | None
    cost: Decimal
    accrued_paid: Decimal


class LineOut(BaseModel):
    instrument_id: UUID
    kind: str
    secid: str
    short_name: str
    trade_quantity: int
    settle_quantity: int
    cost: Decimal
    accrued_paid: Decimal
    average_price: Decimal | None
    lots: list[LotOut]


class MovementOut(BaseModel):
    kind: str
    instrument_id: UUID
    trade_id: UUID | None
    quantity: int
    cash: Decimal


class PortfolioStateOut(BaseModel):
    id: UUID
    code: str
    name: str
    as_of: date | None
    waiting_since: date | None
    cash: Decimal
    movements: list[MovementOut]


class PositionsOut(BaseModel):
    portfolio_id: UUID
    code: str
    name: str
    date: date
    cash: Decimal
    portfolios: list[PortfolioStateOut]
    lines: list[LineOut]


# --- сделки ---------------------------------------------------------------------


@router.get("/trade-defaults", tags=["trades"])
async def trade_defaults(
    user: Staff,
    *,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
    instrument_id: UUID,
    trade_date: date,
) -> DefaultsOut:
    """Подсказка для формы: дата расчётов T+1, номинал и НКД на неё (решение 86)."""
    found: Defaults = await desk(session, user, authorization, services, now).defaults(
        instrument_id, trade_date
    )
    return DefaultsOut.model_validate(found, from_attributes=True)


@router.post("/trades", status_code=201, tags=["trades"])
async def enter_trade(
    body: TradeIn,
    *,
    user: Staff,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
) -> TradeOut:
    view = await desk(session, user, authorization, services, now).enter(body.entry())
    return _trade_out(view)


@router.get("/trades", tags=["trades"])
async def list_trades(
    user: Staff,
    *,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
    portfolio_id: UUID,
    day: Annotated[date | None, Query(alias="date")] = None,
) -> list[TradeOut]:
    """Сделки за день по портфелю; compound — по всем его physical-портфелям."""
    visible = await services.portfolios.visible(authorization)
    physical = {p.id: p for p in physical_under(portfolio_id, visible)}
    rows = await Repository(session).current(
        portfolio_ids=physical, trade_date=day or now.astimezone(MOSCOW).date()
    )
    return [_version_out(row, physical[row.portfolio_id].code) for row in rows]


@router.get("/trades/{trade_id}", tags=["trades"])
async def get_trade(
    trade_id: UUID,
    *,
    user: Staff,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
) -> TradeOut:
    return _trade_out(await desk(session, user, authorization, services, now).view(trade_id))


@router.post("/trades/{trade_id}/versions", tags=["trades"])
async def amend_trade(
    trade_id: UUID,
    *,
    body: AmendIn,
    user: Staff,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
) -> TradeOut:
    """Исправление — новая версия сделки; подтверждение снимается (решение 95)."""
    view = await desk(session, user, authorization, services, now).amend(trade_id, body.entry())
    return _trade_out(view)


@router.post("/trades/{trade_id}/cancel", tags=["trades"])
async def cancel_trade(
    trade_id: UUID,
    *,
    body: CommentIn,
    user: Staff,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
) -> TradeOut:
    view = await desk(session, user, authorization, services, now).cancel(trade_id, body.comment)
    return _trade_out(view)


@router.post("/trades/{trade_id}/confirm", tags=["trades"])
async def confirm_trade(
    trade_id: UUID,
    *,
    body: ConfirmIn,
    user: Operations,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
) -> TradeOut:
    """Сопровождение сверило сделку с подтверждением брокера."""
    view = await desk(session, user, authorization, services, now).confirm(trade_id, body.comment)
    return _trade_out(view)


# --- позиции --------------------------------------------------------------------


@router.get("/positions", tags=["positions"])
async def get_positions(
    _user: Staff,
    *,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
    portfolio_id: UUID,
    day: Annotated[date | None, Query(alias="date")] = None,
) -> PositionsOut:
    """Позиции на конец дня: по дате сделки и по дате расчётов, лоты FIFO, деньги."""
    visible = await services.portfolios.visible(authorization)
    on = day or now.astimezone(MOSCOW).date()
    view = await positions(session, visible, portfolio_id, on)
    if view is None:
        raise NotFoundError("portfolio", portfolio_id)
    return PositionsOut(
        portfolio_id=view.portfolio.id,
        code=view.portfolio.code,
        name=view.portfolio.name,
        date=view.day,
        cash=view.cash,
        portfolios=[_state_out(p) for p in view.portfolios],
        lines=[_line_out(line) for line in view.lines],
    )


# --- ошибки → ответы HTTP -------------------------------------------------------


def install_error_handlers(app: FastAPI) -> None:
    def problem(status_code: int, code: str, message: str) -> Response:
        return JSONResponse(
            status_code=status_code, content={"detail": {"code": code, "message": message}}
        )

    async def rule_violation(_request: Request, exc: Exception) -> Response:
        violation = cast(RuleViolationError, exc)
        return problem(violation.status, violation.code, violation.message)

    async def not_found(_request: Request, exc: Exception) -> Response:
        missing = cast(NotFoundError, exc)
        return problem(404, "not_found", f"Не найдено: {missing.what}")

    async def unavailable(_request: Request, exc: Exception) -> Response:
        service = cast(ServiceUnavailableError, exc).service
        names = {
            "refdata": "Справочник",
            "marketdata": "Сервис цен",
            "portfolio": "Сервис портфелей",
        }
        return problem(
            503, f"{service}_unavailable", f"{names[service]} недоступен. Повторите позже."
        )

    app.add_exception_handler(RuleViolationError, rule_violation)
    app.add_exception_handler(NotFoundError, not_found)
    app.add_exception_handler(ServiceUnavailableError, unavailable)


# --- вспомогательное ------------------------------------------------------------

TRACKED = (
    "portfolio_id",
    "instrument_id",
    "side",
    "quantity",
    "price",
    "face_value",
    "accrued",
    "fee",
    "trade_at",
    "settle_date",
    "venue",
    "counterparty",
    "external_id",
)


def _version_out(row: Any, portfolio_code: str | None) -> TradeOut:
    return TradeOut(
        id=row.trade_id,
        portfolio_code=portfolio_code,
        **{name: getattr(row, name) for name in TradeOut.model_fields if hasattr(row, name)},
    )


def _trade_out(view: TradeView) -> TradeOut:
    out = _version_out(view.current, None if view.portfolio is None else view.portfolio.code)
    previous: Any = None
    versions: list[VersionOut] = []
    for row in view.versions:
        changed = (
            []
            if previous is None
            else [name for name in TRACKED if getattr(row, name) != getattr(previous, name)]
        )
        versions.append(
            VersionOut(
                number=row.number,
                status=row.status,
                author=row.author,
                created_at=row.created_at,
                comment=row.comment,
                changed=changed,
            )
        )
        previous = row
    return out.model_copy(update={"versions": versions, "warnings": view.warnings})


def _state_out(state: PortfolioState) -> PortfolioStateOut:
    return PortfolioStateOut(
        id=state.portfolio.id,
        code=state.portfolio.code,
        name=state.portfolio.name,
        as_of=state.as_of,
        waiting_since=state.waiting_since,
        cash=state.cash,
        movements=[
            MovementOut(
                kind=m.kind.value,
                instrument_id=m.instrument_id,
                trade_id=m.trade_id,
                quantity=m.quantity,
                cash=m.cash,
            )
            for m in state.movements
        ],
    )


def _line_out(line: PositionLine) -> LineOut:
    average = line.average_price
    return LineOut(
        instrument_id=line.instrument_id,
        kind=line.kind,
        secid=line.secid,
        short_name=line.short_name,
        trade_quantity=line.trade_quantity,
        settle_quantity=line.settle_quantity,
        cost=line.cost.quantize(Decimal("0.01")),
        accrued_paid=line.accrued_paid.quantize(Decimal("0.01")),
        average_price=None if average is None else average.quantize(Decimal("0.000001")),
        lots=[
            LotOut(
                portfolio_code=code,
                trade_id=lot.trade_id,
                opened_at=lot.opened_at,
                quantity=lot.quantity,
                price=lot.price,
                face=lot.face,
                cost=lot.cost.quantize(Decimal("0.01")),
                accrued_paid=lot.accrued_paid.quantize(Decimal("0.01")),
            )
            for code, lot in line.lots
        ],
    )
