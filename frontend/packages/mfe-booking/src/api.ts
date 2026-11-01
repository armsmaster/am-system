import type { ApiClient } from '@am/platform';

/** Десятичное число строкой, как его отдаёт сервис: так не теряется точность. */
export type Decimal = string;
export type Side = 'buy' | 'sell';
export type Status = 'entered' | 'confirmed' | 'cancelled';

export interface Portfolio {
  readonly id: string;
  readonly code: string;
  readonly name: string;
  readonly kind: 'compound' | 'physical';
  readonly is_fund: boolean;
  readonly parent_ids: readonly string[];
  readonly fund_id: string | null;
  readonly account_id?: string | null;
  /** Только у фонда: сюда проводятся паи и расходы (решение 35). */
  readonly treasury_id?: string | null;
}

/** Пара счетов фонда: денежный и депо. */
export interface Account {
  readonly id: string;
  readonly fund_id: string;
  readonly code: string;
  readonly name: string;
  readonly cash_kind: 'broker' | 'bank';
  readonly institution: string;
}

export interface Instrument {
  readonly id: string;
  readonly secid: string;
  readonly kind: 'bond' | 'fund_share';
  readonly short_name: string;
}

/** Подсказка для формы: дата расчётов T+1, номинал и НКД на неё (решение 86). */
export interface Defaults {
  readonly instrument_id: string;
  readonly kind: 'bond' | 'fund_share';
  readonly settle_date: string;
  readonly face_value: Decimal | null;
  readonly accrued: Decimal | null;
}

export interface TradeVersion {
  readonly number: number;
  readonly status: Status;
  readonly author: string;
  readonly created_at: string;
  readonly comment: string | null;
  /** Какие поля изменились относительно предыдущей версии. */
  readonly changed: readonly string[];
}

/** Покупка или продажа; РЕПО с НКЦ (design/07): сторона — первой части, sell — прямое. */
export type TradeKind = 'outright' | 'repo';

export interface Trade {
  readonly id: string;
  readonly number: number;
  readonly status: Status;
  readonly portfolio_id: string;
  readonly portfolio_code: string | null;
  /** У РЕПО с КСУ бумаги нет. */
  readonly instrument_id: string | null;
  readonly instrument_kind: 'bond' | 'fund_share' | 'ksu';
  readonly secid: string;
  readonly short_name: string;
  readonly side: Side;
  readonly quantity: number;
  readonly price: Decimal;
  readonly face_value: Decimal | null;
  readonly accrued: Decimal | null;
  readonly amount: Decimal;
  readonly fee: Decimal;
  readonly trade_at: string;
  readonly trade_date: string;
  readonly settle_date: string;
  readonly venue: 'exchange' | 'otc';
  readonly counterparty: string | null;
  readonly external_id: string | null;
  readonly kind: TradeKind;
  /** РЕПО: % годовых, дата и сумма второй части по отчёту брокера. */
  readonly repo_rate: Decimal | null;
  readonly second_date: string | null;
  readonly second_amount: Decimal | null;
  readonly comment: string | null;
  readonly author: string;
  readonly created_at: string;
  readonly versions: readonly TradeVersion[];
  readonly warnings: readonly string[];
}

export interface TradeInput {
  readonly kind: TradeKind;
  readonly portfolio_id: string;
  readonly instrument_id: string | null;
  readonly side: Side;
  readonly quantity: number;
  readonly price: string;
  readonly trade_at: string;
  readonly settle_date: string;
  readonly face_value: string | null;
  readonly accrued: string | null;
  readonly fee: string;
  readonly venue: 'exchange' | 'otc';
  readonly counterparty: string | null;
  readonly external_id: string | null;
  readonly comment: string | null;
  /** РЕПО с КСУ: сумма первой части. */
  readonly amount: string | null;
  readonly repo_rate: string | null;
  readonly second_date: string | null;
  readonly second_amount: string | null;
}

export interface Lot {
  readonly portfolio_code: string;
  readonly trade_id: string;
  readonly opened_at: string;
  readonly quantity: number;
  readonly price: Decimal;
  readonly face: Decimal | null;
  readonly cost: Decimal;
  readonly accrued_paid: Decimal;
}

