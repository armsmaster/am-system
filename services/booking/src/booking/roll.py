"""Дневной ролл портфеля: состояние дня из вчерашнего и событий дня (design/05).

Чистые функции без базы и сети:

    состояние(D) = ролл(состояние(D − 1), сделки, условия бумаг на D)

- Экономическая позиция и лоты FIFO — по дате сделки (решение 22).
- Бумаги на счёте депо и деньги — по дате расчётов.
- Купон, амортизацию и погашение получает тот, у кого бумага на счёте депо
  на конец даты фиксации; деньги приходят в дату выплаты.

Портфели фонда роллятся вместе (``roll_fund``): перевод бумаг переносит лоты
отправителя получателю (решение 98). Операции фонда — паи и расходы —
проводятся в казначейском портфеле.

Порядок внутри дня: сделки по дате заключения → расчёты по сделкам →
операции фонда и переводы → право на выплаты по бумагам на счёте на конец
дня → выплаты дня → амортизация и погашение → оценка на конец дня.

Оценка (design/06): бумаги — по цене для оценки и НКД на дату расчётов T+1,
сделки до расчётов — задолженностью, купоны после даты фиксации — к получению.
Результат дня раскладывается на части, сумма которых точно равна изменению
стоимости портфеля за вычетом притоков: паёв и переводов.

Комиссии от СЧА (решение 21) начисляются в конце дня фонда, когда оценены все
его портфели: ставка × СЧА до сегодняшних комиссий / дней в году — в казначейский
портфель, по месяцам. Выплата за период закрывает месяцы этого периода, разница
с начисленным — корректировка в день выплаты (решения 43, 106).

РЕПО с НКЦ (design/07) — не продажа: экономическая позиция и лоты не меняются,
части сделки двигают деньги и бумаги на счёте депо. Проценты — весь денежный
поток сделки, по ночам пропорционально долгу (решения 111, 113). Купон за бумаги
в РЕПО идёт передачей дохода в дату выплаты (решение 112).
"""

from __future__ import annotations

from calendar import isleap
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

ZERO = Decimal(0)
HUNDRED = Decimal(100)
KOPECK = Decimal("0.01")


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class InstrumentKind(StrEnum):
    BOND = "bond"
    FUND_SHARE = "fund_share"  # пай биржевого фонда


@dataclass(frozen=True, slots=True)
class Trade:
    """Действующая версия сделки — то, что нужно роллу."""

    id: UUID
    instrument_id: UUID
    kind: InstrumentKind
    side: Side
    quantity: int
    price: Decimal  # облигация — % от номинала, пай — ₽
    face_value: Decimal | None  # облигация: номинал на дату расчётов
    accrued: Decimal | None  # облигация: НКД на бумагу
    amount: Decimal
    fee: Decimal
    trade_at: datetime
    trade_date: date
    settle_date: date

    @property
    def signed(self) -> int:
        return self.quantity if self.side is Side.BUY else -self.quantity


