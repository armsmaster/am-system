"""HTTP API сверки: приём выписок, история, сверка фонда за день, принятие. Правила — design/08."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints, model_validator

from am_common.app import AppResources, DbSession
from am_common.auth import Principal, Role, require_any_role
from reconciliation.clients import (
    BookingClient,
    PortfolioClient,
    RefdataClient,
    ServiceUnavailableError,
)
from reconciliation.matching import (
    BY_DAY,
    BY_SECURITY,
    BY_TRADE,
    BalanceLine,
    MovementLine,
    OpenItem,
    Statement,
    TradeLine,
)
from reconciliation.settings import ReconciliationSettings
from reconciliation.statements import (
    KSU_CODES,
    AccountReconciliation,
    Desk,
    MovementEntry,
    NotFoundError,
    Received,
    Result,
    RuleViolationError,
    StatementEntry,
    TradeEntry,
    parse,
)

# Сверку смотрят трейдинг и сопровождение; вводит выписки и принимает — сопровождение.
Staff = Annotated[Principal, Depends(require_any_role(Role.TRADING, Role.OPERATIONS))]
Operations = Annotated[Principal, Depends(require_any_role(Role.OPERATIONS))]
# Пакет выписок — от скрипта брокера (спецификация docs/api/statement-feed).
Feeder = Annotated[Principal, Depends(require_any_role(Role.STATEMENT_FEED))]
Authorization = Annotated[str, Header(alias="Authorization")]
Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
Comment = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None
Cash = Annotated[Decimal, Field(max_digits=20, decimal_places=2)]
Money = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=2)]
Kopecks = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
Price = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=6)]
Rate = Annotated[Decimal, Field(ge=0, le=100, max_digits=9, decimal_places=6)]
MAX_LINES = 5000

router = APIRouter()


class Clients:
    def __init__(self, resources: AppResources) -> None:
        settings = cast(ReconciliationSettings, resources.settings)
        self.portfolios = PortfolioClient(resources.http, settings.portfolio_url)
        self.booking = BookingClient(resources.http, settings.booking_url)
        self.refdata = RefdataClient(resources.http, settings.refdata_url)


def get_clients(request: Request, resources: AppResources) -> Clients:
    clients: Clients | None = getattr(request.app.state, "clients", None)
    if clients is None:
        clients = request.app.state.clients = Clients(resources)
    return clients


Services = Annotated[Clients, Depends(get_clients)]


def desk(
    request: Request,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    principal: Principal,
) -> Desk:
    return Desk(
        session,
        principal,
        authorization,
        portfolios=services.portfolios,
        booking=services.booking,
        refdata=services.refdata,
        clock=request.app.state.clock,
    )


# --- схемы ----------------------------------------------------------------------


class StatementTradeIn(BaseModel):
    """Сделка дня из отчёта брокера — поля как в импорте сделок."""

    external_id: Code  # номер сделки биржи
    kind: Literal["outright", "repo"] = "outright"
    side: Literal["buy", "sell"]
    security: Code  # SECID или ISIN; KSU — РЕПО с КСУ
    settle_date: date
    quantity: Annotated[int, Field(ge=0, le=10**12)] = 0
    price: Price = Decimal(0)
    accrued: Kopecks | None = None  # НКД на бумагу
    amount: Money
    fee: Kopecks = Decimal(0)
    repo_rate: Rate | None = None
    second_date: date | None = None
    second_amount: Money | None = None

    @model_validator(mode="after")
    def _fields_of_kind(self) -> StatementTradeIn:
        repo = (self.repo_rate, self.second_date, self.second_amount)
        if self.kind == "repo" and None in repo:
            raise ValueError("у РЕПО нужны ставка, дата и сумма второй части")
        if self.kind == "outright" and any(v is not None for v in repo):
            raise ValueError("ставка и вторая часть бывают только у РЕПО")
        ksu = self.security.upper() in KSU_CODES
        if ksu and (self.quantity or self.price):
            raise ValueError("у РЕПО с КСУ нет количества и цены — только сумма")
        if not ksu and (self.quantity <= 0 or self.price <= 0):
            raise ValueError("у сделки с бумагой нужны количество и цена")
        return self


class StatementMovementIn(BaseModel):
    kind: Literal[
        "settlement",
        "fee",
        "repo_first",
        "repo_second",
        "repo_contribution",
        "coupon",
        "amortization",
        "redemption",
        "transfer",
        "unit_settlement",
        "fee_payment",
        "expense_payment",
        "other",
    ]
    amount: Cash  # + пришли, − ушли
    trade: Code | None = None  # номер сделки: расчёты, комиссия, РЕПО, взнос
    security: Code | None = None  # бумага: купон, амортизация, погашение
    comment: Note = None

    @model_validator(mode="after")
    def _key_of_kind(self) -> StatementMovementIn:
        if not self.amount:
            raise ValueError("движение на ноль рублей не бывает")
        if self.kind in BY_TRADE and (self.trade is None or self.security is not None):
            raise ValueError(f"у движения {self.kind} нужен номер сделки, бумага не нужна")
        if self.kind in BY_SECURITY and self.security is None:
            raise ValueError(f"у движения {self.kind} нужна бумага")
        if self.kind in BY_DAY and (self.trade is not None or self.security is not None):
            raise ValueError(f"у движения {self.kind} нет ни сделки, ни бумаги")
        return self


class _StatementBase(BaseModel):
    date: date
    opening_cash: Cash
    closing_cash: Cash
    trades: Annotated[list[StatementTradeIn], Field(max_length=MAX_LINES)] = []
    movements: Annotated[list[StatementMovementIn], Field(max_length=MAX_LINES)] = []


class StatementFeedIn(_StatementBase):
    account: Code  # код счёта в системе


class StatementBatchIn(BaseModel):
    source: Code
    statements: Annotated[list[StatementFeedIn], Field(min_length=1, max_length=1000)]


class StatementIn(_StatementBase):
    """Выписка вручную: по выписке банка или отчёту брокера."""

    account_id: UUID


class ReceivedOut(BaseModel):
    index: int
    account: str
    date: date
    statement_id: UUID
    result: Result


class RejectedOut(BaseModel):
    index: int
    account: str | None
    date: date
    error: str
    message: str


class BatchOut(BaseModel):
    batch_id: UUID
    created: int
    replaced: int
    unchanged: int
    statements: list[ReceivedOut]
    rejected: list[RejectedOut]


class StatementOut(BaseModel):
    id: UUID
    batch_id: UUID | None = None
    source: str
    date: date
    opening_cash: Decimal
    closing_cash: Decimal
    trades: list[dict[str, Any]]
    movements: list[dict[str, Any]]
    received_by: str
    received_at: datetime


class AcceptanceOut(BaseModel):
    comment: str
    accepted_by: str
    accepted_at: datetime


class DifferenceOut(BaseModel):
    field: str
    system: str | None
    statement: str | None


class TradeLineOut(BaseModel):
    line: str
    status: str
    system: dict[str, Any] | None
    statement: dict[str, Any] | None
    differences: list[DifferenceOut]
    acceptance: AcceptanceOut | None


class MovementLineOut(BaseModel):
    line: str
    status: str
    kind: str
    trade: str | None
    instrument_id: UUID | None
    secid: str | None
    system: Decimal | None
    statement: Decimal | None
    discrepancy: Decimal
    comments: list[str]
    paired: date | None
    acceptance: AcceptanceOut | None


class BalanceLineOut(BaseModel):
    line: str
    status: str
    system: Decimal
    statement: Decimal
    discrepancy: Decimal
    expected: Decimal
    acceptance: AcceptanceOut | None


class WaitingOut(BaseModel):
    date: date
    line: str
    kind: str
    secid: str | None
    trade: str | None
    discrepancy: Decimal
    comment: str


class PreviousOut(BaseModel):
    date: date
    closing_cash: Decimal


class AccountDayOut(BaseModel):
    account_id: UUID
    code: str
    name: str
    cash_kind: str
    institution: str
    portfolios: list[str]
    status: str
    waiting_since: date | None
    trading: bool
    calculated: bool
    system_opening: Decimal
    system_closing: Decimal
    statement: StatementOut | None
    previous: PreviousOut | None
    trades: list[TradeLineOut]
    movements: list[MovementLineOut]
    opening: BalanceLineOut | None
    closing: BalanceLineOut | None
    waiting: list[WaitingOut]


class FundDayOut(BaseModel):
    fund_id: UUID
    code: str
    name: str
    date: date
    trading: bool
    reconciled: bool
    accounts: list[AccountDayOut]


class AcceptIn(BaseModel):
    account_id: UUID
    date: date
    line: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    comment: Comment


# --- выписки --------------------------------------------------------------------


@router.post("/feed/v1/statements", tags=["statements"])
async def receive_statements(
    body: StatementBatchIn,
    request: Request,
    *,
    user: Feeder,
    session: DbSession,
    authorization: Authorization,
    services: Services,
) -> BatchOut:
    """Пакет выписок от скрипта брокера: каждая принимается или отклоняется отдельно."""
    payload = await request.json()
    report = await desk(request, session, authorization, services, user).receive(
        body.source, [_entry(s, account=s.account) for s in body.statements], payload
    )
    return BatchOut(
        batch_id=report.batch_id,
        created=report.count(Result.CREATED),
        replaced=report.count(Result.REPLACED),
        unchanged=report.count(Result.UNCHANGED),
        statements=[_received_out(r) for r in report.received],
        rejected=[
            RejectedOut(
                index=r.index, account=r.account, date=r.day, error=r.error, message=r.message
            )
            for r in report.rejected
        ],
    )


@router.post("/statements", status_code=201, tags=["statements"])
async def enter_statement(
    body: StatementIn,
    request: Request,
    *,
    user: Operations,
    session: DbSession,
    authorization: Authorization,
    services: Services,
) -> ReceivedOut:
    """Выписка вручную; «движений нет» — та же выписка с остатком прошлой и без движений."""
    received = await desk(request, session, authorization, services, user).enter(
        body.account_id, _entry(body), body.model_dump(mode="json")
    )
    return _received_out(received)


@router.get("/statements", tags=["statements"])
async def statement_history(
    request: Request,
    _user: Staff,
    *,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    account_id: UUID,
    day: Annotated[date, Query(alias="date")],
) -> list[StatementOut]:
    """Выписки по счёту за день, от последней к первой: первая — действующая."""
    rows = await desk(request, session, authorization, services, _user).history(account_id, day)
    return [_statement_out(parse(row), batch_id=row.batch_id) for row in rows]


@router.get("/batches/{batch_id}", tags=["statements"])
async def get_batch(
    batch_id: UUID,
    request: Request,
    _user: Staff,
    *,
    session: DbSession,
    authorization: Authorization,
    services: Services,
) -> dict[str, Any]:
    """Пакет, как его прислали: источник, кто и когда, содержимое."""
    found = await desk(request, session, authorization, services, _user).batch(batch_id)
    return {
        "id": found.id,
        "source": found.source,
        "received_by": found.received_by,
        "received_at": found.received_at,
        "payload": found.payload,
    }


# --- сверка ---------------------------------------------------------------------


@router.get("/funds/{fund_id}/days/{day}", tags=["reconciliation"])
async def fund_day(
    fund_id: UUID,
    day: date,
    request: Request,
    _user: Staff,
    *,
    session: DbSession,
    authorization: Authorization,
    services: Services,
) -> FundDayOut:
    """Сверка фонда за день: по каждому счёту — сделки, движения, остатки."""
    found = await desk(request, session, authorization, services, _user).fund_day(fund_id, day)
    return FundDayOut(
        fund_id=found.fund.id,
        code=found.fund.code,
        name=found.fund.name,
        date=found.day,
        trading=found.trading,
        reconciled=found.reconciled,
        accounts=[_account_out(a) for a in found.accounts],
    )


@router.post("/acceptances", status_code=201, tags=["reconciliation"])
async def accept(
    body: AcceptIn,
    request: Request,
    *,
    user: Operations,
    session: DbSession,
    authorization: Authorization,
    services: Services,
) -> AccountDayOut:
    """Принять строку, которая не сошлась: комментарий объясняет расхождение (решение 123)."""
    found = await desk(request, session, authorization, services, user).accept(
        body.account_id, body.date, body.line, body.comment
    )
    return _account_out(found)


# --- ответы ---------------------------------------------------------------------


def _entry(body: _StatementBase, *, account: str | None = None) -> StatementEntry:
    return StatementEntry(
        day=body.date,
        opening_cash=body.opening_cash,
        closing_cash=body.closing_cash,
        account=account,
        trades=[TradeEntry(**t.model_dump()) for t in body.trades],
        movements=[MovementEntry(**m.model_dump()) for m in body.movements],
    )


def _received_out(received: Received) -> ReceivedOut:
    return ReceivedOut(
        index=received.index,
        account=received.account,
        date=received.day,
        statement_id=received.statement_id,
        result=received.result,
    )


def _statement_out(found: Statement, *, batch_id: UUID | None = None) -> StatementOut:
    return StatementOut(
        id=found.id,
        batch_id=batch_id,
        source=found.source,
        date=found.day,
        opening_cash=found.opening,
        closing_cash=found.closing,
        trades=[_plain(t) for t in found.trades],
        movements=[_plain(m) for m in found.movements],
        received_by=found.received_by,
        received_at=found.received_at,
    )


def _plain(item: Any) -> dict[str, Any]:
    return {
        name: getattr(item, name)
        if not isinstance(getattr(item, name), UUID | Decimal | date)
        else str(getattr(item, name))
        for name in item.__dataclass_fields__
    }


def _acceptance_out(line: TradeLine | MovementLine | BalanceLine) -> AcceptanceOut | None:
    found = line.acceptance
    if found is None:
        return None
    return AcceptanceOut(
        comment=found.comment, accepted_by=found.accepted_by, accepted_at=found.accepted_at
    )


def _account_out(found: AccountReconciliation) -> AccountDayOut:
    day = found.day
    return AccountDayOut(
        account_id=found.account.id,
        code=found.account.code,
        name=found.account.name,
        cash_kind=found.account.cash_kind,
        institution=found.account.institution,
        portfolios=list(found.portfolios),
        status=found.status,
        waiting_since=found.waiting_since,
        trading=day.system.trading,
        calculated=day.system.calculated,
        system_opening=day.system.opening,
        system_closing=day.system.closing,
        statement=None if day.statement is None else _statement_out(day.statement),
        previous=None
        if found.previous is None
        else PreviousOut(date=found.previous.day, closing_cash=found.previous.closing),
        trades=[
            TradeLineOut(
                line=t.line,
                status=t.status.value,
                system=None if t.system is None else _plain(t.system),
                statement=None if t.statement is None else _plain(t.statement),
                differences=[
                    DifferenceOut(field=d.field, system=d.system, statement=d.statement)
                    for d in t.differences
                ],
                acceptance=_acceptance_out(t),
            )
            for t in day.trades
        ],
        movements=[
            MovementLineOut(
                line=m.line,
                status=m.status.value,
                kind=m.key.kind,
                trade=m.key.trade,
                instrument_id=m.key.instrument_id,
                secid=m.secid,
                system=m.system,
                statement=m.statement,
                discrepancy=m.discrepancy,
                comments=list(m.comments),
                paired=m.paired,
                acceptance=_acceptance_out(m),
            )
            for m in day.movements
        ],
        opening=None if day.opening is None else _balance_out(day.opening),
        closing=None if day.closing is None else _balance_out(day.closing),
        waiting=[_waiting_out(w) for w in day.waiting],
    )


def _balance_out(line: BalanceLine) -> BalanceLineOut:
    return BalanceLineOut(
        line=line.line,
        status=line.status.value,
        system=line.system,
        statement=line.statement,
        discrepancy=line.discrepancy,
        expected=line.expected,
        acceptance=_acceptance_out(line),
    )


def _waiting_out(item: OpenItem) -> WaitingOut:
    return WaitingOut(
        date=item.day,
        line=item.key.line,
        kind=item.key.kind,
        secid=item.secid,
        trade=item.key.trade,
        discrepancy=item.discrepancy,
        comment=item.comment,
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
            "portfolio": "Сервис портфелей",
            "booking": "Сервис сделок",
            "refdata": "Справочник",
        }
        return problem(
            503, f"{service}_unavailable", f"{names[service]} недоступен. Повторите позже."
        )

    app.add_exception_handler(RuleViolationError, rule_violation)
    app.add_exception_handler(NotFoundError, not_found)
    app.add_exception_handler(ServiceUnavailableError, unavailable)