export interface Line {
  readonly instrument_id: string;
  readonly kind: 'bond' | 'fund_share';
  readonly secid: string;
  readonly short_name: string;
  readonly trade_quantity: number;
  readonly settle_quantity: number;
  readonly cost: Decimal;
  readonly accrued_paid: Decimal;
  readonly average_price: Decimal | null;
  readonly lots: readonly Lot[];
  /** Цена для оценки на конец дня: облигация — %, пай — ₽; null — нет цены или расчёта. */
  readonly price: Decimal | null;
  /** Стоимость по чистой цене. */
  readonly clean: Decimal;
  /** НКД в оценке — на дату расчётов T+1 (решение 102). */
  readonly accrued_value: Decimal;
  /** Купоны к получению: дата фиксации прошла, выплаты ещё не было. */
  readonly coupon_due: Decimal;
  readonly unrealized: Decimal;
  readonly revaluation: Decimal;
  readonly realized: Decimal;
  readonly coupon: Decimal;
  readonly fees: Decimal;
  /** Результат дня по бумаге. */
  readonly result: Decimal;
  readonly flags: readonly ValuationFlag[];
  /** ₽ за 1 бп на всю позицию. */
  readonly dv01: Decimal | null;
  readonly duration: Decimal | null;
  /** yield — по доходности, spread — по спреду (флоатер, линкер). */
  readonly metrics_basis: 'yield' | 'spread' | null;
}

/** Почему оценка неполная или неточная (design/06). */
export type ValuationFlag = 'no_price' | 'stale_price' | 'no_accrued' | 'no_face' | 'coupon_unknown';

/** Стоимость портфеля на конец дня и результат дня по частям. */
export interface DayResult {
  readonly value: Decimal;
  /** Приток: паи, переводы из других портфелей фонда. */
  readonly flows: Decimal;
  readonly revaluation: Decimal;
  readonly realized: Decimal;
  readonly coupon: Decimal;
  readonly fees: Decimal;
  readonly expenses: Decimal;
  /** Начисленные вознаграждение и комиссии от СЧА. */
  readonly charges: Decimal;
  /** Корректировка комиссий от СЧА при выплате: факт минус начисленное за период. */
  readonly correction: Decimal;
  /** Проценты по РЕПО: доход по обратному, расход по прямому. */
  readonly repo: Decimal;
  readonly total: Decimal;
  readonly receivable: Decimal;
  readonly payable: Decimal;
  readonly flags: readonly ValuationFlag[];
}

export type MovementKind =
  | 'settlement'
  | 'fee'
  | 'coupon'
  | 'amortization'
  | 'redemption'
  | 'payment_unknown'
  | 'unit_issue'
  | 'unit_redemption'
  | 'unit_settlement'
  | 'fee_payment'
  | 'expense_accrual'
  | 'expense_payment'
  | 'transfer_out'
  | 'transfer_in'
  | 'repo_first'
  | 'repo_second'
  | 'repo_contribution';

export interface Movement {
  readonly kind: MovementKind;
  /** У денежных операций бумаги нет. */
  readonly instrument_id: string | null;
  readonly trade_id: string | null;
  readonly operation_id: string | null;
  readonly quantity: number;
  readonly cash: Decimal;
}

/** Паи, требования и обязательства — у казначейского портфеля фонда. */
export interface FundFigures {
  readonly units: Decimal;
  /** К пайщикам: паи выданы, деньги не пришли. */
  readonly claims: Decimal;
  /** Перед пайщиками: паи погашены, деньги не ушли. */
  readonly obligations: Decimal;
  /** Начисленные и не выплаченные расходы. */
  readonly expenses: Decimal;
}

export interface PortfolioState extends FundFigures {
  readonly id: string;
  readonly code: string;
  readonly name: string;
  /** День расчёта; null — расчёта ещё нет. */
  readonly as_of: string | null;
  /** Стоит в очереди пересчёта с этой даты. */
  readonly waiting_since: string | null;
  readonly cash: Decimal;
  readonly movements: readonly Movement[];
  readonly result: DayResult | null;
}

