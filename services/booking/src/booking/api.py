"""HTTP API сделок, импорта, операций фонда, позиций и лестницы. Правила — в design/05."""

from __future__ import annotations

import base64
import datetime as dt
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from sqlalchemy.ext.asyncio import AsyncSession

from am_common.app import AppResources, DbSession
from am_common.auth import Principal, Role, require_any_role
from am_common.calendar import add_trading_days
from am_common.clock import MOSCOW
from am_common.events import Actor
from am_quant.bond import QuantError
from am_quant.kalman import NAV_SETTINGS, Memory, Settings, memory_of, settings_for
from booking.accounts import MAX_ACCOUNT_DAYS, AccountDay, AccountDays, account_days
from booking.clients import (
    MarketdataClient,
    PortfolioClient,
    PortfolioRef,
    RefdataClient,
    ServiceUnavailableError,
    physical_under,
    service_token,
)
from booking.closing import ClosingDesk, ClosingState
from booking.imports import ImportItem, Result, TradeImport
from booking.ladder import MAX_PAST_DAYS, Ladder, ladder
from booking.nav_workbook import filename as workbook_name
from booking.nav_workbook import nav_workbook
from booking.operations import OperationDesk, OperationInput, OperationView
from booking.queries import (
    NavDay,
    NavHistory,
    NavPoint,
    PeriodResult,
    PeriodTotal,
    PortfolioState,
    PositionLine,
    ResultDay,
    ResultLine,
    nav,
    nav_history,
    positions,
    previous_trading_day,
    result_summary,
    results,
)
from booking.quik import QuikFormatError, QuikImport, QuikLine, Rates
from booking.repository import ClosedPeriodError, NotFoundError, Repository
from booking.roll import DayResult, Movement, OperationKind, Side
from booking.settings import BookingSettings
from booking.tariffs import TariffDesk
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
# Позиции, СЧА и сделки читает и риск-менеджмент: по ним считаются лимиты (design/09).
# Сервис лимитов читает их и сам — для проверки сделки до ввода (решение 147).
Viewer = Annotated[
    Principal,
    Depends(require_any_role(Role.TRADING, Role.OPERATIONS, Role.RISK, Role.FUND_READER)),
]
Operations = Annotated[Principal, Depends(require_any_role(Role.OPERATIONS))]
# День закрывает сервис сверки, когда все счета фонда сверены (design/08); открывает
# заново — администратор.
DayCloser = Annotated[Principal, Depends(require_any_role(Role.CLOSE_DAY))]
Admin = Annotated[Principal, Depends(require_any_role(Role.ADMIN))]
# Тариф брокера задаёт администратор, а форма загрузки из отчёта QUIK его показывает.
TariffReader = Annotated[
    Principal, Depends(require_any_role(Role.TRADING, Role.OPERATIONS, Role.ADMIN))
]
# Импорт — пользователям и скрипту сделок (спецификация docs/api/trade-import).
Feeder = Annotated[
    Principal, Depends(require_any_role(Role.TRADING, Role.OPERATIONS, Role.TRADE_FEED))
]
Authorization = Annotated[str, Header(alias="Authorization")]
Comment = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
OptionalComment = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None
# Результат за период — не больше трёх лет за запрос.
MAX_RESULT_DAYS = 1100
CENT = Decimal("0.01")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
Money = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
Price = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
Kopecks = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
Quantity = Annotated[int, Field(gt=0, le=10**12)]
Rate = Annotated[Decimal, Field(ge=0, le=100, max_digits=9, decimal_places=6)]  # % годовых

# Свои настройки фильтра Калмана в днях (решение 174) и стандартные — для сравнения.
HalfLife = Annotated[Decimal | None, Query(ge=Decimal("0.5"), le=Decimal(20))]
TrendMemory = Annotated[Decimal | None, Query(ge=Decimal(5), le=Decimal(500))]
STANDARD_MEMORY = memory_of(NAV_SETTINGS)

router = APIRouter()


class Clients:
    def __init__(self, resources: AppResources) -> None:
        settings = cast(BookingSettings, resources.settings)
        token = service_token(resources)
        self.refdata = RefdataClient(resources.http, settings.refdata_url, token)
        self.marketdata = MarketdataClient(resources.http, settings.marketdata_url, token)
        self.portfolios = PortfolioClient(resources.http, settings.portfolio_url)
        self.benchmark = settings.benchmark_secid


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
    """Сделка; ``kind: repo`` — РЕПО с НКЦ (design/07): сторона, дата расчётов и сумма —
    первой части, продажа — прямое РЕПО. Без бумаги — РЕПО с КСУ: только сумма."""

    kind: Literal["outright", "repo"] = "outright"
    portfolio_id: UUID
    instrument_id: UUID | None = None
    side: Side
    quantity: Annotated[int, Field(ge=0, le=10**12)] = 0
    price: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=6)] = Decimal(0)
    trade_at: AwareDatetime
    settle_date: date
    face_value: Price | None = None
    accrued: Kopecks | None = None
    fee: Kopecks = Decimal(0)
    venue: Literal["exchange", "negotiated", "otc"] = "exchange"
    counterparty: Text | None = None
    external_id: Text | None = None
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    amount: Money | None = None  # РЕПО с КСУ: сумма первой части
    repo_rate: Rate | None = None
    second_date: date | None = None
    second_amount: Money | None = None

    @model_validator(mode="after")
    def _security(self) -> TradeIn:
        """С бумагой — количество и цена; без бумаги — только РЕПО с КСУ."""
        if self.instrument_id is not None and not (self.quantity and self.price):
            raise ValueError("у сделки с бумагой нужны количество и цена")
        return self

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
    instrument_id: UUID | None  # у РЕПО с КСУ бумаги нет
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
    kind: str = "outright"
    repo_rate: Decimal | None = None
    second_date: date | None = None
    second_amount: Decimal | None = None
    comment: str | None
    author: str
    created_at: datetime
    versions: list[VersionOut] = []
    warnings: list[str] = []
    # Фонд закрыт по этот день: сделку с более ранней датой не изменить (design/08).
    closed_through: date | None = None


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
    # Оценка на конец дня (design/06); без расчёта на этот день — нули и цены нет.
    price: Decimal | None = None  # цена для оценки: облигация — %, пай — ₽
    clean: Decimal = Decimal(0)  # стоимость по чистой цене
    accrued_value: Decimal = Decimal(0)  # НКД в оценке: на дату расчётов T+1
    coupon_due: Decimal = Decimal(0)  # купоны к получению
    principal_due: Decimal = Decimal(0)  # погашение номинала к получению (решение 149)
    unrealized: Decimal = Decimal(0)
    revaluation: Decimal = Decimal(0)  # результат дня по частям
    realized: Decimal = Decimal(0)
    coupon: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    result: Decimal = Decimal(0)
    flags: list[str] = []
    # Чувствительность (решение 107): ₽ за 1 бп на всю позицию; основание — yield | spread.
    dv01: Decimal | None = None
    duration: Decimal | None = None
    metrics_basis: str | None = None
    # Флоатер: модифицированная дюрация до ближайшего купона — для лимитов (design/09).
    reset_duration: Decimal | None = None
    bond_type: str | None = None  # fixed | floater | linker
    # Доходность к горизонту (облигация с фиксированным купоном) или спред (флоатер,
    # линкер) — по цене для оценки, в годовых и в непрерывной капитализации.
    yield_percent: Decimal | None = None
    yield_continuous: Decimal | None = None
    spread_bp: Decimal | None = None
    spread_continuous_bp: Decimal | None = None
    horizon_kind: str | None = None
    horizon_date: date | None = None
    duration_continuous: Decimal | None = None
    dv01_continuous: Decimal | None = None  # на всю позицию
    # Результат и реализованный результат за периоды (решение 164).
    periods: dict[str, PeriodOut] = {}