def trade_amount(
    kind: InstrumentKind,
    quantity: int,
    price: Decimal,
    face_value: Decimal | None,
    accrued: Decimal | None,
) -> Decimal:
    """Сумма сделки до копейки: облигация — количество × (цена × номинал / 100 + НКД),
    пай — количество × цена (решение 86)."""
    if kind is InstrumentKind.BOND:
        if face_value is None:
            raise ValueError("bond trade without face value")
        per_bond = price * face_value / HUNDRED + (accrued or ZERO)
        return (quantity * per_bond).quantize(KOPECK, rounding=ROUND_HALF_UP)
    return (quantity * price).quantize(KOPECK, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class Forecast:
    """Прогноз купона флоатера по текущей ставке индекса (решение 14)."""

    index: str  # key_rate | ruonia | rusfar
    value: Decimal  # ставка индекса, % годовых
    margin_bp: Decimal
    capitalized: bool


@dataclass(frozen=True, slots=True)
class Payment:
    """Выплата по бумаге в одну дату: купон и (или) погашение номинала."""

    pay_date: date
    record_date: date
    has_coupon: bool
    coupon: Decimal | None  # на бумагу; None — сумма ещё не объявлена
    principal: Decimal | None  # погашение номинала на бумагу: 0 — нет, None — не объявлено
    outstanding: Decimal | None  # непогашенный номинал после выплаты
    final: bool  # погашение: позиция закрывается
    # Купон не объявлен, сумма — прогноз. Только в лестнице: в деньги и оценку прогноз
    # не идёт, ролл фонда видит такие купоны необъявленными.
    forecast: Forecast | None = None


@dataclass(frozen=True, slots=True)
class Terms:
    """Что роллу нужно знать о бумаге на дату: выплаты по графику и номинал."""

    payments: tuple[Payment, ...] = ()
    face: Decimal | None = None  # начальный номинал на бумагу; None — неизвестен
    indexed: bool = False  # номинал индексируется: номинал на дату — из ряда ISS

    def paid_on(self, day: date) -> Payment | None:
        return next((p for p in self.payments if p.pay_date == day), None)

    def outstanding_on(self, day: date) -> Decimal | None:
        """Непогашенный номинал на дату по графику. None — по графику не посчитать:
        номинал индексируется или неизвестен, сумма погашения не объявлена."""
        if self.face is None or self.indexed:
            return None
        repaid = ZERO
        for payment in self.payments:
            if payment.pay_date > day:
                continue
            if payment.principal is None:
                return None
            repaid += payment.principal
        return max(self.face - repaid, ZERO)


# Условия бумаги по версии справочника на дату.
TermsOn = Callable[[UUID, date], Terms]


@dataclass(frozen=True, slots=True)
class Lot:
    """Открытый лот FIFO: часть покупки (или короткой продажи), ещё не закрытая."""

    trade_id: UUID
    opened_at: datetime
    quantity: int  # > 0 — длинный лот, < 0 — короткий
    price: Decimal
    face: Decimal | None  # облигация: номинал на бумагу, на который куплен лот
    accrued: Decimal  # НКД на бумагу при открытии

    @property
    def cost(self) -> Decimal:
        """Стоимость по чистой цене, ₽ (решение 88)."""
        if self.face is None:
            return self.quantity * self.price
        return self.quantity * self.price * self.face / HUNDRED

    @property
    def accrued_paid(self) -> Decimal:
        return self.quantity * self.accrued


@dataclass(frozen=True, slots=True)
class Holding:
    instrument_id: UUID
    kind: InstrumentKind
    trade_quantity: int = 0  # по дате сделки
    settle_quantity: int = 0  # на счёте депо
    lots: tuple[Lot, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.trade_quantity or self.settle_quantity or self.lots)


@dataclass(frozen=True, slots=True)
class Entitlement:
    """Право на выплату: бумаги были на счёте на конец даты фиксации. У передачи дохода
    по РЕПО — сделка: бумаги на конец даты фиксации были в РЕПО (решение 112)."""

    instrument_id: UUID
    pay_date: date
    record_date: date
    quantity: int  # передача дохода: + НКЦ платит фонду, − фонд платит НКЦ
    repo_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Contribution:
    """Компенсационный взнос по РЕПО (решение 113): деньги и бумаги — со знаком для фонда."""

    id: UUID
    day: date
    cash: Decimal = ZERO  # + фонд получил, − заплатил
    quantity: int = 0  # бумаги на счёт фонда: + пришли, − ушли


@dataclass(frozen=True, slots=True)
class Repo:
    """Действующая версия сделки РЕПО с НКЦ — то, что нужно роллу (design/07).

    Прямое: фонд отдаёт бумаги и получает деньги в первой части, во второй — наоборот.
    Обратное — зеркально. С КСУ бумаг нет: только деньги (решение 117).
    """

    id: UUID
    instrument_id: UUID | None  # обеспечение; None — КСУ
    kind: InstrumentKind | None
    direct: bool
    quantity: int  # бумаги в первой части; у КСУ — 0
    trade_at: datetime
    trade_date: date
    first_date: date
    first_amount: Decimal
    second_date: date
    second_amount: Decimal
    fee: Decimal = ZERO
    contributions: tuple[Contribution, ...] = ()

    @property
    def first_cash(self) -> Decimal:
        return self.first_amount if self.direct else -self.first_amount

    @property
    def first_bonds(self) -> int:
        return -self.quantity if self.direct else self.quantity

    def active(self) -> list[Contribution]:
        """Взносы между частями сделки: позже второй части взносов не бывает."""
        return [c for c in self.contributions if self.first_date <= c.day < self.second_date]

    def net_cash(self, day: date) -> Decimal:
        """Деньги фонду по сделке на конец дня между частями: первая часть и взносы."""
        return self.first_cash + sum((c.cash for c in self.active() if c.day <= day), ZERO)

    def bonds(self, day: date) -> int:
        """Бумаги на счёт фонда по сделке на конец дня между частями."""
        return self.first_bonds + sum(c.quantity for c in self.active() if c.day <= day)

    @property
    def second_cash(self) -> Decimal:
        return -self.second_amount if self.direct else self.second_amount

    @property
    def income(self) -> Decimal:
        """Проценты сделки для фонда: весь денежный поток; у прямого — отрицательные."""
        return self.net_cash(self.second_date - timedelta(days=1)) + self.second_cash

    def accrued(self, day: date) -> Decimal:
        """Проценты на вечер дня: доля ночей срока до этого дня, взвешенная долгом
        за ночь, нарастающим итогом до копейки (решения 111, 113)."""
        if day < self.first_date:
            return ZERO
        if day >= self.second_date:
            return self.income
        nights = [
            self.first_date + timedelta(days=n)
            for n in range((self.second_date - self.first_date).days)
        ]
        weights = [abs(self.net_cash(night)) for night in nights]
        total = sum(weights, ZERO)
        if not total:
            weights, total = [Decimal(1)] * len(nights), Decimal(len(nights))
        passed = sum((w for night, w in zip(nights, weights, strict=True) if night < day), ZERO)
        return _kopecks(self.income * passed / total)


@dataclass(frozen=True, slots=True)
class RepoPosition:
    """Открытая сделка РЕПО на конец дня: долг и начисленные проценты."""

    id: UUID
    instrument_id: UUID | None
    direct: bool
    quantity: int  # бумаги в РЕПО: прямое — отданы, обратное — получены
    principal: Decimal  # долг без процентов: первая часть с взносами
    accrued: Decimal  # начисленные проценты
    first_date: date
    second_date: date
    second_amount: Decimal

    @property
    def value(self) -> Decimal:
        """В стоимости фонда: размещение — требование, привлечение — обязательство."""
        total = self.principal + self.accrued
        return -total if self.direct else total


class MovementKind(StrEnum):
    SETTLEMENT = "settlement"  # расчёты по сделке: бумаги и деньги
    FEE = "fee"  # комиссия по сделке
    COUPON = "coupon"
    AMORTIZATION = "amortization"
    REDEMPTION = "redemption"  # погашение
    PAYMENT_UNKNOWN = "payment_unknown"  # выплата сегодня, но сумма не объявлена
    UNIT_ISSUE = "unit_issue"  # паи выданы: паёв больше, требование к пайщикам
    UNIT_REDEMPTION = "unit_redemption"  # паи погашены: паёв меньше, обязательство
    UNIT_SETTLEMENT = "unit_settlement"  # расчёт по паям: деньги
    FEE_PAYMENT = "fee_payment"  # выплата вознаграждения или комиссии от СЧА
    EXPENSE_ACCRUAL = "expense_accrual"
    EXPENSE_PAYMENT = "expense_payment"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"
    REPO_FIRST = "repo_first"  # первая часть РЕПО
    REPO_SECOND = "repo_second"  # вторая часть РЕПО
    REPO_CONTRIBUTION = "repo_contribution"  # компенсационный взнос
    COUPON_FORECAST = "coupon_forecast"  # купон флоатера по прогнозу — только в лестнице


@dataclass(frozen=True, slots=True)
class Movement:
    """Движение дня по счёту — для прослеживаемости и лестницы."""

    kind: MovementKind
    instrument_id: UUID | None  # у денежных операций бумаги нет
    trade_id: UUID | None
    quantity: int  # бумаги на счёте депо: + пришли, − ушли
    cash: Decimal  # деньги: + пришли, − ушли
    operation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Mark:
    """Рыночные данные бумаги для оценки на конец дня."""

    price: Decimal | None  # цена для оценки: облигация — чистая, %; пай — ₽ за пай
    stale: bool = False
    settle: date | None = None  # T+1: расчёты по сделке, заключённой в этот день
    accrued: Decimal | None = None  # облигация: НКД на бумагу на дату T+1 (решение 102)
    face: Decimal | None = None  # облигация: номинал на дату T+1 из ряда ISS (решение 149)


# Отметка бумаги на конец дня.
MarksOn = Callable[[UUID, date], Mark]


class Flag(StrEnum):
    """Почему оценка неполная или неточная."""

    NO_PRICE = "no_price"  # цены не было никогда — по цене покупки (решение 103)
    STALE_PRICE = "stale_price"  # цена не за этот торговый день
    NO_ACCRUED = "no_accrued"  # нет НКД на дату расчётов — НКД 0
    NO_FACE = "no_face"  # нет номинала на дату расчётов — номинал лотов
    COUPON_UNKNOWN = "coupon_unknown"  # купон к получению, сумма не объявлена
    PRINCIPAL_UNKNOWN = "principal_unknown"  # погашение к получению, сумма не объявлена


@dataclass(frozen=True, slots=True)
class Valuation:
    """Бумага в портфеле на конец дня: оценка и результат дня по ней, ₽."""

    instrument_id: UUID
    kind: InstrumentKind
    quantity: int  # по дате сделки
    price: Decimal | None
    accrued: Decimal  # НКД на бумагу в оценке
    clean: Decimal  # стоимость по чистой цене, до копейки
    accrued_value: Decimal  # НКД в оценке
    coupon_due: Decimal  # купоны к получению: дата фиксации прошла, выплаты ещё не было
    cost: Decimal  # открытые лоты по цене покупки
    revaluation: Decimal = ZERO
    realized: Decimal = ZERO
    coupon: Decimal = ZERO
    fees: Decimal = ZERO
    flags: tuple[Flag, ...] = ()
    # Погашение номинала к получению по номиналу: дата фиксации прошла (решение 149).
    principal_due: Decimal = ZERO
    face: Decimal | None = None  # облигация: номинал на бумагу в оценке

    @property
    def value(self) -> Decimal:
        return self.clean + self.accrued_value + self.coupon_due + self.principal_due

    @property
    def unrealized(self) -> Decimal:
        return self.clean - self.cost

    @property
    def result(self) -> Decimal:
        return self.revaluation + self.realized + self.coupon + self.fees


@dataclass(frozen=True, slots=True)
class DayResult:
    """Портфель на конец дня: стоимость и результат дня по частям, ₽."""

    value: Decimal  # стоимость портфеля
    flows: Decimal  # приток за день: паи, переводы из других портфелей фонда
    revaluation: Decimal = ZERO
    realized: Decimal = ZERO
    coupon: Decimal = ZERO
    fees: Decimal = ZERO  # комиссии по сделкам
    expenses: Decimal = ZERO  # начисленные расходы фонда
    charges: Decimal = ZERO  # начисленные вознаграждение и комиссии от СЧА
    correction: Decimal = ZERO  # корректировка комиссий от СЧА при выплате
    repo: Decimal = ZERO  # проценты по РЕПО: доход по обратному, расход по прямому
    receivable: Decimal = ZERO  # к получению по сделкам до расчётов
    payable: Decimal = ZERO  # к уплате по сделкам до расчётов, с комиссией
    flags: tuple[Flag, ...] = ()

    @property
    def total(self) -> Decimal:
        return (
            self.revaluation
            + self.realized
            + self.coupon
            + self.fees
            + self.expenses
            + self.charges
            + self.correction
            + self.repo
        )


@dataclass(frozen=True, slots=True)
class FeeRate:
    """Ставка комиссии от СЧА фонда: действует с ``valid_from`` до следующей того же вида."""

    kind: str  # management | specdep | registrar | other
    rate: Decimal  # % годовых
    valid_from: date
    portfolio_id: UUID  # казначейский портфель: сюда начисление


@dataclass(frozen=True, slots=True)
class State:
    day: date
    cash: Decimal = ZERO
    holdings: Mapping[UUID, Holding] = field(default_factory=dict)
    pending: tuple[Entitlement, ...] = ()
    movements: tuple[Movement, ...] = ()
    # Уровень фонда — в казначейском портфеле (решение 35).
    units: Decimal = ZERO  # паи фонда
    claims: Decimal = ZERO  # требования к пайщикам: паи выданы, деньги не пришли
    obligations: Decimal = ZERO  # обязательства перед пайщиками: паи погашены, деньги не ушли
    expenses: Decimal = ZERO  # начисленные и не выплаченные расходы
    # Оценка на конец дня; без рыночных данных (ролл позиций шага 5) — пусто.
    valuations: Mapping[UUID, Valuation] = field(default_factory=dict)
    result: DayResult | None = None
    # Комиссии от СЧА к выплате: вид → месяц «ГГГГ-ММ» → сумма; начислено сегодня по видам.
    fees_due: Mapping[str, Mapping[str, Decimal]] = field(default_factory=dict)
    fees_today: Mapping[str, Decimal] = field(default_factory=dict)
    repos: tuple[RepoPosition, ...] = ()  # открытые сделки РЕПО — с оценкой

    @property
    def fees_owed(self) -> Decimal:
        return sum((a for months in self.fees_due.values() for a in months.values()), ZERO)

    @property
    def repo_value(self) -> Decimal:
        return sum((r.value for r in self.repos), ZERO)


class OperationKind(StrEnum):
    UNIT_ISSUE = "unit_issue"
    UNIT_REDEMPTION = "unit_redemption"
    EXPENSE_ACCRUAL = "expense_accrual"
    EXPENSE_PAYMENT = "expense_payment"
    CASH_TRANSFER = "cash_transfer"
    SECURITY_TRANSFER = "security_transfer"
    FEE_PAYMENT = "fee_payment"
    # Взнос — часть своей сделки РЕПО: в ролл он идёт внутри неё, а не операцией.
    REPO_CONTRIBUTION = "repo_contribution"


@dataclass(frozen=True, slots=True)
class Operation:
    """Действующая версия операции фонда или перевода — то, что нужно роллу."""

    id: UUID
    kind: OperationKind
    portfolio_id: UUID  # паи и расходы — казначейский портфель; перевод — отправитель
    day: date
    amount: Decimal = ZERO
    units: Decimal = ZERO
    planned: date | None = None  # паи: плановая дата расчёта
    settled: date | None = None  # паи: расчёт отмечен этой датой (решение 42)
    receiver_id: UUID | None = None
    instrument_id: UUID | None = None
    instrument_kind: InstrumentKind | None = None
    quantity: int = 0
    fee_kind: str | None = None  # выплата вознаграждения: вид комиссии
    period_end: date | None = None  # выплата вознаграждения: последний день периода


def start(first_day: date) -> State:
    """Состояние накануне первого дня истории: ничего нет (решение 9)."""
    return State(day=first_day - timedelta(days=1))


@dataclass(slots=True)
class _Day:
    """Состояние портфеля в течение дня — пока его меняют события дня."""

    day: date
    holdings: dict[UUID, Holding]
    cash: Decimal
    pending: list[Entitlement]
    movements: list[Movement]
    units: Decimal
    claims: Decimal
    obligations: Decimal
    expenses: Decimal
    # Для оценки: вчерашняя оценка и события дня, которые её меняют.
    previous: State
    traded: list[Trade] = field(default_factory=list)  # по дате заключения сегодня
    unsettled: list[Trade] = field(default_factory=list)  # заключены, расчётов ещё не было
    realized: dict[UUID, Decimal] = field(default_factory=dict)
    moved: dict[UUID, tuple[int, Decimal]] = field(
        default_factory=dict
    )  # перевод: штуки, стоимость
    flows: Decimal = ZERO  # паи, переводы денег
    expensed: Decimal = ZERO  # начисленные расходы
    fees_due: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    charged: Decimal = ZERO  # корректировки вознаграждения при выплате
    repos: list[Repo] = field(default_factory=list)  # сделки РЕПО портфеля


def roll(
    previous: State,
    trades: Iterable[Trade],
    terms_on: TermsOn,
    marks_on: MarksOn | None = None,
    repos: Iterable[Repo] = (),
) -> State:
    """Состояние портфеля на конец следующего дня после ``previous``."""
    return _close(_open(previous, trades, repos), terms_on, marks_on)


def roll_fund(
    previous: Mapping[UUID, State],
    day: date,
    trades: Mapping[UUID, Sequence[Trade]],
    operations: Sequence[Operation],
    terms_on: TermsOn,
    *,
    marks_on: MarksOn | None = None,
    rates: Sequence[FeeRate] = (),
    projected: bool = False,
    repos: Mapping[UUID, Sequence[Repo]] | None = None,
) -> dict[UUID, State]:
    """Состояния всех портфелей фонда на конец ``day`` по состояниям на конец предыдущего дня.

    ``marks_on`` — рыночные данные для оценки; без них ролл ведёт только позиции.
    ``rates`` — ставки комиссий от СЧА фонда; начисляются только вместе с оценкой.
    ``projected`` — прогноз для лестницы: расчёт по паям, который ещё не отмечен,
    проходит в плановую дату.
    """
    before = day - timedelta(days=1)
    repos = repos or {}
    portfolios = (
        set(previous)
        | set(trades)
        | set(repos)
        | {o.portfolio_id for o in operations}
        | {o.receiver_id for o in operations if o.receiver_id is not None}
    )
    works = {
        p: _open(previous.get(p) or State(day=before), trades.get(p, ()), repos.get(p, ()))
        for p in portfolios
    }
    for operation in sorted(operations, key=lambda o: str(o.id)):
        _OPERATIONS[operation.kind](operation, works, day, projected)
    states = {p: _close(work, terms_on, marks_on) for p, work in works.items()}
    return states if marks_on is None else _accrue(states, day, rates)


def roll_fund_days(
    previous: Mapping[UUID, State],
    first_day: date,
    till: date,
    *,
    trades: Mapping[UUID, Sequence[Trade]],
    operations: Sequence[Operation],
    terms_on: TermsOn,
    marks_on: MarksOn | None = None,
    rates: Sequence[FeeRate] = (),
    projected: bool = False,
    repos: Mapping[UUID, Sequence[Repo]] | None = None,
) -> list[dict[UUID, State]]:
    """Состояния фонда на каждый календарный день с ``first_day`` по ``till``."""
    days: list[dict[UUID, State]] = []
    current = dict(previous)
    day = first_day
    while day <= till:
        current = roll_fund(
            current,
            day,
            trades,
            operations,
            terms_on,
            marks_on=marks_on,
            rates=rates,
            projected=projected,
            repos=repos,
        )
        days.append(current)
        day += timedelta(days=1)
    return days


def _open(previous: State, trades: Iterable[Trade], repos: Iterable[Repo] = ()) -> _Day:
    """Начало дня: сделки по дате заключения, расчёты по сделкам, части РЕПО и взносы."""
    day = previous.day + timedelta(days=1)
    todays = list(trades)
    work = _Day(
        day=day,
        holdings=dict(previous.holdings),
        cash=previous.cash,
        pending=list(previous.pending),
        movements=[],
        units=previous.units,
        claims=previous.claims,
        obligations=previous.obligations,
        expenses=previous.expenses,
        previous=previous,
        fees_due={kind: dict(months) for kind, months in previous.fees_due.items()},
        traded=[t for t in todays if t.trade_date == day],
        unsettled=[t for t in todays if t.trade_date <= day < t.settle_date],
        repos=[r for r in repos if r.trade_date <= day <= r.second_date],
    )
    _trade_day(work.holdings, work.traded, work.realized)
    work.cash = _settle(
        work.holdings, [t for t in todays if t.settle_date == day], work.cash, work.movements
    )
    for repo in _ordered_repos(work.repos):
        _repo_day(work, repo)
    return work


def _ordered_repos(repos: Iterable[Repo]) -> list[Repo]:
    return sorted(repos, key=lambda r: (r.trade_at, str(r.id)))


def _repo_day(work: _Day, repo: Repo) -> None:
    """Части РЕПО и взносы дня: деньги и бумаги на счёте депо. Позиция и лоты не меняются."""
    day = work.day
    legs: list[tuple[MovementKind, Decimal, int, UUID | None]] = []
    if repo.first_date == day:
        legs.append((MovementKind.REPO_FIRST, repo.first_cash, repo.first_bonds, None))
        if repo.fee:
            legs.append((MovementKind.FEE, -repo.fee, 0, None))
    legs += [
        (MovementKind.REPO_CONTRIBUTION, c.cash, c.quantity, c.id)
        for c in repo.active()
        if c.day == day
    ]
    if repo.second_date == day:
        returned = -repo.bonds(day - timedelta(days=1))
        legs.append((MovementKind.REPO_SECOND, repo.second_cash, returned, None))
    for kind, cash, quantity, operation_id in legs:
        work.cash += cash
        if quantity and repo.instrument_id is not None:
            # Бумаги РЕПО всегда с видом — его берёт сделка.
            current = work.holdings.get(repo.instrument_id) or Holding(
                repo.instrument_id, cast(InstrumentKind, repo.kind)
            )
            work.holdings[repo.instrument_id] = replace(
                current, settle_quantity=current.settle_quantity + quantity
            )
        work.movements.append(
            Movement(kind, repo.instrument_id, repo.id, quantity, cash, operation_id)
        )


def _close(work: _Day, terms_on: TermsOn, marks_on: MarksOn | None) -> State:
    """Конец дня: право на выплаты, выплаты, амортизация и погашение, оценка."""
    pending = [
        *work.pending,
        *_record(work.holdings, work.day, terms_on),
        *_record_repos(work.repos, work.day, terms_on),
    ]
    pending, received = _payments(pending, work.day, terms_on, work.movements)
    _amortize(work.holdings, work.day, terms_on, work.realized)
    state = State(
        day=work.day,
        cash=work.cash + received,
        holdings={k: v for k, v in work.holdings.items() if not v.empty},
        pending=tuple(pending),
        movements=tuple(work.movements),
        units=work.units,
        claims=work.claims,
        obligations=work.obligations,
        expenses=work.expenses,
        fees_due={kind: months for kind, months in work.fees_due.items() if months},
    )
    if marks_on is None:
        return state
    repos = _repo_positions(work.repos, work.day)
    state = replace(state, repos=repos)
    valuations, result = _appraise(work, state, terms_on, marks_on)
    return replace(state, valuations=valuations, result=result)


def _repo_positions(repos: Iterable[Repo], day: date) -> tuple[RepoPosition, ...]:
    """Сделки РЕПО между частями на конец дня."""
    return tuple(
        RepoPosition(
            id=r.id,
            instrument_id=r.instrument_id,
            direct=r.direct,
            quantity=abs(r.bonds(day)),
            principal=abs(r.net_cash(day)),
            accrued=abs(r.accrued(day)),
            first_date=r.first_date,
            second_date=r.second_date,
            second_amount=r.second_amount,
        )
        for r in _ordered_repos(repos)
        if r.first_date <= day < r.second_date
    )


def _record_repos(repos: Iterable[Repo], day: date, terms_on: TermsOn) -> list[Entitlement]:
    """Передача дохода: бумаги в РЕПО на конец даты фиксации. Свои бумаги, отданные
    в прямое РЕПО, — доход фонду; чужие, полученные в обратное, — доход НКЦ."""
    return [
        Entitlement(repo.instrument_id, p.pay_date, day, -repo.bonds(day), repo.id)
        for repo in _ordered_repos(repos)
        if repo.instrument_id is not None
        and repo.first_date <= day < repo.second_date
        and repo.bonds(day)
        for p in terms_on(repo.instrument_id, day).payments
        if p.record_date == day
    ]


def _unit(kind: InstrumentKind, price: Decimal, face: Decimal | None) -> Decimal:
    """Цена одной бумаги в рублях: облигация — % от номинала, пай — ₽ за пай."""
    if kind is InstrumentKind.BOND and face is not None:
        return price * face / HUNDRED
    return price


def _appraise(
    work: _Day, state: State, terms_on: TermsOn, marks_on: MarksOn
) -> tuple[dict[UUID, Valuation], DayResult]:
    """Оценка на конец дня и результат дня по бумагам (design/06).

    По каждой бумаге результат дня — сумма частей:

    - переоценка и реализованный = изменение стоимости по чистой цене + погашение
      номинала + чистая часть сделок дня − перенос переводом;
    - купонный доход = изменение НКД в оценке и купонов к получению + купоны
      деньгами + НКД в сделках дня − НКД в переносе;
    - комиссии по сделкам дня.

    Всё в копейках, поэтому части сходятся с изменением стоимости портфеля точно.
    """
    kinds = (
        {i: v.kind for i, v in work.previous.valuations.items()}
        | {t.instrument_id: t.kind for t in work.traded}
        | {i: h.kind for i, h in state.holdings.items()}
    )
    paid = _paid_today(state.movements)
    instruments = set(kinds) | {e.instrument_id for e in state.pending} | set(work.moved)
    valuations: dict[UUID, Valuation] = {}
    moved_in = ZERO
    for instrument_id in sorted(instruments, key=str):
        valuation, moved_value = _value(
            work,
            state,
            instrument_id,
            kind=kinds.get(instrument_id, InstrumentKind.BOND),
            terms_on=terms_on,
            marks_on=marks_on,
            paid=paid.get(instrument_id, (ZERO, ZERO)),
        )
        moved_in += moved_value
        if (
            valuation.quantity
            or valuation.cost
            or valuation.coupon_due
            or valuation.principal_due
            # В день погашения реализованный и обратная переоценка в сумме — ноль.
            or valuation.revaluation
            or valuation.realized
            or valuation.result
            or valuation.flags
        ):
            valuations[instrument_id] = valuation

    receivable = sum((t.amount for t in work.unsettled if t.side is Side.SELL), ZERO)
    payable = (
        sum((t.amount for t in work.unsettled if t.side is Side.BUY), ZERO)
        + sum((t.fee for t in work.unsettled), ZERO)
        # Комиссия РЕПО — расход в дату сделки, деньги — в первой части (решение 105).
        + sum((r.fee for r in work.repos if r.trade_date <= work.day < r.first_date), ZERO)
    )
    yesterday = work.day - timedelta(days=1)
    values = list(valuations.values())
    result = DayResult(
        value=state.cash
        + sum((v.value for v in values), ZERO)
        + receivable
        - payable
        + state.claims
        - state.obligations
        - state.expenses
        - state.fees_owed
        + state.repo_value,
        flows=work.flows + moved_in,
        revaluation=sum((v.revaluation for v in values), ZERO),
        realized=sum((v.realized for v in values), ZERO),
        coupon=sum((v.coupon for v in values), ZERO),
        fees=sum((v.fees for v in values), ZERO)
        - sum((r.fee for r in work.repos if r.trade_date == work.day), ZERO),
        expenses=ZERO - work.expensed,
        correction=ZERO - work.charged,
        repo=sum((r.accrued(work.day) - r.accrued(yesterday) for r in work.repos), ZERO),
        receivable=receivable,
        payable=payable,
        flags=tuple(sorted({f for v in values for f in v.flags})),
    )
    return valuations, result


def remark(state: State, marks_on: MarksOn) -> State:
    """Оценка дня по текущим ценам — переоценка внутри дня (решение 68).

    Меняется стоимость по чистой цене, а с ней — переоценка и стоимость портфеля.
    НКД на T+1, номинал на T+1 и выплаты к получению внутри дня не меняются.
    """
    if state.result is None:
        return state
    valuations: dict[UUID, Valuation] = {}
    delta = ZERO
    for instrument_id, valuation in state.valuations.items():
        mark = marks_on(instrument_id, state.day) if valuation.quantity else Mark(price=None)
        if mark.price is None:
            valuations[instrument_id] = valuation
            continue
        holding = state.holdings.get(instrument_id)
        lots = () if holding is None else holding.lots
        # Снапшот до решения 149 номинала не хранит — номинал отметки или лотов.
        face = valuation.face
        if face is None:
            face = mark.face or next((lot.face for lot in lots if lot.face is not None), None)
        clean = _kopecks(valuation.quantity * _unit(valuation.kind, mark.price, face))
        flags: list[Flag] = [
            f for f in valuation.flags if f not in {Flag.NO_PRICE, Flag.STALE_PRICE}
        ]
        if mark.stale:
            flags.append(Flag.STALE_PRICE)
        delta += clean - valuation.clean
        valuations[instrument_id] = replace(
            valuation,
            price=mark.price,
            clean=clean,
            revaluation=valuation.revaluation + clean - valuation.clean,
            flags=tuple(flags),
        )
    result = replace(
        state.result,
        value=state.result.value + delta,
        revaluation=state.result.revaluation + delta,
        flags=tuple(sorted({f for v in valuations.values() for f in v.flags})),
    )
    return replace(state, valuations=valuations, result=result)


def _paid_today(movements: Iterable[Movement]) -> dict[UUID, tuple[Decimal, Decimal]]:
    """Выплаты деньгами сегодня по бумагам: купоны и погашение номинала."""
    paid: dict[UUID, tuple[Decimal, Decimal]] = {}
    for movement in movements:
        if movement.instrument_id is None:
            continue
        coupons, principal = paid.get(movement.instrument_id, (ZERO, ZERO))
        if movement.kind is MovementKind.COUPON:
            coupons += movement.cash
        elif movement.kind in {MovementKind.AMORTIZATION, MovementKind.REDEMPTION}:
            principal += movement.cash
        paid[movement.instrument_id] = (coupons, principal)
    return paid


def _value(
    work: _Day,
    state: State,
    instrument_id: UUID,
    *,
    kind: InstrumentKind,
    terms_on: TermsOn,
    marks_on: MarksOn,
    paid: tuple[Decimal, Decimal],
) -> tuple[Valuation, Decimal]:
    """Оценка одной бумаги и её результат дня; второе — приток бумагами переводом."""
    day = work.day
    holding = state.holdings.get(instrument_id)
    quantity = 0 if holding is None else holding.trade_quantity
    lots = () if holding is None else holding.lots
    moved, moved_cost = work.moved.get(instrument_id, (0, ZERO))
    entitled = [e for e in state.pending if e.instrument_id == instrument_id]
    held = bool(quantity or moved)
    mark = marks_on(instrument_id, day) if held or entitled else Mark(price=None)
    terms = terms_on(instrument_id, day)
    flags: list[Flag] = []
    cost = sum((lot.cost for lot in lots), ZERO)

    face = _face(terms, mark, day) if kind is InstrumentKind.BOND else None
    if kind is InstrumentKind.BOND and face is None and held:
        face = next((lot.face for lot in lots if lot.face is not None), None)
        flags.append(Flag.NO_FACE)
    unit = None if mark.price is None else _unit(kind, mark.price, face)
    if quantity and mark.price is None:
        flags.append(Flag.NO_PRICE)
    if quantity and mark.stale:
        flags.append(Flag.STALE_PRICE)
    clean = _kopecks(quantity * unit if unit is not None else cost)

    accrued = ZERO
    if kind is InstrumentKind.BOND and held:
        if mark.accrued is None:
            flags.append(Flag.NO_ACCRUED)
        else:
            accrued = mark.accrued - _coupon_after_record(terms, day, mark.settle)
    accrued_value = _kopecks(quantity * accrued)
    coupon_due, unknown = _coupon_due(entitled, terms)
    if unknown:
        flags.append(Flag.COUPON_UNKNOWN)
    principal_due, unknown = _principal_due(entitled, terms)
    if unknown:
        flags.append(Flag.PRINCIPAL_UNKNOWN)
    clean_flow, accrued_flow, fees = _traded(work.traded, instrument_id)

    moved_clean = _kopecks(moved * unit if unit is not None else moved_cost)
    moved_accrued = _kopecks(moved * accrued)
    coupons, principal = paid
    previous = work.previous.valuations.get(instrument_id)
    price_effect = (
        clean
        + principal_due
        - (ZERO if previous is None else previous.clean + previous.principal_due)
        + principal
        + clean_flow
        - moved_clean
    )
    coupon = (
        accrued_value
        + coupon_due
        - (ZERO if previous is None else previous.accrued_value + previous.coupon_due)
        + coupons
        + accrued_flow
        - moved_accrued
    )
    realized = _kopecks(work.realized.get(instrument_id, ZERO))
    valuation = Valuation(
        instrument_id=instrument_id,
        kind=kind,
        quantity=quantity,
        price=mark.price,
        accrued=accrued,
        clean=clean,
        accrued_value=accrued_value,
        coupon_due=coupon_due,
        cost=cost,
        revaluation=price_effect - realized,
        realized=realized,
        coupon=coupon,
        fees=fees,
        flags=tuple(dict.fromkeys(flags)),
        principal_due=principal_due,
        face=face,
    )
    return valuation, moved_clean + moved_accrued


def _face(terms: Terms, mark: Mark, day: date) -> Decimal | None:
    """Номинал бумаги в оценке — на дату расчётов T+1, как НКД (решение 149).

    Обычной облигации — по графику погашений, линкера — из ряда ISS. Погашение,
    право на которое уже зафиксировано, бумаге не принадлежит: покупатель с расчётами
    до выплаты его не получит, оно — к получению. После даты фиксации окончательного
    погашения бумага по чистой цене не стоит ничего.
    """
    settle = mark.settle or day
    fixed = [p for p in terms.payments if p.record_date <= day < p.pay_date]
    if any(p.final for p in fixed):
        return ZERO
    scheduled = terms.outstanding_on(settle)
    face = scheduled if scheduled is not None else mark.face
    if face is None:
        return None
    # Дата фиксации раньше, чем за день до выплаты: номинал на T+1 погашение ещё содержит.
    return face - sum((p.principal or ZERO for p in fixed if p.pay_date > settle), ZERO)


def _coupon_after_record(terms: Terms, day: date, settle: date | None) -> Decimal:
    """Купон, который НКД на T+1 ещё содержит, а покупатель уже не получит: дата
    фиксации прошла, расчёты раньше выплаты. Он — к получению, не в НКД бумаги."""
    return sum(
        (
            p.coupon
            for p in terms.payments
            if p.coupon is not None
            and p.record_date <= day < p.pay_date
            and settle is not None
            and settle < p.pay_date
        ),
        ZERO,
    )


def _coupon_due(entitled: Iterable[Entitlement], terms: Terms) -> tuple[Decimal, bool]:
    """Купоны к получению по правам на выплату; второе — есть купон с необъявленной суммой."""
    due = ZERO
    unknown = False
    for entitlement in entitled:
        payment = terms.paid_on(entitlement.pay_date)
        if payment is None or not payment.has_coupon:
            continue
        if payment.coupon is None:
            unknown = True
            continue
        due += _kopecks(entitlement.quantity * payment.coupon)
    return due, unknown


def _principal_due(entitled: Iterable[Entitlement], terms: Terms) -> tuple[Decimal, bool]:
    """Погашения номинала к получению по правам на выплату — по номиналу, как вернёт
    эмитент; второе — есть погашение с необъявленной суммой."""
    due = ZERO
    unknown = False
    for entitlement in entitled:
        payment = terms.paid_on(entitlement.pay_date)
        if payment is None:
            continue
        if payment.principal is None:
            unknown = True
            continue
        due += _kopecks(entitlement.quantity * payment.principal)
    return due, unknown


def _traded(trades: Iterable[Trade], instrument_id: UUID) -> tuple[Decimal, Decimal, Decimal]:
    """Сделки дня по бумаге: чистая часть суммы, НКД в сумме, комиссии — со знаком для фонда."""
    clean = accrued = fees = ZERO
    for trade in trades:
        if trade.instrument_id != instrument_id:
            continue
        sign = 1 if trade.side is Side.SELL else -1
        in_trade = trade.quantity * (trade.accrued or ZERO)
        clean += sign * (trade.amount - in_trade)
        accrued += sign * in_trade
        fees -= trade.fee
    return clean, accrued, fees


def _rates_on(rates: Iterable[FeeRate], day: date) -> dict[str, FeeRate]:
    """Действующая ставка каждого вида: последняя с датой начала не позже дня."""
    active: dict[str, FeeRate] = {}
    for rate in sorted(rates, key=lambda r: r.valid_from):
        if rate.valid_from <= day:
            active[rate.kind] = rate
    return active


def _accrue(states: dict[UUID, State], day: date, rates: Sequence[FeeRate]) -> dict[UUID, State]:
    """Начисление комиссий от СЧА за день: ставка × СЧА до сегодняшних комиссий / дней в году.

    СЧА — стоимость всех портфелей фонда на конец дня. Все виды считаются от одной
    и той же СЧА: комиссия не зависит ни от себя, ни от соседней.
    """
    active = _rates_on(rates, day)
    nav = sum((s.result.value for s in states.values() if s.result is not None), ZERO)
    year = 366 if isleap(day.year) else 365
    month = day.strftime("%Y-%m")
    for kind, rate in sorted(active.items()):
        amount = _kopecks(rate.rate / HUNDRED * nav / year)
        if not amount:
            continue
        state = states.get(rate.portfolio_id) or State(
            day=day, result=DayResult(value=ZERO, flows=ZERO)
        )
        result = state.result or DayResult(value=ZERO, flows=ZERO)
        due = {k: dict(v) for k, v in state.fees_due.items()}
        months = due.setdefault(kind, {})
        months[month] = months.get(month, ZERO) + amount
        states[rate.portfolio_id] = replace(
            state,
            fees_due=due,
            fees_today={**state.fees_today, kind: amount},
            result=replace(result, value=result.value - amount, charges=result.charges - amount),
        )
    return states


def _fee_payment(
    operation: Operation, works: dict[UUID, _Day], day: date, _projected: bool
) -> None:
    """Выплата за период: закрывает начисления по месяц конца периода включительно,
    разница с фактической суммой — корректировка в день выплаты (решения 43, 106)."""
    if operation.day != day:
        return
    work = works[operation.portfolio_id]
    # Вид комиссии и конец периода у выплаты есть всегда — проверено при вводе.
    kind, last = cast(str, operation.fee_kind), cast(date, operation.period_end)
    months = work.fees_due.get(kind, {})
    paid_for = [m for m in months if m <= last.strftime("%Y-%m")]
    closed = sum((months.pop(m) for m in paid_for), ZERO)
    work.charged += operation.amount - closed
    work.cash -= operation.amount
    work.movements.append(
        Movement(MovementKind.FEE_PAYMENT, None, None, 0, -operation.amount, operation.id)
    )


def _units(operation: Operation, works: dict[UUID, _Day], day: date, projected: bool) -> None:
    """Выдача и погашение паёв: паи и требование (обязательство) — в дату операции,
    деньги — в дату расчёта, которую отмечает сопровождение (решение 42)."""
    work = works[operation.portfolio_id]
    issue = operation.kind is OperationKind.UNIT_ISSUE
    sign = 1 if issue else -1
    if operation.day == day:
        work.units += sign * operation.units
        work.flows += sign * operation.amount
        if issue:
            work.claims += operation.amount
        else:
            work.obligations += operation.amount
        kind = MovementKind.UNIT_ISSUE if issue else MovementKind.UNIT_REDEMPTION
        work.movements.append(Movement(kind, None, None, 0, ZERO, operation.id))
    settled = operation.settled or (operation.planned if projected else None)
    if settled == day:
        work.cash += sign * operation.amount
        if issue:
            work.claims -= operation.amount
        else:
            work.obligations -= operation.amount
        work.movements.append(
            Movement(
                MovementKind.UNIT_SETTLEMENT, None, None, 0, sign * operation.amount, operation.id
            )
        )


def _expense(operation: Operation, works: dict[UUID, _Day], day: date, _projected: bool) -> None:
    """Начисление расхода — обязательство; выплата — деньги уходят, обязательство меньше."""
    if operation.day != day:
        return
    work = works[operation.portfolio_id]
    if operation.kind is OperationKind.EXPENSE_ACCRUAL:
        work.expenses += operation.amount
        work.expensed += operation.amount
        work.movements.append(
            Movement(MovementKind.EXPENSE_ACCRUAL, None, None, 0, ZERO, operation.id)
        )
        return
    work.cash -= operation.amount
    work.expenses -= operation.amount
    work.movements.append(
        Movement(MovementKind.EXPENSE_PAYMENT, None, None, 0, -operation.amount, operation.id)
    )


def _transfer(operation: Operation, works: dict[UUID, _Day], day: date, _projected: bool) -> None:
    """Перевод между портфелями фонда в дату исполнения — по обеим осям сразу (решение 37).
    Бумаги переходят лотами отправителя по FIFO, без финансового результата (решение 98)."""
    if operation.day != day:
        return
    # Получатель, бумага и её вид у перевода есть всегда — проверено при вводе.
    sender, receiver = works[operation.portfolio_id], works[cast(UUID, operation.receiver_id)]
    if operation.kind is OperationKind.CASH_TRANSFER:
        sender.cash -= operation.amount
        receiver.cash += operation.amount
        sender.flows -= operation.amount
        receiver.flows += operation.amount
        sender.movements.append(
            Movement(MovementKind.TRANSFER_OUT, None, None, 0, -operation.amount, operation.id)
        )
        receiver.movements.append(
            Movement(MovementKind.TRANSFER_IN, None, None, 0, operation.amount, operation.id)
        )
        return
    instrument, quantity = cast(UUID, operation.instrument_id), operation.quantity
    kind = cast(InstrumentKind, operation.instrument_kind)
    given = sender.holdings.get(instrument) or Holding(instrument, kind)
    taken, left = take(given.lots, quantity)
    sender.holdings[instrument] = replace(
        given,
        trade_quantity=given.trade_quantity - quantity,
        settle_quantity=given.settle_quantity - quantity,
        lots=left,
    )
    got = receiver.holdings.get(instrument) or Holding(instrument, kind)
    receiver.holdings[instrument] = replace(
        got,
        trade_quantity=got.trade_quantity + quantity,
        settle_quantity=got.settle_quantity + quantity,
        lots=tuple(sorted([*got.lots, *taken], key=lambda lot: (lot.opened_at, str(lot.trade_id)))),
    )
    # Стоимость переноса для оценки — по цене дня; без цены — по лотам (решение 103).
    cost = sum((lot.cost for lot in taken), ZERO)
    for work, sign in ((sender, -1), (receiver, 1)):
        moved, moved_cost = work.moved.get(instrument, (0, ZERO))
        work.moved[instrument] = (moved + sign * quantity, moved_cost + sign * cost)
    sender.movements.append(
        Movement(MovementKind.TRANSFER_OUT, instrument, None, -quantity, ZERO, operation.id)
    )
    receiver.movements.append(
        Movement(MovementKind.TRANSFER_IN, instrument, None, quantity, ZERO, operation.id)
    )


_OPERATIONS: Mapping[OperationKind, Callable[[Operation, dict[UUID, _Day], date, bool], None]] = {
    OperationKind.UNIT_ISSUE: _units,
    OperationKind.UNIT_REDEMPTION: _units,
    OperationKind.EXPENSE_ACCRUAL: _expense,
    OperationKind.EXPENSE_PAYMENT: _expense,
    OperationKind.CASH_TRANSFER: _transfer,
    OperationKind.SECURITY_TRANSFER: _transfer,
    OperationKind.FEE_PAYMENT: _fee_payment,
}


def take(lots: tuple[Lot, ...], quantity: int) -> tuple[tuple[Lot, ...], tuple[Lot, ...]]:
    """Перевод забирает ``quantity`` бумаг из длинных лотов с самого раннего:
    (взятые лоты, оставшиеся)."""
    taken: list[Lot] = []
    left = list(lots)
    remaining = quantity
    while remaining and left and left[0].quantity > 0:
        first = left[0]
        if first.quantity <= remaining:
            taken.append(first)
            left.pop(0)
            remaining -= first.quantity
        else:
            taken.append(replace(first, quantity=remaining))
            left[0] = replace(first, quantity=first.quantity - remaining)
            remaining = 0
    return tuple(taken), tuple(left)


def _holding(holdings: dict[UUID, Holding], trade: Trade) -> Holding:
    return holdings.get(trade.instrument_id) or Holding(trade.instrument_id, trade.kind)


def _trade_day(
    holdings: dict[UUID, Holding], trades: Iterable[Trade], realized: dict[UUID, Decimal]
) -> None:
    """Сделки по дате заключения: экономическая позиция, лоты FIFO, реализованный результат."""
    for trade in _ordered(trades):
        current = _holding(holdings, trade)
        lots, closed = _fifo(current.lots, trade)
        holdings[trade.instrument_id] = replace(
            current, trade_quantity=current.trade_quantity + trade.signed, lots=lots
        )
        if closed:
            realized[trade.instrument_id] = realized.get(trade.instrument_id, ZERO) + closed


def _settle(
    holdings: dict[UUID, Holding], trades: Iterable[Trade], cash: Decimal, movements: list[Movement]
) -> Decimal:
    """Расчёты по сделкам: бумаги на счёте депо, деньги, комиссия."""
    for trade in _ordered(trades):
        current = _holding(holdings, trade)
        holdings[trade.instrument_id] = replace(
            current, settle_quantity=current.settle_quantity + trade.signed
        )
        money = trade.amount if trade.side is Side.SELL else -trade.amount
        cash += money
        movements.append(
            Movement(MovementKind.SETTLEMENT, trade.instrument_id, trade.id, trade.signed, money)
        )
        if trade.fee:
            cash -= trade.fee
            movements.append(
                Movement(MovementKind.FEE, trade.instrument_id, trade.id, 0, -trade.fee)
            )
    return cash


def _record(holdings: Mapping[UUID, Holding], day: date, terms_on: TermsOn) -> list[Entitlement]:
    """Дата фиксации: право на выплату — по бумагам на счёте депо на конец дня."""
    return [
        Entitlement(instrument_id, p.pay_date, day, current.settle_quantity)
        for instrument_id, current in holdings.items()
        if current.settle_quantity
        for p in terms_on(instrument_id, day).payments
        if p.record_date == day
    ]


def _payments(
    pending: Iterable[Entitlement], day: date, terms_on: TermsOn, movements: list[Movement]
) -> tuple[list[Entitlement], Decimal]:
    """Выплаты по наступившим правам; сумма не объявлена — право ждёт."""
    still: list[Entitlement] = []
    received = ZERO
    for entitlement in pending:
        if entitlement.pay_date > day:
            still.append(entitlement)
            continue
        payment = terms_on(entitlement.instrument_id, day).paid_on(entitlement.pay_date)
        if payment is None:
            # Выплату убрали из графика новой версией бумаги — права больше нет.
            continue
        paid = _pay(entitlement, payment)
        if paid is None:
            still.append(entitlement)
            movements.append(
                Movement(
                    MovementKind.PAYMENT_UNKNOWN,
                    entitlement.instrument_id,
                    entitlement.repo_id,
                    0,
                    ZERO,
                )
            )
            continue
        received += sum((m.cash for m in paid), ZERO)
        movements += paid
    return still, received


def _amortize(
    holdings: dict[UUID, Holding], day: date, terms_on: TermsOn, realized: dict[UUID, Decimal]
) -> None:
    """Погашение закрывает позицию; амортизация — лоты стоят от непогашенного номинала.
    Эмитент возвращает номинал по 100%, а лоты куплены по своей цене — это реализованный
    результат."""
    for instrument_id, current in list(holdings.items()):
        payment = terms_on(instrument_id, day).paid_on(day)
        if payment is None:
            continue
        outstanding = payment.outstanding or ZERO
        if payment.final:
            holdings[instrument_id] = Holding(instrument_id, current.kind)
        elif payment.outstanding is not None:
            holdings[instrument_id] = replace(
                current,
                lots=tuple(
                    replace(lot, face=min(lot.face, outstanding)) if lot.face is not None else lot
                    for lot in current.lots
                ),
            )
        else:
            continue
        # Лоты облигации всегда с номиналом.
        repaid = sum(
            (
                lot.quantity
                * (lot.face - (ZERO if payment.final else min(lot.face, outstanding)))
                * (1 - lot.price / HUNDRED)
                for lot in current.lots
                if lot.face is not None
            ),
            ZERO,
        )
        if repaid:
            realized[instrument_id] = realized.get(instrument_id, ZERO) + repaid


def roll_days(
    previous: State, till: date, trades: Sequence[Trade], terms_on: TermsOn
) -> list[State]:
    """Состояния на каждый календарный день после ``previous`` по ``till`` включительно."""
    states: list[State] = []
    current = previous
    while current.day < till:
        current = roll(current, trades, terms_on)
        states.append(current)
    return states


def fifo(lots: tuple[Lot, ...], trade: Trade) -> tuple[Lot, ...]:
    """Сделка закрывает встречные лоты с самого раннего; остаток открывает новый лот."""
    return _fifo(lots, trade)[0]


def _fifo(lots: tuple[Lot, ...], trade: Trade) -> tuple[tuple[Lot, ...], Decimal]:
    """Лоты после сделки и реализованный результат закрытых лотов по чистой цене."""
    remaining = trade.signed
    result = list(lots)
    unit = _unit(trade.kind, trade.price, trade.face_value)
    realized = ZERO
    while remaining and result and (result[0].quantity > 0) != (remaining > 0):
        first = result[0]
        closed = first.quantity if abs(first.quantity) <= abs(remaining) else -remaining
        # Закрыли длинный лот продажей — сделка минус лот; короткий покупкой — наоборот.
        realized += closed * unit - first.cost * Decimal(closed) / Decimal(first.quantity)
        if abs(first.quantity) <= abs(remaining):
            remaining += first.quantity
            result.pop(0)
        else:
            result[0] = replace(first, quantity=first.quantity + remaining)
            remaining = 0
    if remaining:
        result.append(
            Lot(
                trade_id=trade.id,
                opened_at=trade.trade_at,
                quantity=remaining,
                price=trade.price,
                face=trade.face_value,
                accrued=trade.accrued or ZERO,
            )
        )
    return tuple(result), realized


def _kopecks(value: Decimal) -> Decimal:
    return value.quantize(KOPECK, rounding=ROUND_HALF_UP)


def _ordered(trades: Iterable[Trade]) -> list[Trade]:
    return sorted(trades, key=lambda t: (t.trade_at, str(t.id)))


def _pay(entitlement: Entitlement, payment: Payment) -> list[Movement] | None:
    """Деньги по праву на выплату; None — сумма купона или погашения не объявлена."""
    if (payment.has_coupon and payment.coupon is None) or payment.principal is None:
        return None
    movements: list[Movement] = []
    quantity = entitlement.quantity
    # Передача дохода по РЕПО — те же выплаты со ссылкой на сделку: бумаги на счёте
    # она не двигает, их возвращает вторая часть.
    repo = entitlement.repo_id
    if payment.coupon is not None:
        movements.append(
            Movement(
                MovementKind.COUPON if payment.forecast is None else MovementKind.COUPON_FORECAST,
                entitlement.instrument_id,
                repo,
                0,
                _kopecks(quantity * payment.coupon),
            )
        )
    if payment.principal:
        kind = MovementKind.REDEMPTION if payment.final else MovementKind.AMORTIZATION
        movements.append(
            Movement(
                kind,
                entitlement.instrument_id,
                repo,
                -quantity if payment.final and repo is None else 0,
                _kopecks(quantity * payment.principal),
            )
        )
    return movements


# --- снапшот в JSON и обратно -----------------------------------------------------


def to_json(state: State) -> dict[str, Any]:
    return {
        "day": state.day.isoformat(),
        "cash": str(state.cash),
        "holdings": [
            {
                "instrument_id": str(h.instrument_id),
                "kind": h.kind.value,
                "trade_quantity": h.trade_quantity,
                "settle_quantity": h.settle_quantity,
                "lots": [
                    {
                        "trade_id": str(lot.trade_id),
                        "opened_at": lot.opened_at.isoformat(),
                        "quantity": lot.quantity,
                        "price": str(lot.price),
                        "face": None if lot.face is None else str(lot.face),
                        "accrued": str(lot.accrued),
                    }
                    for lot in h.lots
                ],
            }
            for h in sorted(state.holdings.values(), key=lambda h: str(h.instrument_id))
        ],
        "pending": [
            {
                "instrument_id": str(e.instrument_id),
                "pay_date": e.pay_date.isoformat(),
                "record_date": e.record_date.isoformat(),
                "quantity": e.quantity,
                "repo_id": None if e.repo_id is None else str(e.repo_id),
            }
            for e in state.pending
        ],
        "movements": [
            {
                "kind": m.kind.value,
                "instrument_id": None if m.instrument_id is None else str(m.instrument_id),
                "trade_id": None if m.trade_id is None else str(m.trade_id),
                "operation_id": None if m.operation_id is None else str(m.operation_id),
                "quantity": m.quantity,
                "cash": str(m.cash),
            }
            for m in state.movements
        ],
        "units": str(state.units),
        "claims": str(state.claims),
        "obligations": str(state.obligations),
        "expenses": str(state.expenses),
        "valuations": [
            {
                "instrument_id": str(v.instrument_id),
                "kind": v.kind.value,
                "quantity": v.quantity,
                "price": None if v.price is None else str(v.price),
                "accrued": str(v.accrued),
                "clean": str(v.clean),
                "accrued_value": str(v.accrued_value),
                "coupon_due": str(v.coupon_due),
                "principal_due": str(v.principal_due),
                "face": None if v.face is None else str(v.face),
                "cost": str(v.cost),
                "revaluation": str(v.revaluation),
                "realized": str(v.realized),
                "coupon": str(v.coupon),
                "fees": str(v.fees),
                "flags": [f.value for f in v.flags],
            }
            for v in state.valuations.values()
        ],
        "result": None if state.result is None else _result_json(state.result),
        "fees_due": {
            kind: {month: str(amount) for month, amount in months.items()}
            for kind, months in state.fees_due.items()
        },
        "fees_today": {kind: str(amount) for kind, amount in state.fees_today.items()},
        "repos": [
            {
                "id": str(r.id),
                "instrument_id": None if r.instrument_id is None else str(r.instrument_id),
                "direct": r.direct,
                "quantity": r.quantity,
                "principal": str(r.principal),
                "accrued": str(r.accrued),
                "first_date": r.first_date.isoformat(),
                "second_date": r.second_date.isoformat(),
                "second_amount": str(r.second_amount),
            }
            for r in state.repos
        ],
    }


def _result_json(result: DayResult) -> dict[str, Any]:
    return {
        "value": str(result.value),
        "flows": str(result.flows),
        "revaluation": str(result.revaluation),
        "realized": str(result.realized),
        "coupon": str(result.coupon),
        "fees": str(result.fees),
        "expenses": str(result.expenses),
        "charges": str(result.charges),
        "correction": str(result.correction),
        "repo": str(result.repo),
        "receivable": str(result.receivable),
        "payable": str(result.payable),
        "flags": [f.value for f in result.flags],
    }


def from_json(data: Mapping[str, Any]) -> State:
    holdings = {}
    for h in data["holdings"]:
        holding = Holding(
            instrument_id=UUID(h["instrument_id"]),
            kind=InstrumentKind(h["kind"]),
            trade_quantity=h["trade_quantity"],
            settle_quantity=h["settle_quantity"],
            lots=tuple(
                Lot(
                    trade_id=UUID(lot["trade_id"]),
                    opened_at=datetime.fromisoformat(lot["opened_at"]),
                    quantity=lot["quantity"],
                    price=Decimal(lot["price"]),
                    face=None if lot["face"] is None else Decimal(lot["face"]),
                    accrued=Decimal(lot["accrued"]),
                )
                for lot in h["lots"]
            ),
        )
        holdings[holding.instrument_id] = holding
    return State(
        day=date.fromisoformat(data["day"]),
        cash=Decimal(data["cash"]),
        holdings=holdings,
        pending=tuple(
            Entitlement(
                instrument_id=UUID(e["instrument_id"]),
                pay_date=date.fromisoformat(e["pay_date"]),
                record_date=date.fromisoformat(e["record_date"]),
                quantity=e["quantity"],
                repo_id=_uuid(e.get("repo_id")),
            )
            for e in data["pending"]
        ),
        movements=tuple(
            Movement(
                kind=MovementKind(m["kind"]),
                instrument_id=_uuid(m["instrument_id"]),
                trade_id=_uuid(m["trade_id"]),
                quantity=m["quantity"],
                cash=Decimal(m["cash"]),
                operation_id=_uuid(m.get("operation_id")),
            )
            for m in data["movements"]
        ),
        units=Decimal(data.get("units", "0")),
        claims=Decimal(data.get("claims", "0")),
        obligations=Decimal(data.get("obligations", "0")),
        expenses=Decimal(data.get("expenses", "0")),
        valuations={
            UUID(v["instrument_id"]): Valuation(
                instrument_id=UUID(v["instrument_id"]),
                kind=InstrumentKind(v["kind"]),
                quantity=v["quantity"],
                price=None if v["price"] is None else Decimal(v["price"]),
                accrued=Decimal(v["accrued"]),
                clean=Decimal(v["clean"]),
                accrued_value=Decimal(v["accrued_value"]),
                coupon_due=Decimal(v["coupon_due"]),
                cost=Decimal(v["cost"]),
                revaluation=Decimal(v["revaluation"]),
                realized=Decimal(v["realized"]),
                coupon=Decimal(v["coupon"]),
                fees=Decimal(v["fees"]),
                flags=tuple(Flag(f) for f in v["flags"]),
                # Снапшоты до решения 149: погашений к получению и номинала в них нет.
                principal_due=Decimal(v.get("principal_due", "0")),
                face=None if v.get("face") is None else Decimal(v["face"]),
            )
            for v in data.get("valuations", [])
        },
        result=_result(data.get("result")),
        fees_due={
            kind: {month: Decimal(amount) for month, amount in months.items()}
            for kind, months in data.get("fees_due", {}).items()
        },
        fees_today={kind: Decimal(a) for kind, a in data.get("fees_today", {}).items()},
        repos=tuple(
            RepoPosition(
                id=UUID(r["id"]),
                instrument_id=_uuid(r["instrument_id"]),
                direct=r["direct"],
                quantity=r["quantity"],
                principal=Decimal(r["principal"]),
                accrued=Decimal(r["accrued"]),
                first_date=date.fromisoformat(r["first_date"]),
                second_date=date.fromisoformat(r["second_date"]),
                second_amount=Decimal(r["second_amount"]),
            )
            for r in data.get("repos", [])
        ),
    )


def _result(data: Mapping[str, Any] | None) -> DayResult | None:
    if data is None:
        return None
    return DayResult(
        value=Decimal(data["value"]),
        flows=Decimal(data["flows"]),
        revaluation=Decimal(data["revaluation"]),
        realized=Decimal(data["realized"]),
        coupon=Decimal(data["coupon"]),
        fees=Decimal(data["fees"]),
        expenses=Decimal(data["expenses"]),
        charges=Decimal(data.get("charges", "0")),
        correction=Decimal(data.get("correction", "0")),
        repo=Decimal(data.get("repo", "0")),
        receivable=Decimal(data["receivable"]),
        payable=Decimal(data["payable"]),
        flags=tuple(Flag(f) for f in data["flags"]),
    )


def _uuid(value: str | None) -> UUID | None:
    return None if value is None else UUID(value)