export interface Positions extends FundFigures {
  readonly portfolio_id: string;
  readonly code: string;
  readonly name: string;
  readonly date: string;
  readonly cash: Decimal;
  readonly portfolios: readonly PortfolioState[];
  readonly lines: readonly Line[];
  /** Сумма по портфелям, посчитанным на этот день; null — расчёта на день нет. */
  readonly result: DayResult | null;
  /** DV01 портфеля, ₽ за 1 бп: по доходности — фиксированный купон; по спреду — все облигации. */
  readonly dv01_yield: Decimal;
  readonly dv01_spread: Decimal;
  /** Открытые сделки РЕПО на дату. */
  readonly repos: readonly RepoLine[];
}

/** Открытая сделка РЕПО: долг и начисленные проценты (design/07). */
export interface RepoLine {
  readonly id: string;
  readonly portfolio_id: string;
  readonly portfolio_code: string;
  /** Прямое: фонд привлёк деньги под бумаги. */
  readonly direct: boolean;
  /** null — КСУ. */
  readonly instrument_id: string | null;
  readonly secid: string;
  readonly short_name: string;
  readonly quantity: number;
  readonly rate: Decimal;
  readonly first_date: string;
  readonly second_date: string;
  readonly second_amount: Decimal;
  readonly principal: Decimal;
  readonly accrued: Decimal;
  /** В стоимости портфеля: требование +, обязательство −. */
  readonly value: Decimal;
}

export interface ResultDay {
  readonly date: string;
  readonly value: Decimal;
  readonly flows: Decimal;
  readonly revaluation: Decimal;
  readonly realized: Decimal;
  readonly coupon: Decimal;
  readonly fees: Decimal;
  readonly expenses: Decimal;
  readonly charges: Decimal;
  readonly correction: Decimal;
  readonly repo: Decimal;
  readonly total: Decimal;
  readonly flags: readonly ValuationFlag[];
}

export interface ResultLine {
  readonly instrument_id: string;
  readonly secid: string;
  readonly short_name: string;
  readonly revaluation: Decimal;
  readonly realized: Decimal;
  readonly coupon: Decimal;
  readonly fees: Decimal;
  readonly total: Decimal;
}

/** Финансовый результат за период: по дням и по бумагам (design/06). */
export interface Results {
  readonly portfolio_id: string;
  readonly code: string;
  readonly name: string;
  readonly first: string;
  readonly last: string;
  readonly portfolios: readonly string[];
  readonly waiting_since: string | null;
  readonly days: readonly ResultDay[];
  readonly lines: readonly ResultLine[];
}

export type OperationKind =
  | 'unit_issue'
  | 'unit_redemption'
  | 'expense_accrual'
  | 'expense_payment'
  | 'cash_transfer'
  | 'security_transfer'
  | 'fee_payment'
  | 'repo_contribution';

/** Виды комиссий от СЧА (решение 21). */
export type FeeKind = 'management' | 'specdep' | 'registrar' | 'other';

export interface FeeRate {
  readonly kind: FeeKind;
  /** % годовых. */
  readonly rate: Decimal;
  readonly valid_from: string;
  readonly portfolio_id: string;
  readonly comment: string | null;
  readonly author: string;
  readonly created_at: string;
}

export interface FeeRateInput {
  readonly kind: FeeKind;
  readonly rate: string;
  readonly valid_from: string;
  readonly comment: string | null;
}

/** СЧА фонда на конец дня и её состав (design/06). */
export interface NavDay {
  readonly date: string;
  readonly nav: Decimal;
  readonly units: Decimal;
  readonly unit_price: Decimal | null;
  readonly cash: Decimal;
  readonly clean: Decimal;
  readonly accrued: Decimal;
  readonly receivable: Decimal;
  readonly payable: Decimal;
  readonly claims: Decimal;
  readonly obligations: Decimal;
  readonly expenses: Decimal;
  readonly fees_owed: Decimal;
  readonly fees_today: Readonly<Partial<Record<FeeKind, Decimal>>>;
  /** Размещено в обратное РЕПО и привлечено в прямое — с процентами. */
  readonly repo_placed: Decimal;
  readonly repo_borrowed: Decimal;
  readonly flags: readonly ValuationFlag[];
}