class PeriodOut(BaseModel):
    result: Decimal
    realized: Decimal


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


class DayResultOut(BaseModel):
    """Стоимость портфеля на конец дня и результат дня по частям, ₽ (design/06)."""

    value: Decimal
    flows: Decimal  # приток: паи, переводы из других портфелей фонда
    revaluation: Decimal
    realized: Decimal
    coupon: Decimal
    fees: Decimal
    expenses: Decimal
    charges: Decimal  # начисленные вознаграждение и комиссии от СЧА
    correction: Decimal  # корректировка комиссий от СЧА при выплате
    repo: Decimal = Decimal(0)  # проценты по РЕПО
    total: Decimal
    receivable: Decimal  # к получению по сделкам до расчётов
    payable: Decimal  # к уплате по сделкам до расчётов
    flags: list[str]


class PortfolioStateOut(FundFigures):
    id: UUID
    code: str
    name: str
    as_of: date | None
    waiting_since: date | None
    cash: Decimal
    movements: list[MovementOut]
    result: DayResultOut | None = None


class RepoOut(BaseModel):
    """Открытая сделка РЕПО на дату: долг и начисленные проценты (design/07)."""

    id: UUID
    portfolio_id: UUID
    portfolio_code: str
    direct: bool  # прямое: фонд привлёк деньги под бумаги
    instrument_id: UUID | None  # None — КСУ
    secid: str
    short_name: str
    quantity: int  # бумаги в РЕПО
    rate: Decimal
    first_date: date
    second_date: date
    second_amount: Decimal
    principal: Decimal
    accrued: Decimal
    value: Decimal  # в стоимости портфеля: требование +, обязательство −


class PositionsOut(FundFigures):
    portfolio_id: UUID
    code: str
    name: str
    date: date
    cash: Decimal
    portfolios: list[PortfolioStateOut]
    lines: list[LineOut]
    result: DayResultOut | None = None  # сумма по портфелям, посчитанным на этот день
    dv01_yield: Decimal = Decimal(0)  # облигации с фиксированным купоном
    dv01_spread: Decimal = Decimal(0)  # все облигации: по спреду
    repos: list[RepoOut] = []
    # Бумаги, которых в позиции уже нет: результат за периоды одной суммой.
    closed: dict[str, PeriodOut] = {}
    # Соседние торговые дни — для кнопок «‹ пред.» и «след. ›»; нет календаря — None.
    previous_day: dt.date | None = None
    next_day: dt.date | None = None


class ResultDayOut(BaseModel):
    date: date
    value: Decimal
    flows: Decimal
    revaluation: Decimal
    realized: Decimal
    coupon: Decimal
    fees: Decimal
    expenses: Decimal
    charges: Decimal
    correction: Decimal
    repo: Decimal
    total: Decimal
    flags: list[str]


class ResultLineOut(BaseModel):
    instrument_id: UUID
    secid: str
    short_name: str
    revaluation: Decimal
    realized: Decimal
    coupon: Decimal
    fees: Decimal
    total: Decimal


class ResultsOut(BaseModel):
    portfolio_id: UUID
    code: str
    name: str
    first: date
    last: date
    portfolios: list[str]
    waiting_since: date | None
    days: list[ResultDayOut]
    lines: list[ResultLineOut]


class PeriodResultOut(BaseModel):
    """Результат за период отчёта (решение 167): части, стоимость, приток, доходность."""

    key: str  # day | week | days30 | quarter | year | all | custom
    first: date
    last: date
    days: int
    revaluation: Decimal
    realized: Decimal
    coupon: Decimal
    fees: Decimal
    repo: Decimal
    expenses: Decimal
    charges: Decimal
    correction: Decimal
    total: Decimal
    flows: Decimal
    start_value: Decimal
    end_value: Decimal
    invested: Decimal  # средневзвешенные вложения — база доходности
    return_percent: Decimal | None  # % от вложений; вложений нет — None
    annual_percent: Decimal | None  # % годовых: × 365 / дней периода
    flags: list[str]


class SummaryLineOut(BaseModel):
    instrument_id: UUID
    secid: str
    short_name: str
    held: bool  # в позиции на дату отчёта; нет — продана или погашена
    totals: dict[str, Decimal]  # период → результат бумаги


class ResultSummaryOut(BaseModel):
    portfolio_id: UUID
    code: str
    name: str
    date: dt.date
    first_day: dt.date  # начало «за всё время»
    portfolios: list[str]
    waiting_since: dt.date | None
    periods: list[PeriodResultOut]
    lines: list[SummaryLineOut]
    # Начислено и не оплачено на дату отчёта (решение 168): расходы и комиссии от СЧА.
    expenses_owed: Decimal = Decimal(0)
    fees_owed: Decimal = Decimal(0)


class NavPointOut(BaseModel):
    date: dt.date
    nav: Decimal
    units: Decimal
    unit_price: Decimal | None
    smoothed: Decimal | None  # уровень фильтра Калмана, до копейки
    benchmark: Decimal | None  # цена бумаги сравнения (LQDT)


class NavForecastOut(BaseModel):
    """Прогноз стоимости пая на следующий день по фильтру Калмана (решение 168)."""

    date: dt.date
    value: Decimal
    low: Decimal | None  # ± одно стандартное отклонение: 68%
    high: Decimal | None
    trend: Decimal  # прирост стоимости пая за день
    # Чистый прирост, % годовых: тренд минус комиссии от СЧА за день — столько получает
    # пайщик. Дни начисления и календарные дни за год совпадают: по 365.
    trend_percent: Decimal


