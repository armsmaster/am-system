"""HTTP API сделок, импорта, операций фонда, позиций и лестницы. Правила — в design/05."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from am_common.app import AppResources, DbSession
from am_common.auth import Principal, Role, require_any_role
from am_common.clock import MOSCOW
from booking.clients import (
    MarketdataClient,
    PortfolioClient,
    PortfolioRef,
    RefdataClient,
    ServiceUnavailableError,
    physical_under,
    service_token,
)
from booking.imports import ImportItem, Result, TradeImport
from booking.ladder import Ladder, ladder
from booking.operations import OperationDesk, OperationInput, OperationView
from booking.queries import PortfolioState, PositionLine, positions
from booking.repository import NotFoundError, Repository
from booking.roll import Movement, OperationKind, Side
from booking.settings import BookingSettings
from booking.trades import (
    TRACKED,
    Defaults,
    RuleViolationError,
    TradeDesk,
    TradeInput,
    TradeView,
)

# Сделки вводят все авторизованные пользователи (решение 25); скрипт цен — нет.
Staff = Annotated[Principal, Depends(require_any_role(Role.TRADING, Role.OPERATIONS))]
Operations = Annotated[Principal, Depends(require_any_role(Role.OPERATIONS))]
# Импорт — пользователям и скрипту сделок (спецификация docs/api/trade-import).
Feeder = Annotated[
    Principal, Depends(require_any_role(Role.TRADING, Role.OPERATIONS, Role.TRADE_FEED))
]
Authorization = Annotated[str, Header(alias="Authorization")]
Comment = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
OptionalComment = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None
Money = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
Price = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
Kopecks = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
Quantity = Annotated[int, Field(gt=0, le=10**12)]

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


def operation_desk(
    session: DbSession,
    principal: Principal,
    authorization: str,
    services: Clients,
    now: datetime,
) -> OperationDesk:
    return OperationDesk(
        session,
        principal,
        authorization,
        refdata=services.refdata,
        portfolios=services.portfolios,
        clock=lambda: now,
    )


# --- схемы ----------------------------------------------------------------------


class TradeIn(BaseModel):
    portfolio_id: UUID
    instrument_id: UUID
    side: Side
    quantity: Quantity
    price: Price
    trade_at: AwareDatetime
    settle_date: date
    face_value: Price | None = None
    accrued: Kopecks | None = None
    fee: Kopecks = Decimal(0)
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
    instrument_id: UUID | None
    trade_id: UUID | None
    operation_id: UUID | None
    quantity: int
    cash: Decimal


class FundFigures(BaseModel):
    """Паи, требования и обязательства — у казначейского портфеля фонда."""

    units: Decimal
    claims: Decimal  # к пайщикам: паи выданы, деньги не пришли
    obligations: Decimal  # перед пайщиками: паи погашены, деньги не ушли
    expenses: Decimal  # начисленные и не выплаченные расходы


class PortfolioStateOut(FundFigures):
    id: UUID
    code: str
    name: str
    as_of: date | None
    waiting_since: date | None
    cash: Decimal
    movements: list[MovementOut]


class PositionsOut(FundFigures):
    portfolio_id: UUID
    code: str
    name: str
    date: date
    cash: Decimal
    portfolios: list[PortfolioStateOut]
    lines: list[LineOut]


class _OperationBase(BaseModel):
    op_date: date
    comment: OptionalComment = None


class UnitsIn(_OperationBase):
    """Выдача или погашение паёв: паи с 5 знаками и сумма (вопрос 50)."""

    kind: Literal["unit_issue", "unit_redemption"]
    fund_id: UUID
    units: Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=5)]
    amount: Money
    planned_date: date


class ExpenseIn(_OperationBase):
    kind: Literal["expense_accrual", "expense_payment"]
    fund_id: UUID
    expense_kind: Literal["specdep", "registrar", "auditor", "exchange_broker", "other"]
    amount: Money


class CashTransferIn(_OperationBase):
    kind: Literal["cash_transfer"]
    portfolio_id: UUID
    receiver_id: UUID
    amount: Money


class SecurityTransferIn(_OperationBase):
    kind: Literal["security_transfer"]
    portfolio_id: UUID
    receiver_id: UUID
    instrument_id: UUID
    quantity: Annotated[int, Field(gt=0, le=10**12)]


OperationIn = Annotated[
    UnitsIn | ExpenseIn | CashTransferIn | SecurityTransferIn, Field(discriminator="kind")
]


class SettleIn(BaseModel):
    settled_date: date
    comment: OptionalComment = None


class OperationOut(BaseModel):
    id: UUID
    number: int
    status: str
    kind: OperationKind
    fund_id: UUID
    portfolio_id: UUID
    portfolio_code: str | None
    receiver_id: UUID | None
    receiver_code: str | None
    op_date: date
    amount: Decimal | None
    units: Decimal | None
    planned_date: date | None
    settled_date: date | None
    expense_kind: str | None
    instrument_id: UUID | None
    secid: str | None
    short_name: str | None
    quantity: int | None
    comment: str | None
    author: str
    created_at: datetime
    versions: list[VersionOut] = []
    warnings: list[str] = []


class ImportTradeIn(BaseModel):
    """Сделка пакета: поля — в docs/api/trade-import/README.md."""

    model_config = ConfigDict(extra="forbid")

    external_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
    ]
    portfolio: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=20)]
    side: Side
    quantity: Quantity
    price: Price
    trade_at: AwareDatetime
    settle_date: date
    accrued: Kopecks | None = None
    face_value: Price | None = None
    fee: Kopecks = Decimal(0)
    venue: Literal["exchange", "otc"] = "exchange"
    counterparty: Text | None = None
    comment: OptionalComment = None
    cancelled: bool = False


class TradeBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_-]{1,32}$")]
    trades: Annotated[list[ImportTradeIn], Field(min_length=1, max_length=5000)]


class ImportedOut(BaseModel):
    index: int
    external_id: str
    result: Result
    trade_id: UUID
    warnings: list[str]


class ImportRejectedOut(BaseModel):
    index: int
    external_id: str
    error: str
    message: str


class TradeBatchOut(BaseModel):
    created: int
    amended: int
    cancelled: int
    unchanged: int
    trades: list[ImportedOut]
    rejected: list[ImportRejectedOut]


class LadderMovementOut(MovementOut):
    portfolio_code: str


class LadderSecurityOut(BaseModel):
    instrument_id: UUID
    quantity: int  # на счёте депо на конец дня; меньше нуля — дефицит


class LadderDayOut(BaseModel):
    date: date
    trading: bool
    movements: list[LadderMovementOut]
    cash: Decimal  # меньше нуля — дефицит денег
    securities: list[LadderSecurityOut]


class InstrumentOut(BaseModel):
    instrument_id: UUID
    secid: str
    short_name: str


class LadderPortfolioOut(BaseModel):
    id: UUID
    code: str
    name: str


class LadderOut(BaseModel):
    account_id: UUID
    portfolios: list[LadderPortfolioOut]
    as_of: date | None
    waiting_since: date | None
    instruments: list[InstrumentOut]
    days: list[LadderDayOut]


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


# --- импорт сделок --------------------------------------------------------------


@router.post("/feed/v1/trades", tags=["import"])
async def import_trades(
    body: TradeBatchIn,
    *,
    user: Feeder,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
) -> TradeBatchOut:
    """Пакет сделок от внешней системы или из файла: ключ — источник и номер сделки."""
    trades = desk(session, user, authorization, services, now)
    report = await TradeImport(session, trades, services.refdata).run(
        body.source, [ImportItem(**item.model_dump()) for item in body.trades]
    )
    return TradeBatchOut(
        created=report.count(Result.CREATED),
        amended=report.count(Result.AMENDED),
        cancelled=report.count(Result.CANCELLED),
        unchanged=report.count(Result.UNCHANGED),
        trades=[ImportedOut.model_validate(t, from_attributes=True) for t in report.trades],
        rejected=[
            ImportRejectedOut.model_validate(r, from_attributes=True) for r in report.rejected
        ],
    )


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
        units=view.units,
        claims=view.claims,
        obligations=view.obligations,
        expenses=view.expenses,
        portfolios=[_state_out(p) for p in view.portfolios],
        lines=[_line_out(line) for line in view.lines],
    )


# --- операции фонда и переводы --------------------------------------------------


@router.post("/operations", status_code=201, tags=["operations"])
async def enter_operation(
    body: OperationIn,
    *,
    user: Operations,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
) -> OperationOut:
    """Паи и расходы — в казначейский портфель фонда; перевод — внутри фонда."""
    entry = OperationInput(**(body.model_dump() | {"kind": OperationKind(body.kind)}))
    view = await operation_desk(session, user, authorization, services, now).enter(entry)
    return _operation_out(view)


@router.get("/operations", tags=["operations"])
async def list_operations(
    _user: Staff,
    *,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    portfolio_id: UUID,
    day: Annotated[date | None, Query(alias="date")] = None,
) -> list[OperationOut]:
    """Операции портфеля, фонда или compound — по всем его physical; новые сверху."""
    visible = await services.portfolios.visible(authorization)
    physical = {p.id for p in physical_under(portfolio_id, visible)}
    rows = await Repository(session).operations(portfolio_ids=physical, op_date=day)
    return [_operation_row_out(row, visible) for row in reversed(rows)]


@router.get("/operations/{operation_id}", tags=["operations"])
async def get_operation(
    operation_id: UUID,
    *,
    user: Staff,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
) -> OperationOut:
    desk_ = operation_desk(session, user, authorization, services, now)
    return _operation_out(await desk_.view(operation_id))


@router.post("/operations/{operation_id}/settle", tags=["operations"])
async def settle_operation(
    operation_id: UUID,
    *,
    body: SettleIn,
    user: Operations,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
) -> OperationOut:
    """Отметка расчёта по паям: деньги пришли или ушли (решение 42)."""
    desk_ = operation_desk(session, user, authorization, services, now)
    return _operation_out(await desk_.settle(operation_id, body.settled_date, body.comment))


@router.post("/operations/{operation_id}/cancel", tags=["operations"])
async def cancel_operation(
    operation_id: UUID,
    *,
    body: CommentIn,
    user: Operations,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
) -> OperationOut:
    desk_ = operation_desk(session, user, authorization, services, now)
    return _operation_out(await desk_.cancel(operation_id, body.comment))


# --- лестница расчётов ----------------------------------------------------------


@router.get("/ladder", tags=["positions"])
async def get_ladder(
    _user: Staff,
    *,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
    account_id: UUID,
) -> LadderOut:
    """Деньги и бумаги на счёте на каждую дату расчётов от сегодня, дефициты."""
    visible = await services.portfolios.visible(authorization)
    found = await ladder(
        session, services.refdata, visible, account_id, now.astimezone(MOSCOW).date()
    )
    if found is None:
        raise NotFoundError("account", account_id)
    return _ladder_out(found)


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


def _movement_out(m: Movement) -> MovementOut:
    return MovementOut(
        kind=m.kind.value,
        instrument_id=m.instrument_id,
        trade_id=m.trade_id,
        operation_id=m.operation_id,
        quantity=m.quantity,
        cash=m.cash,
    )


def _state_out(state: PortfolioState) -> PortfolioStateOut:
    return PortfolioStateOut(
        id=state.portfolio.id,
        code=state.portfolio.code,
        name=state.portfolio.name,
        as_of=state.as_of,
        waiting_since=state.waiting_since,
        cash=state.cash,
        units=state.units,
        claims=state.claims,
        obligations=state.obligations,
        expenses=state.expenses,
        movements=[_movement_out(m) for m in state.movements],
    )


OPERATION_TRACKED = ("status", "settled_date")


def _operation_row_out(row: Any, visible: Mapping[UUID, PortfolioRef]) -> OperationOut:
    def code(portfolio_id: UUID | None) -> str | None:
        found = None if portfolio_id is None else visible.get(portfolio_id)
        return None if found is None else found.code

    return OperationOut(
        id=row.operation_id,
        portfolio_code=code(row.portfolio_id),
        receiver_code=code(row.receiver_id),
        **{name: getattr(row, name) for name in OperationOut.model_fields if hasattr(row, name)},
    )


def _operation_out(view: OperationView) -> OperationOut:
    out = _operation_row_out(view.current, view.visible)
    previous: Any = None
    versions: list[VersionOut] = []
    for row in view.versions:
        changed = (
            []
            if previous is None
            else [
                name for name in OPERATION_TRACKED if getattr(row, name) != getattr(previous, name)
            ]
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


def _ladder_out(found: Ladder) -> LadderOut:
    codes = {p.id: p.code for p in found.portfolios}
    return LadderOut(
        account_id=found.account_id,
        portfolios=[
            LadderPortfolioOut(id=p.id, code=p.code, name=p.name) for p in found.portfolios
        ],
        as_of=found.as_of,
        waiting_since=found.waiting_since,
        instruments=sorted(
            (
                InstrumentOut(instrument_id=i, secid=row.secid, short_name=row.short_name)
                for i, row in found.names.items()
            ),
            key=lambda item: item.secid,
        ),
        days=[
            LadderDayOut(
                date=d.day,
                trading=d.trading,
                cash=d.cash,
                movements=[
                    LadderMovementOut(
                        portfolio_code=codes[portfolio_id], **_movement_out(m).model_dump()
                    )
                    for portfolio_id, m in d.movements
                ],
                securities=[
                    LadderSecurityOut(instrument_id=i, quantity=q) for i, q in d.securities.items()
                ],
            )
            for d in found.days
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