export interface Nav {
  readonly fund_id: string;
  readonly code: string;
  readonly name: string;
  readonly first: string;
  readonly last: string;
  readonly waiting_since: string | null;
  readonly days: readonly NavDay[];
}

/** Виды расходов — короткий список (вопрос 94). */
export type ExpenseKind = 'specdep' | 'registrar' | 'auditor' | 'exchange_broker' | 'other';

export interface OperationVersion extends Omit<TradeVersion, 'status'> {
  readonly status: 'active' | 'cancelled';
}

export interface Operation {
  readonly id: string;
  readonly number: number;
  readonly status: 'active' | 'cancelled';
  readonly kind: OperationKind;
  readonly fund_id: string;
  readonly portfolio_id: string;
  readonly portfolio_code: string | null;
  readonly receiver_id: string | null;
  readonly receiver_code: string | null;
  readonly op_date: string;
  readonly amount: Decimal | null;
  readonly units: Decimal | null;
  readonly planned_date: string | null;
  /** Паи: расчёт отмечен этой датой (решение 42). */
  readonly settled_date: string | null;
  readonly expense_kind: ExpenseKind | null;
  readonly fee_kind: FeeKind | null;
  /** Выплата вознаграждения: последний день оплаченного месяца. */
  readonly period_end: string | null;
  /** Компенсационный взнос: сделка РЕПО; in — фонду, out — от фонда. */
  readonly trade_id: string | null;
  readonly direction: 'in' | 'out' | null;
  readonly instrument_id: string | null;
  readonly secid: string | null;
  readonly short_name: string | null;
  readonly quantity: number | null;
  readonly comment: string | null;
  readonly author: string;
  readonly created_at: string;
  readonly versions: readonly OperationVersion[];
  readonly warnings: readonly string[];
}

interface OperationBase {
  readonly op_date: string;
  readonly comment: string | null;
}

export type OperationInput =
  | (OperationBase & {
      readonly kind: 'unit_issue' | 'unit_redemption';
      readonly fund_id: string;
      readonly units: string;
      readonly amount: string;
      readonly planned_date: string;
    })
  | (OperationBase & {
      readonly kind: 'expense_accrual' | 'expense_payment';
      readonly fund_id: string;
      readonly expense_kind: ExpenseKind;
      readonly amount: string;
    })
  | (OperationBase & {
      readonly kind: 'cash_transfer';
      readonly portfolio_id: string;
      readonly receiver_id: string;
      readonly amount: string;
    })
  | (OperationBase & {
      readonly kind: 'fee_payment';
      readonly fund_id: string;
      readonly fee_kind: FeeKind;
      readonly period_end: string;
      readonly amount: string;
    })
  | (OperationBase & {
      readonly kind: 'security_transfer';
      readonly portfolio_id: string;
      readonly receiver_id: string;
      readonly instrument_id: string;
      readonly quantity: number;
    })
  | (OperationBase & {
      readonly kind: 'repo_contribution';
      readonly trade_id: string;
      readonly direction: 'in' | 'out';
      readonly amount: string | null;
      readonly quantity: number | null;
    });

export interface LadderMovement extends Movement {
  readonly portfolio_code: string;
}

export interface LadderDay {
  readonly date: string;
  readonly trading: boolean;
  readonly movements: readonly LadderMovement[];
  /** Меньше нуля — дефицит денег. */
  readonly cash: Decimal;
  /** На счёте депо на конец дня; меньше нуля — дефицит бумаг. */
  readonly securities: readonly { readonly instrument_id: string; readonly quantity: number }[];
}

export interface Ladder {
  readonly account_id: string;
  readonly portfolios: readonly { readonly id: string; readonly code: string; readonly name: string }[];
  /** Последний расчёт, от которого идёт прогноз. */
  readonly as_of: string | null;
  readonly waiting_since: string | null;
  readonly instruments: readonly { readonly instrument_id: string; readonly secid: string; readonly short_name: string }[];
  readonly days: readonly LadderDay[];
}