class NavFilterOut(BaseModel):
    """Настройки фильтра Калмана, по которым посчитаны сглаженная и прогноз (решение 174)."""

    half_life: Decimal  # за сколько торговых дней сглаженная наполовину догоняет скачок
    trend_memory: Decimal  # по скольким последним торговым дням считается тренд
    q_level: Decimal
    q_trend: Decimal
    standard: bool  # настройки по умолчанию, а не свои
    standard_half_life: Decimal
    standard_trend_memory: Decimal


class NavHistoryOut(BaseModel):
    fund_id: UUID
    code: str
    name: str
    first_day: dt.date | None
    today: dt.date
    waiting_since: dt.date | None
    benchmark: str | None
    points: list[NavPointOut]
    forecast: NavForecastOut | None
    filter: NavFilterOut
    # Два последних дня со стоимостью пая по всей истории — для карточек «сейчас»,
    # что бы ни было выбрано для графика.
    latest: list[NavPointOut]


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
    # С чьего счёта платят: physical фонда; нет — казначейский (решение 122).
    portfolio_id: UUID | None = None
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


FeeKind = Literal["management", "specdep", "registrar", "other"]


class FeePaymentIn(_OperationBase):
    """Выплата вознаграждения или комиссии от СЧА за период (решения 43, 106)."""

    kind: Literal["fee_payment"]
    fund_id: UUID
    fee_kind: FeeKind
    period_end: date  # последний день оплаченного месяца
    amount: Money


class RepoContributionIn(_OperationBase):
    """Компенсационный взнос по РЕПО по отчёту брокера (решение 113): деньгами или бумагами."""

    kind: Literal["repo_contribution"]
    trade_id: UUID
    direction: Literal["in", "out"]  # in — фонду, out — от фонда
    amount: Money | None = None
    quantity: Quantity | None = None

    @model_validator(mode="after")
    def _one_form(self) -> RepoContributionIn:
        if (self.amount is None) == (self.quantity is None):
            raise ValueError("взнос — деньгами или бумагами: укажите одно из двух")
        return self


OperationIn = Annotated[
    UnitsIn | ExpenseIn | CashTransferIn | SecurityTransferIn | FeePaymentIn | RepoContributionIn,
    Field(discriminator="kind"),
]


class FeeRateIn(BaseModel):
    kind: FeeKind
    rate: Annotated[Decimal, Field(ge=0, le=100, max_digits=9, decimal_places=6)]  # % годовых
    valid_from: date
    comment: OptionalComment = None


class FeeRateOut(BaseModel):
    kind: FeeKind
    rate: Decimal
    valid_from: date
    portfolio_id: UUID  # казначейский портфель: сюда начисление
    comment: str | None
    author: str
    created_at: datetime


class NavDayOut(BaseModel):
    date: date
    nav: Decimal
    units: Decimal
    unit_price: Decimal | None
    cash: Decimal
    clean: Decimal
    accrued: Decimal
    principal_due: Decimal  # погашение номинала к получению (решение 149)
    receivable: Decimal
    payable: Decimal
    claims: Decimal
    obligations: Decimal
    expenses: Decimal
    fees_owed: Decimal
    fees_today: dict[str, Decimal]
    # Начислено и не выплачено: вид комиссии → месяц («2026-10») → сумма (решение 175).
    fees_due: dict[str, dict[str, Decimal]]
    repo_placed: Decimal  # размещено в обратное РЕПО, с процентами
    repo_borrowed: Decimal  # привлечено в прямое РЕПО, с процентами
    flags: list[str]


class NavOut(BaseModel):
    fund_id: UUID
    code: str
    name: str
    first: date
    last: date
    waiting_since: date | None
    days: list[NavDayOut]


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
    fee_kind: str | None = None
    period_end: date | None = None
    trade_id: UUID | None = None  # компенсационный взнос: сделка РЕПО
    direction: str | None = None
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
    quantity: Annotated[int, Field(ge=0, le=10**12)] = 0
    price: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=6)] = Decimal(0)
    trade_at: AwareDatetime
    settle_date: date
    accrued: Kopecks | None = None
    face_value: Price | None = None
    fee: Kopecks = Decimal(0)
    venue: Literal["exchange", "negotiated", "otc"] = "exchange"
    counterparty: Text | None = None
    comment: OptionalComment = None
    cancelled: bool = False
    kind: Literal["outright", "repo"] = "outright"
    amount: Money | None = None
    repo_rate: Rate | None = None
    second_date: date | None = None
    second_amount: Money | None = None


class TradeBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_-]{1,32}$")]
    trades: Annotated[list[ImportTradeIn], Field(min_length=1, max_length=5000)]


class ImportedOut(BaseModel):
    index: int
    external_id: str
    result: Result
    trade_id: UUID | None  # None — новая сделка при проверке: её ещё нет
    warnings: list[str]


class ImportRejectedOut(BaseModel):
    index: int
    external_id: str
    error: str
    message: str


class TariffIn(BaseModel):
    outright_rate: Annotated[Decimal, Field(ge=0, le=10, max_digits=9, decimal_places=6)]
    repo_rate: Annotated[Decimal, Field(ge=0, le=100, max_digits=9, decimal_places=6)]
    # РПС — по умолчанию 0,02% (решение 159).
    rps_rate: Annotated[Decimal, Field(ge=0, le=10, max_digits=9, decimal_places=6)] = Decimal(
        "0.02"
    )
    valid_from: date
    comment: OptionalComment = None


class TariffOut(BaseModel):
    outright_rate: Decimal  # % от оборота без НКД
    repo_rate: Decimal  # % годовых от суммы первой части
    rps_rate: Decimal  # РПС: % от оборота без НКД
    valid_from: date
    comment: str | None
    author: str
    created_at: datetime


# Сделки за период — не больше года (решение 161).
MAX_TRADE_DAYS = 366

# Отчёт QUIK — в base64: файл в несколько десятков килобайт, multipart не нужен.
QUIK_MAX_BYTES = 5 * 1024 * 1024