export type ImportResult = 'created' | 'amended' | 'cancelled' | 'unchanged';

/** Ответ на пакет сделок (docs/api/trade-import). */
export interface ImportReport {
  readonly created: number;
  readonly amended: number;
  readonly cancelled: number;
  readonly unchanged: number;
  readonly trades: readonly {
    readonly index: number;
    readonly external_id: string;
    readonly result: ImportResult;
    readonly trade_id: string;
    readonly warnings: readonly string[];
  }[];
  readonly rejected: readonly {
    readonly index: number;
    readonly external_id: string;
    readonly error: string;
    readonly message: string;
  }[];
}

const BASE = '/api/booking';

/** Вызовы API сделок; портфели — из сервиса портфелей, бумаги — из справочника. */
export function bookingApi(client: ApiClient) {
  return {
    portfolios: () => client.get<Portfolio[]>('/api/portfolio/portfolios'),
    instruments: () => client.get<Instrument[]>('/api/refdata/instruments'),
    accounts: () => client.get<Account[]>('/api/portfolio/accounts'),
    defaults: (instrumentId: string, tradeDate: string) =>
      client.get<Defaults>(
        `${BASE}/trade-defaults${query({ instrument_id: instrumentId, trade_date: tradeDate })}`,
      ),
    trades: (portfolioId: string, date: string) =>
      client.get<Trade[]>(`${BASE}/trades${query({ portfolio_id: portfolioId, date })}`),
    trade: (id: string) => client.get<Trade>(`${BASE}/trades/${id}`),
    enter: (body: TradeInput) => client.post<Trade>(`${BASE}/trades`, body),
    amend: (id: string, body: TradeInput) => client.post<Trade>(`${BASE}/trades/${id}/versions`, body),
    cancel: (id: string, comment: string) =>
      client.post<Trade>(`${BASE}/trades/${id}/cancel`, { comment }),
    confirm: (id: string, comment: string) =>
      client.post<Trade>(`${BASE}/trades/${id}/confirm`, { comment: comment || null }),
    positions: (portfolioId: string, date: string) =>
      client.get<Positions>(`${BASE}/positions${query({ portfolio_id: portfolioId, date })}`),
    results: (portfolioId: string, first: string, last: string) =>
      client.get<Results>(`${BASE}/results${query({ portfolio_id: portfolioId, from: first, till: last })}`),
    nav: (fundId: string, first: string, last: string) =>
      client.get<Nav>(`${BASE}/nav${query({ fund_id: fundId, from: first, till: last })}`),
    feeRates: (fundId: string) => client.get<FeeRate[]>(`${BASE}/funds/${fundId}/fee-rates`),
    setFeeRate: (fundId: string, body: FeeRateInput) =>
      client.post<FeeRate[]>(`${BASE}/funds/${fundId}/fee-rates`, body),
    operations: (portfolioId: string) =>
      client.get<Operation[]>(`${BASE}/operations${query({ portfolio_id: portfolioId })}`),
    enterOperation: (body: OperationInput) => client.post<Operation>(`${BASE}/operations`, body),
    settleOperation: (id: string, settledDate: string) =>
      client.post<Operation>(`${BASE}/operations/${id}/settle`, { settled_date: settledDate }),
    cancelOperation: (id: string, comment: string) =>
      client.post<Operation>(`${BASE}/operations/${id}/cancel`, { comment }),
    /** Пакет — как в файле: форму проверяет сервис по спецификации. */
    importTrades: (batch: unknown) => client.post<ImportReport>(`${BASE}/feed/v1/trades`, batch),
    ladder: (accountId: string) => client.get<Ladder>(`${BASE}/ladder${query({ account_id: accountId })}`),
  };
}

export type BookingApi = ReturnType<typeof bookingApi>;

function query(params: Record<string, string>): string {
  return `?${new URLSearchParams(params).toString()}`;
}