class QuikIn(BaseModel):
    """Отчёт QUIK «Отчёт по всем сделкам клиента» и куда его загрузить (решение 151)."""

    file: Annotated[str, StringConstraints(min_length=1, max_length=QUIK_MAX_BYTES * 4 // 3 + 4)]
    portfolio_id: UUID
    # Ставки на эту загрузку; нет — тариф брокера на счёт портфеля на день сделки.
    outright_rate: Annotated[Decimal | None, Field(ge=0, le=10, max_digits=9, decimal_places=6)] = (
        None
    )
    repo_rate: Annotated[Decimal | None, Field(ge=0, le=100, max_digits=9, decimal_places=6)] = None
    rps_rate: Annotated[Decimal | None, Field(ge=0, le=10, max_digits=9, decimal_places=6)] = None
    apply: bool = False  # false — только проверка: ничего не записывается


class QuikLineOut(BaseModel):
    line: int
    external_id: str
    kind: str
    code: str
    name: str
    side: Side | None
    trade_at: datetime | None
    quantity: int | None
    price: Decimal | None
    amount: Decimal | None
    fee: Decimal | None
    fee_rate: Decimal | None
    result: str | None  # created | amended | unchanged; None — отклонена
    trade_id: UUID | None
    error: str | None
    message: str | None
    warnings: list[str]


class NewInstrumentOut(BaseModel):
    code: str
    name: str


class QuikOut(BaseModel):
    client_code: str | None
    period_from: date | None
    period_till: date | None
    tariff: TariffOut | None  # тариф счёта на последний день сделок отчёта
    applied: bool
    lines: list[QuikLineOut]
    # Бумаг нет в справочнике: пока список не пуст, отчёт не загрузить.
    new_instruments: list[NewInstrumentOut]


class TradeBatchOut(BaseModel):
    created: int
    amended: int
    cancelled: int
    unchanged: int
    trades: list[ImportedOut]
    rejected: list[ImportRejectedOut]


class LadderMovementOut(MovementOut):
    portfolio_code: str
    # Сделка РЕПО: срок в торговых днях — части, комиссия и взносы (решение 166).
    repo_term: int | None = None


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


class ForecastOut(BaseModel):
    """Купон флоатера в лестнице по прогнозу (решение 14): на бумагу и из какой ставки."""

    instrument_id: UUID
    date: date
    coupon: Decimal  # на бумагу, ₽
    index: str  # key_rate | ruonia | rusfar
    index_value: Decimal  # % годовых
    margin_bp: Decimal
    capitalized: bool


class LadderOut(BaseModel):
    account_id: UUID
    portfolios: list[LadderPortfolioOut]
    today: date  # дни раньше — факт, с сегодня — план
    as_of: date | None
    waiting_since: date | None
    instruments: list[InstrumentOut]
    days: list[LadderDayOut]
    forecasts: list[ForecastOut] = []


class AccountTradeOut(BaseModel):
    """Сделка дня на счёте — для сверки с отчётом брокера (design/08)."""

    trade_id: UUID
    version: int
    status: str
    portfolio_code: str
    external_id: str | None
    kind: str
    side: str
    instrument_id: UUID | None
    instrument_kind: str
    secid: str
    short_name: str
    quantity: int
    price: Decimal
    accrued: Decimal | None
    amount: Decimal
    fee: Decimal
    settle_date: date
    repo_rate: Decimal | None
    second_date: date | None
    second_amount: Decimal | None


class AccountMovementOut(BaseModel):
    """Движение денег дня: у движения по сделке — её номер, у выплаты — бумага."""

    portfolio_code: str
    kind: str
    cash: Decimal
    trade_id: UUID | None
    external_id: str | None
    instrument_id: UUID | None
    secid: str | None


class AccountDayOut(BaseModel):
    date: date
    trading: bool
    calculated: bool
    opening_cash: Decimal
    closing_cash: Decimal
    trades: list[AccountTradeOut]
    movements: list[AccountMovementOut]


class AccountDaysOut(BaseModel):
    account_id: UUID
    fund_id: UUID
    portfolios: list[LadderPortfolioOut]
    waiting_since: date | None
    days: list[AccountDayOut]


class ClosedByIn(BaseModel):
    subject: Text
    username: Text


class CloseIn(BaseModel):
    """Закрыть день: сервис сверки передаёт, кто закрывает и код фонда для журнала."""

    day: date
    fund_code: Text
    closed_by: ClosedByIn


class ReopenIn(BaseModel):
    day: date
    comment: Comment


class ClosingRowOut(BaseModel):
    action: Literal["close", "reopen"]
    first_day: date
    last_day: date
    closed_through: date
    author: str
    comment: str | None
    created_at: datetime


class ClosingOut(BaseModel):
    fund_id: UUID
    closed_through: date | None
    next_day: date | None
    history: list[ClosingRowOut]


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
    user: Viewer,
    *,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
    portfolio_id: UUID,
    day: Annotated[date | None, Query(alias="date")] = None,
    first: Annotated[date | None, Query(alias="from")] = None,
    last: Annotated[date | None, Query(alias="till")] = None,
) -> list[TradeOut]:
    """Сделки по портфелю за день ``date`` или за период ``from``–``till`` (решение 161);
    ничего — за сегодня. Compound — по всем его physical-портфелям."""
    today = now.astimezone(MOSCOW).date()
    since = day or first or today
    till = day or last or since
    if till < since or (till - since).days >= MAX_TRADE_DAYS:
        raise RuleViolationError(
            "bad_period",
            f"Период — от одного дня до {MAX_TRADE_DAYS} дней, конец не раньше начала.",
        )
    visible = await services.portfolios.visible(authorization)
    physical = {p.id: p for p in physical_under(portfolio_id, visible)}
    rows = await Repository(session).current(portfolio_ids=physical, period=(since, till))
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
    dry_run: bool = False,
) -> TradeBatchOut:
    """Пакет сделок от внешней системы или из файла: ключ — источник и номер сделки.
    ``dry_run`` — только проверка: что случится с каждой сделкой, без записи."""
    trades = desk(session, user, authorization, services, now)
    report = await TradeImport(session, trades, services.refdata).run(
        body.source, [ImportItem(**item.model_dump()) for item in body.trades], dry_run=dry_run
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


@router.post("/imports/quik", tags=["import"])
async def import_quik(
    body: QuikIn,
    *,
    user: Staff,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
) -> QuikOut:
    """Отчёт QUIK: сначала проверка без записи, потом загрузка с ``apply`` (решение 151)."""
    try:
        content = base64.b64decode(body.file, validate=True)
    except ValueError as exc:  # binascii.Error и не-ASCII символы
        raise RuleViolationError("bad_file", "Файл не прочитать: передайте его в base64.") from exc
    if len(content) > QUIK_MAX_BYTES:
        raise RuleViolationError(
            "file_too_large", "Отчёт больше 5 МБ — выгрузите период короче.", 413
        )
    trades = desk(session, user, authorization, services, now)
    rates = Rates(outright=body.outright_rate, repo=body.repo_rate, rps=body.rps_rate)
    try:
        result = await QuikImport(session, trades, services.refdata).run(
            content, body.portfolio_id, rates, apply=body.apply
        )
    except QuikFormatError as exc:
        raise RuleViolationError("not_quik_report", exc.message) from exc
    tariff = result.tariff
    return QuikOut(
        client_code=result.client_code,
        period_from=None if result.period is None else result.period[0],
        period_till=None if result.period is None else result.period[1],
        tariff=None if tariff is None else TariffOut.model_validate(tariff, from_attributes=True),
        applied=result.applied,
        new_instruments=[
            NewInstrumentOut(code=code, name=name) for code, name in result.new_instruments
        ],
        lines=[
            QuikLineOut.model_validate({**_quik_fields(line), "warnings": list(line.warnings)})
            for line in result.lines
        ],
    )


def _quik_fields(line: QuikLine) -> dict[str, object]:
    return {name: getattr(line, name) for name in line.__dataclass_fields__}


# --- тарифы брокера -----------------------------------------------------------------


@router.get("/accounts/{account_id}/tariffs", tags=["import"])
async def tariffs(
    account_id: UUID,
    *,
    user: TariffReader,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
) -> list[TariffOut]:
    """Тарифы брокера на счёт — вся история, по дате начала."""
    desk_ = TariffDesk(session, user, authorization, services.portfolios, lambda: now)
    return [
        TariffOut.model_validate(r, from_attributes=True) for r in await desk_.history(account_id)
    ]


@router.post("/accounts/{account_id}/tariffs", status_code=201, tags=["import"])
async def set_tariff(
    account_id: UUID,
    *,
    body: TariffIn,
    user: Admin,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
) -> list[TariffOut]:
    """Новый тариф брокера с даты начала; загруженные сделки он не меняет."""
    desk_ = TariffDesk(session, user, authorization, services.portfolios, lambda: now)
    rows = await desk_.set(
        account_id,
        outright_rate=body.outright_rate,
        repo_rate=body.repo_rate,
        rps_rate=body.rps_rate,
        valid_from=body.valid_from,
        comment=body.comment,
    )
    return [TariffOut.model_validate(r, from_attributes=True) for r in rows]


# --- позиции --------------------------------------------------------------------


@router.get("/positions", tags=["positions"])
async def get_positions(
    _user: Viewer,
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
    view = await positions(
        session, visible, portfolio_id, on, now=now, marketdata=services.marketdata
    )
    if view is None:
        raise NotFoundError("portfolio", portfolio_id)
    try:
        exceptions = await services.refdata.calendar_around(on)
        following = add_trading_days(on + timedelta(days=1), 0, exceptions)
        neighbours: tuple[date | None, date | None] = (
            previous_trading_day(on, exceptions),
            following,
        )
    except ServiceUnavailableError:
        neighbours = (None, None)
    return PositionsOut(
        previous_day=neighbours[0],
        next_day=neighbours[1],
        closed={name: _period_out(total) for name, total in view.closed.items()},
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
        result=None
        if not view.results
        else DayResultOut(
            **{name: view.result(name) for name in DayResultOut.model_fields if name != "flags"},
            flags=[f.value for f in view.flags],
        ),
        dv01_yield=view.dv01("yield").quantize(Decimal("0.01")),
        dv01_spread=view.dv01().quantize(Decimal("0.01")),
        repos=[
            RepoOut(
                id=line.position.id,
                portfolio_id=line.portfolio.id,
                portfolio_code=line.portfolio.code,
                direct=line.position.direct,
                instrument_id=line.position.instrument_id,
                secid=line.trade.secid,
                short_name=line.trade.short_name,
                quantity=line.position.quantity,
                rate=line.trade.repo_rate,
                first_date=line.position.first_date,
                second_date=line.position.second_date,
                second_amount=line.position.second_amount,
                principal=line.position.principal,
                accrued=line.position.accrued,
                value=line.position.value,
            )
            for line in view.repos
        ],
    )


@router.get("/results", tags=["positions"])
async def get_results(
    _user: Staff,
    *,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
    portfolio_id: UUID,
    first: Annotated[date, Query(alias="from")],
    last: Annotated[date, Query(alias="till")],
) -> ResultsOut:
    """Финансовый результат по дням и по бумагам за период (design/06)."""
    if last < first or (last - first).days > MAX_RESULT_DAYS:
        raise RuleViolationError(
            "bad_period",
            f"Период — от одного дня до {MAX_RESULT_DAYS} дней, конец не раньше начала.",
        )
    visible = await services.portfolios.visible(authorization)
    view = await results(
        session, visible, portfolio_id, (first, last), now=now, marketdata=services.marketdata
    )
    if view is None:
        raise NotFoundError("portfolio", portfolio_id)
    return ResultsOut(
        portfolio_id=view.portfolio.id,
        code=view.portfolio.code,
        name=view.portfolio.name,
        first=view.first,
        last=view.last,
        portfolios=[p.code for p in view.portfolios],
        waiting_since=view.waiting_since,
        days=[_result_day_out(d) for d in view.days],
        lines=[_result_line_out(line) for line in view.lines],
    )


@router.get("/results/summary", tags=["positions"])
async def get_result_summary(
    _user: Staff,
    *,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
    portfolio_id: UUID,
    on: Annotated[date | None, Query(alias="date")] = None,
    custom_from: Annotated[date | None, Query(alias="from")] = None,
    custom_till: Annotated[date | None, Query(alias="till")] = None,
) -> ResultSummaryOut:
    """Отчёт о финансовом результате на дату (решение 167): день, неделя, 30 дней,
    с начала квартала и года, всё время и свой период ``from``–``till``."""
    if (custom_from is None) != (custom_till is None) or (
        custom_from is not None and custom_till is not None and custom_till < custom_from
    ):
        raise RuleViolationError("bad_period", "Свой период — обе даты, конец не раньше начала.")
    custom = None if custom_from is None or custom_till is None else (custom_from, custom_till)
    visible = await services.portfolios.visible(authorization)
    view = await result_summary(
        session,
        visible,
        portfolio_id,
        on or now.astimezone(MOSCOW).date(),
        custom=custom,
        now=now,
        marketdata=services.marketdata,
    )
    if view is None:
        raise NotFoundError("portfolio", portfolio_id)
    return ResultSummaryOut(
        portfolio_id=view.portfolio.id,
        code=view.portfolio.code,
        name=view.portfolio.name,
        date=view.day,
        first_day=view.first_day,
        portfolios=[p.code for p in view.portfolios],
        waiting_since=view.waiting_since,
        periods=[_period_result_out(p) for p in view.periods],
        expenses_owed=view.expenses_owed.quantize(CENT),
        fees_owed=view.fees_owed.quantize(CENT),
        lines=[
            SummaryLineOut(
                instrument_id=line.instrument_id,
                secid=line.secid,
                short_name=line.short_name,
                held=line.held,
                totals={
                    p.key: p.lines.get(line.instrument_id, Decimal(0)).quantize(CENT)
                    for p in view.periods
                },
            )
            for line in view.lines
        ],
    )


def _period_result_out(period: PeriodResult) -> PeriodResultOut:
    rate = period.rate
    percent = None if rate is None else (rate * 100).quantize(Decimal("0.0001"))
    annual = None if rate is None else (rate * 100 * 365 / period.days).quantize(Decimal("0.0001"))
    return PeriodResultOut(
        key=period.key,
        first=period.first,
        last=period.last,
        days=period.days,
        **{name: value.quantize(CENT) for name, value in period.parts.items()},
        total=period.total.quantize(CENT),
        flows=period.flows.quantize(CENT),
        start_value=period.start_value.quantize(CENT),
        end_value=period.end_value.quantize(CENT),
        invested=period.invested.quantize(CENT),
        return_percent=percent,
        annual_percent=annual,
        flags=sorted(f.value for f in period.flags),
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
    """Паи и вознаграждение — в казначейский портфель фонда, расход — в портфель, с чьего
    счёта платят; перевод — внутри фонда."""
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


@router.post("/operations/{operation_id}/amend", tags=["operations"])
async def amend_operation(
    operation_id: UUID,
    body: OperationIn,
    *,
    user: Operations,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
) -> OperationOut:
    """Исправление — новая версия операции: вид и фонд прежние, ``comment`` — причина,
    обязательна (решение 175)."""
    entry = OperationInput(**(body.model_dump() | {"kind": OperationKind(body.kind)}))
    view = await operation_desk(session, user, authorization, services, now).amend(
        operation_id, entry
    )
    return _operation_out(view)


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


# --- СЧА и комиссии от СЧА ---------------------------------------------------------


@router.get("/funds/{fund_id}/fee-rates", tags=["nav"])
async def fee_rates(
    fund_id: UUID,
    *,
    user: Staff,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
) -> list[FeeRateOut]:
    """Ставки комиссий от СЧА фонда — вся история, по дате начала."""
    desk_ = operation_desk(session, user, authorization, services, now)
    return [FeeRateOut.model_validate(r, from_attributes=True) for r in await desk_.rates(fund_id)]


@router.post("/funds/{fund_id}/fee-rates", status_code=201, tags=["nav"])
async def set_fee_rate(
    fund_id: UUID,
    *,
    body: FeeRateIn,
    user: Operations,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
) -> list[FeeRateOut]:
    """Новая ставка с даты начала (решение 21); фонд пересчитывается с этой даты."""
    desk_ = operation_desk(session, user, authorization, services, now)
    rows = await desk_.set_rate(
        fund_id,
        kind=body.kind,
        rate=body.rate,
        valid_from=body.valid_from,
        comment=body.comment,
    )
    return [FeeRateOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/nav", tags=["nav"])
async def get_nav(
    _user: Viewer,
    *,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
    fund_id: UUID,
    first: Annotated[date, Query(alias="from")],
    last: Annotated[date, Query(alias="till")],
) -> NavOut:
    """СЧА фонда, паи и стоимость пая по дням, состав СЧА и начисленные комиссии."""
    if last < first or (last - first).days > MAX_RESULT_DAYS:
        raise RuleViolationError(
            "bad_period",
            f"Период — от одного дня до {MAX_RESULT_DAYS} дней, конец не раньше начала.",
        )
    visible = await services.portfolios.visible(authorization)
    view = await nav(
        session, visible, fund_id, (first, last), now=now, marketdata=services.marketdata
    )
    if view is None:
        raise NotFoundError("fund", fund_id)
    return NavOut(
        fund_id=view.fund.id,
        code=view.fund.code,
        name=view.fund.name,
        first=view.first,
        last=view.last,
        waiting_since=view.waiting_since,
        days=[_nav_day_out(d) for d in view.days],
    )


@router.get("/nav/history", tags=["nav"])
async def get_nav_history(
    _user: Viewer,
    *,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
    fund_id: UUID,
    since: Annotated[date | None, Query(alias="from")] = None,
    till: Annotated[date | None, Query(alias="till")] = None,
    half_life: HalfLife = None,
    trend_memory: TrendMemory = None,
) -> NavHistoryOut:
    """Стоимость пая и СЧА с ``from`` по ``till`` (по умолчанию — вся история по сегодня),
    сглаженная фильтром Калмана, прогноз на завтра и цена бумаги сравнения (решение 168).
    ``half_life`` и ``trend_memory`` — свои настройки фильтра в днях (решение 174)."""
    memory, settings, standard = _nav_filter(half_life, trend_memory)
    history = await _nav_history(
        session,
        authorization,
        services,
        now=now,
        fund_id=fund_id,
        period=(since, till),
        settings=settings,
    )
    return _nav_history_out(
        history,
        NavFilterOut(
            half_life=memory.half_life.quantize(CENT),
            trend_memory=memory.trend_memory.quantize(Decimal("0.1")),
            q_level=settings.q_level,
            q_trend=settings.q_trend,
            standard=standard,
            standard_half_life=STANDARD_MEMORY.half_life.quantize(CENT),
            standard_trend_memory=STANDARD_MEMORY.trend_memory.quantize(Decimal("0.1")),
        ),
    )


@router.get(
    "/nav/history.xlsx",
    tags=["nav"],
    response_class=Response,
    responses={200: {"content": {XLSX: {}}, "description": "СЧА и стоимость пая по дням"}},
)
async def get_nav_workbook(
    _user: Viewer,
    *,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    now: Now,
    fund_id: UUID,
    since: Annotated[date | None, Query(alias="from")] = None,
    till: Annotated[date | None, Query(alias="till")] = None,
    half_life: HalfLife = None,
    trend_memory: TrendMemory = None,
) -> Response:
    """Те же дни в Excel — с теми же настройками фильтра."""
    history = await _nav_history(
        session,
        authorization,
        services,
        now=now,
        fund_id=fund_id,
        period=(since, till),
        settings=_nav_filter(half_life, trend_memory)[1],
    )
    return Response(
        nav_workbook(history),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{workbook_name(history)}"'},
    )


async def _nav_history(
    session: AsyncSession,
    authorization: str,
    services: Clients,
    *,
    now: datetime,
    fund_id: UUID,
    period: tuple[date | None, date | None],
    settings: Settings,
) -> NavHistory:
    visible = await services.portfolios.visible(authorization)
    history = await nav_history(
        session,
        visible,
        fund_id,
        period,
        now=now,
        marketdata=services.marketdata,
        refdata=services.refdata,
        benchmark=services.benchmark,
        settings=settings,
    )
    if history is None:
        raise NotFoundError("fund", fund_id)
    return history


def _nav_filter(
    half_life: Decimal | None, trend_memory: Decimal | None
) -> tuple[Memory, Settings, bool]:
    """Настройки фильтра в днях → шумы уровня и тренда; не заданы — по умолчанию."""
    if half_life is None and trend_memory is None:
        return STANDARD_MEMORY, NAV_SETTINGS, True
    memory = Memory(
        STANDARD_MEMORY.half_life if half_life is None else half_life,
        STANDARD_MEMORY.trend_memory if trend_memory is None else trend_memory,
    )
    try:
        return memory, settings_for(memory), False
    except QuantError as exc:
        raise RuleViolationError(exc.code, exc.message) from exc


def _nav_point_out(point: NavPoint) -> NavPointOut:
    return NavPointOut(
        date=point.day,
        nav=point.nav,
        units=point.units,
        unit_price=point.unit_price,
        smoothed=None if point.smoothed is None else point.smoothed.quantize(CENT),
        benchmark=point.benchmark,
    )


def _nav_history_out(history: NavHistory, nav_filter: NavFilterOut) -> NavHistoryOut:
    forecast = history.forecast
    return NavHistoryOut(
        fund_id=history.fund.id,
        code=history.fund.code,
        name=history.fund.name,
        first_day=history.first_day,
        today=history.today,
        waiting_since=history.waiting_since,
        benchmark=history.benchmark,
        points=[_nav_point_out(p) for p in history.points],
        latest=[_nav_point_out(p) for p in history.latest],
        forecast=None
        if forecast is None
        else NavForecastOut(
            date=forecast.day,
            value=forecast.value.quantize(CENT),
            low=None if forecast.std is None else (forecast.value - forecast.std).quantize(CENT),
            high=None if forecast.std is None else (forecast.value + forecast.std).quantize(CENT),
            trend=forecast.trend.quantize(Decimal("0.0001")),
            trend_percent=(
                (forecast.trend - forecast.fees_per_day) * 365 / forecast.value * 100
            ).quantize(Decimal("0.01")),
        ),
        filter=nav_filter,
    )


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
    since: Annotated[date | None, Query(alias="from")] = None,
) -> LadderOut:
    """План по расчётам: деньги и бумаги на счёте на каждую дату расчётов с ``from``
    (по умолчанию — с сегодня), дефициты; прошлые дни — факт (решение 161)."""
    today = now.astimezone(MOSCOW).date()
    if since is not None and not today - timedelta(days=MAX_PAST_DAYS) <= since <= today:
        raise RuleViolationError(
            "bad_period",
            f"План по расчётам — с сегодня или с даты не раньше чем за {MAX_PAST_DAYS} дня.",
        )
    visible = await services.portfolios.visible(authorization)
    found = await ladder(
        session,
        services.refdata,
        visible,
        account_id,
        today,
        marketdata=services.marketdata,
        since=since,
    )
    if found is None:
        raise NotFoundError("account", account_id)
    return _ladder_out(found)


# --- день счёта для сверки -----------------------------------------------------


@router.get("/accounts/{account_id}/days", tags=["positions"])
async def get_account_days(
    account_id: UUID,
    _user: Staff,
    *,
    session: DbSession,
    authorization: Authorization,
    services: Services,
    first: Annotated[date, Query(alias="from")],
    last: Annotated[date, Query(alias="till")],
) -> AccountDaysOut:
    """Сделки дня, движения и остатки денег счёта по дням — с чем сверка сравнит выписку."""
    if last < first or (last - first).days >= MAX_ACCOUNT_DAYS:
        raise RuleViolationError(
            "bad_period",
            f"Период — от одного дня до {MAX_ACCOUNT_DAYS} дней, конец не раньше начала.",
        )
    visible = await services.portfolios.visible(authorization)
    found = await account_days(
        session, services.refdata, visible, account_id, first=first, last=last
    )
    if found is None:
        raise NotFoundError("account", account_id)
    return AccountDaysOut(
        account_id=found.account_id,
        fund_id=found.fund_id,
        portfolios=[
            LadderPortfolioOut(id=p.id, code=p.code, name=p.name) for p in found.portfolios
        ],
        waiting_since=found.waiting_since,
        days=[_account_day_out(d, found) for d in found.days],
    )


def _account_day_out(day: AccountDay, found: AccountDays) -> AccountDayOut:
    return AccountDayOut(
        date=day.day,
        trading=day.trading,
        calculated=day.calculated,
        opening_cash=day.opening,
        closing_cash=day.closing,
        trades=[
            AccountTradeOut.model_validate(
                {
                    **t._mapping,
                    "version": t.number,
                    "portfolio_code": next(
                        p.code for p in found.portfolios if p.id == t.portfolio_id
                    ),
                }
            )
            for t in day.trades
        ],
        movements=[
            AccountMovementOut(
                portfolio_code=portfolio.code,
                kind=movement.kind.value,
                cash=movement.cash,
                trade_id=movement.trade_id,
                external_id=None
                if movement.trade_id is None
                else found.deals[movement.trade_id].external_id,
                instrument_id=movement.instrument_id,
                secid=None
                if movement.instrument_id is None
                else found.names[movement.instrument_id].secid,
            )
            for portfolio, movement in day.movements
        ],
    )


# --- закрытый период -----------------------------------------------------------


def closing_desk(session: DbSession, services: Services, now: Now) -> ClosingDesk:
    return ClosingDesk(session, services.refdata, lambda: now)


Closings = Annotated[ClosingDesk, Depends(closing_desk)]


@router.get("/funds/{fund_id}/closing", tags=["closing"])
async def get_closing(
    fund_id: UUID,
    _user: Staff,
    *,
    authorization: Authorization,
    services: Services,
    closings: Closings,
) -> ClosingOut:
    """По какой день фонд закрыт, какой день закрывать следующим, кто закрывал и открывал."""
    await _fund_code(fund_id, services, authorization)
    return _closing_out(await closings.state(fund_id))


@router.post("/funds/{fund_id}/close", tags=["closing"])
async def close_day(
    fund_id: UUID, body: CloseIn, _service: DayCloser, *, closings: Closings
) -> ClosingOut:
    """Закрыть следующий день фонда. Зовёт только сервис сверки: он проверил, что все
    счета фонда сверены, и передаёт, кто закрывает."""
    actor = Actor(subject=body.closed_by.subject, username=body.closed_by.username)
    return _closing_out(await closings.close(fund_id, body.day, actor, body.fund_code))


@router.post("/funds/{fund_id}/reopen", tags=["closing"])
async def reopen_day(
    fund_id: UUID,
    body: ReopenIn,
    user: Admin,
    *,
    authorization: Authorization,
    services: Services,
    closings: Closings,
) -> ClosingOut:
    """Открыть день заново с комментарием — вместе со всеми закрытыми днями после него."""
    code = await _fund_code(fund_id, services, authorization)
    actor = Actor.from_principal(user)
    return _closing_out(await closings.reopen(fund_id, body.day, body.comment, actor, code))


async def _fund_code(fund_id: UUID, services: Clients, authorization: str) -> str:
    fund = (await services.portfolios.visible(authorization)).get(fund_id)
    if fund is None or not fund.is_fund:
        raise NotFoundError("fund", fund_id)
    return fund.code


def _closing_out(state: ClosingState) -> ClosingOut:
    return ClosingOut(
        fund_id=state.fund_id,
        closed_through=state.closed_through,
        next_day=state.next_day,
        history=[ClosingRowOut.model_validate(r, from_attributes=True) for r in state.history],
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

    async def closed(_request: Request, exc: Exception) -> Response:
        return problem(422, "day_closed", cast(ClosedPeriodError, exc).message)

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
    app.add_exception_handler(ClosedPeriodError, closed)
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
            else [
                name
                for name in TRACKED
                # Сумму сделки с бумагой считает система — меняется она вслед за полями.
                if (name != "amount" or row.instrument_id is None)
                and getattr(row, name) != getattr(previous, name)
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
    return out.model_copy(
        update={
            "versions": versions,
            "warnings": view.warnings,
            "closed_through": view.closed_through,
        }
    )


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
        result=None if state.result is None else _result_out(state.result),
    )


def _result_out(result: DayResult) -> DayResultOut:
    return DayResultOut(
        value=result.value,
        flows=result.flows,
        revaluation=result.revaluation,
        realized=result.realized,
        coupon=result.coupon,
        fees=result.fees,
        expenses=result.expenses,
        charges=result.charges,
        correction=result.correction,
        repo=result.repo,
        total=result.total,
        receivable=result.receivable,
        payable=result.payable,
        flags=[f.value for f in result.flags],
    )


def _nav_day_out(day: NavDay) -> NavDayOut:
    return NavDayOut(
        date=day.day,
        nav=day.nav,
        units=day.units,
        unit_price=day.unit_price,
        cash=day.cash,
        clean=day.clean,
        accrued=day.accrued,
        principal_due=day.principal_due,
        receivable=day.receivable,
        payable=day.payable,
        claims=day.claims,
        obligations=day.obligations,
        expenses=day.expenses,
        fees_owed=day.fees_owed,
        repo_placed=day.repo_placed,
        repo_borrowed=day.repo_borrowed,
        fees_today=day.fees_today,
        fees_due=day.fees_due,
        flags=[f.value for f in day.flags],
    )


def _result_day_out(day: ResultDay) -> ResultDayOut:
    return ResultDayOut(
        date=day.day,
        value=day.value,
        flows=day.flows,
        revaluation=day.revaluation,
        realized=day.realized,
        coupon=day.coupon,
        fees=day.fees,
        expenses=day.expenses,
        charges=day.charges,
        correction=day.correction,
        repo=day.repo,
        total=day.total,
        flags=[f.value for f in day.flags],
    )


def _result_line_out(line: ResultLine) -> ResultLineOut:
    return ResultLineOut(
        instrument_id=line.instrument_id,
        secid=line.secid,
        short_name=line.short_name,
        revaluation=line.revaluation,
        realized=line.realized,
        coupon=line.coupon,
        fees=line.fees,
        total=line.total,
    )


# Поля, по которым версия операции отличается от прежней, — в карточке «что изменилось».
OPERATION_TRACKED = (
    "status",
    "op_date",
    "portfolio_id",
    "receiver_id",
    "units",
    "amount",
    "planned_date",
    "settled_date",
    "expense_kind",
    "fee_kind",
    "period_end",
    "instrument_id",
    "quantity",
)


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
        today=found.today,
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
                        portfolio_code=codes[portfolio_id],
                        repo_term=None if m.trade_id is None else found.repo_terms.get(m.trade_id),
                        **_movement_out(m).model_dump(),
                    )
                    for portfolio_id, m in d.movements
                ],
                securities=[
                    LadderSecurityOut(instrument_id=i, quantity=q) for i, q in d.securities.items()
                ],
            )
            for d in found.days
        ],
        forecasts=[
            ForecastOut(
                instrument_id=f.instrument_id,
                date=f.pay_date,
                coupon=f.coupon,
                index=f.forecast.index,
                index_value=f.forecast.value,
                margin_bp=f.forecast.margin_bp,
                capitalized=f.forecast.capitalized,
            )
            for f in found.forecasts
        ],
    )


def _period_out(total: PeriodTotal) -> PeriodOut:
    cent = Decimal("0.01")
    return PeriodOut(result=total.result.quantize(cent), realized=total.realized.quantize(cent))


def _metrics_fields(line: PositionLine) -> dict[str, Any]:
    """Доходность, спред и тип облигации из расчёта сервиса цен; DV01 — на всю позицию."""
    metrics = line.metrics
    if metrics is None:
        return {}
    dv01 = metrics.dv01_continuous
    return {
        "bond_type": metrics.bond_type,
        "yield_percent": metrics.yield_percent,
        "yield_continuous": metrics.yield_continuous,
        "spread_bp": metrics.spread_bp,
        "spread_continuous_bp": metrics.spread_continuous_bp,
        "horizon_kind": metrics.horizon_kind,
        "horizon_date": metrics.horizon_date,
        "duration_continuous": metrics.duration_continuous,
        "dv01_continuous": None
        if dv01 is None
        else (line.trade_quantity * dv01).quantize(Decimal("0.01")),
    }


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
        price=line.price,
        clean=line.total("clean"),
        accrued_value=line.total("accrued_value"),
        coupon_due=line.total("coupon_due"),
        principal_due=line.total("principal_due"),
        unrealized=(line.total("clean") - line.total("cost")).quantize(Decimal("0.01")),
        revaluation=line.total("revaluation"),
        realized=line.total("realized"),
        coupon=line.total("coupon"),
        fees=line.total("fees"),
        result=line.total("result"),
        flags=[f.value for f in line.flags],
        dv01=None if line.dv01 is None else line.dv01.quantize(Decimal("0.01")),
        duration=None if line.metrics is None else line.metrics.duration,
        reset_duration=None if line.metrics is None else line.metrics.reset_duration,
        metrics_basis=None if line.metrics is None else line.metrics.basis,
        **_metrics_fields(line),
        periods={name: _period_out(total) for name, total in line.periods.items()},
    )
