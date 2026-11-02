import type { ApiClient } from '@am/platform';

/** Десятичное число строкой, как его отдаёт сервис: так не теряется точность. */
export type Decimal = string;
export type Side = 'buy' | 'sell';
export type TradeKind = 'outright' | 'repo';

export interface Portfolio {
  readonly id: string;
  readonly code: string;
  readonly name: string;
  readonly is_fund: boolean;
}

export interface Account {
  readonly id: string;
  readonly fund_id: string;
  readonly code: string;
  readonly name: string;
  readonly cash_kind: 'broker' | 'bank';
  readonly institution: string;
}

/** Строка сверки (design/08): сходится, расходится, нет с одной из сторон, принята. */
export type LineStatus =
  | 'matched'
  | 'mismatched'
  | 'not_in_system'
  | 'not_in_statement'
  | 'accepted'
  | 'paired'
  | 'explained';

export type AccountStatus =
  | 'reconciled'
  | 'discrepancies'
  | 'no_statement'
  | 'non_trading'
  | 'recalculating'
  | 'not_calculated';

/** Виды движений выписки (решение 120). */
export type MovementKind =
  | 'settlement'
  | 'fee'
  | 'repo_first'
  | 'repo_second'
  | 'repo_contribution'
  | 'coupon'
  | 'amortization'
  | 'redemption'
  | 'transfer'
  | 'unit_settlement'
  | 'fee_payment'
  | 'expense_payment'
  | 'other';

export interface Acceptance {
  readonly comment: string;
  readonly accepted_by: string;
  readonly accepted_at: string;
}

export interface Difference {
  readonly field: string;
  readonly system: string | null;
  readonly statement: string | null;
}

export interface SystemTrade {
  readonly trade_id: string;
  readonly status: 'entered' | 'confirmed' | 'cancelled';
  readonly external_id: string | null;
  readonly kind: TradeKind;
  readonly side: Side;
  readonly secid: string;
  readonly quantity: number;
  readonly amount: Decimal;
  readonly fee: Decimal;
}

export interface StatementTrade {
  readonly external_id: string;
  readonly kind: TradeKind;
  readonly side: Side;
  readonly code: string;
  readonly quantity: number;
  readonly amount: Decimal;
  readonly fee: Decimal;
}

export interface TradeLine {
  readonly line: string;
  readonly status: LineStatus;
  readonly system: SystemTrade | null;
  readonly statement: StatementTrade | null;
  readonly differences: readonly Difference[];
  readonly acceptance: Acceptance | null;
}

export interface MovementLine {
  readonly line: string;
  readonly status: LineStatus;
  readonly kind: MovementKind;
  readonly trade: string | null;
  readonly secid: string | null;
  readonly system: Decimal | null;
  readonly statement: Decimal | null;
  readonly discrepancy: Decimal;
  readonly comments: readonly string[];
  /** Сошлось с принятой строкой этого дня. */
  readonly paired: string | null;
  readonly acceptance: Acceptance | null;
}

export interface BalanceLine {
  readonly line: string;
  readonly status: LineStatus;
  readonly system: Decimal;
  readonly statement: Decimal;
  readonly discrepancy: Decimal;
  /** Сумма принятых расхождений, которые ждут пары. */
  readonly expected: Decimal;
  readonly acceptance: Acceptance | null;
}

/** Принятое расхождение движения, которое ждёт пары в другой день. */
export interface Waiting {
  readonly date: string;
  readonly line: string;
  readonly kind: MovementKind;
  readonly secid: string | null;
  readonly trade: string | null;
  readonly discrepancy: Decimal;
  readonly comment: string;
}

export interface StatementMovement {
  readonly kind: MovementKind;
  readonly amount: Decimal;
  readonly trade: string | null;
  readonly code: string | null;
  readonly comment: string | null;
}

export interface Statement {
  readonly id: string;
  readonly batch_id: string | null;
  readonly source: string;
  readonly date: string;
  readonly opening_cash: Decimal;
  readonly closing_cash: Decimal;
  readonly trades: readonly StatementTrade[];
  readonly movements: readonly StatementMovement[];
  readonly received_by: string;
  readonly received_at: string;
}

export interface AccountDay {
  readonly account_id: string;
  readonly code: string;
  readonly name: string;
  readonly cash_kind: 'broker' | 'bank';
  readonly institution: string;
  readonly portfolios: readonly string[];
  readonly status: AccountStatus;
  readonly waiting_since: string | null;
  readonly trading: boolean;
  readonly calculated: boolean;
  readonly statement: Statement | null;
  /** Последняя выписка до дня: остаток для «движений нет». */
  readonly previous: { readonly date: string; readonly closing_cash: Decimal } | null;
  readonly trades: readonly TradeLine[];
  readonly movements: readonly MovementLine[];
  readonly opening: BalanceLine | null;
  readonly closing: BalanceLine | null;
  readonly waiting: readonly Waiting[];
}

export interface FundDay {
  readonly fund_id: string;
  readonly code: string;
  readonly name: string;
  readonly date: string;
  readonly trading: boolean;
  /** Все счета фонда сверены — день можно закрыть (решение 48). */
  readonly reconciled: boolean;
  readonly accounts: readonly AccountDay[];
}

export interface MovementInput {
  readonly kind: MovementKind;
  readonly amount: Decimal;
  readonly trade: string | null;
  readonly security: string | null;
  readonly comment: string | null;
}

export interface StatementInput {
  readonly account_id: string;
  readonly date: string;
  readonly opening_cash: Decimal;
  readonly closing_cash: Decimal;
  readonly movements: readonly MovementInput[];
}

export interface Received {
  readonly statement_id: string;
  readonly result: 'created' | 'replaced' | 'unchanged';
}

export interface Batch {
  readonly id: string;
  readonly source: string;
  readonly received_by: string;
  readonly received_at: string;
  readonly payload: unknown;
}

export interface AcceptInput {
  readonly account_id: string;
  readonly date: string;
  readonly line: string;
  readonly comment: string;
}

const BASE = '/api/reconciliation';

function query(params: Record<string, string>): string {
  return `?${new URLSearchParams(params).toString()}`;
}

export function reconciliationApi(client: ApiClient) {
  return {
    portfolios: () => client.get<Portfolio[]>('/api/portfolio/portfolios'),
    accounts: () => client.get<Account[]>('/api/portfolio/accounts'),
    day: (fundId: string, date: string) => client.get<FundDay>(`${BASE}/funds/${fundId}/days/${date}`),
    statements: (accountId: string, date: string) =>
      client.get<Statement[]>(`${BASE}/statements${query({ account_id: accountId, date })}`),
    batch: (id: string) => client.get<Batch>(`${BASE}/batches/${id}`),
    enter: (body: StatementInput) => client.post<Received>(`${BASE}/statements`, body),
    accept: (body: AcceptInput) => client.post<AccountDay>(`${BASE}/acceptances`, body),
    /** Сошедшуюся сделку подтверждает сервис сделок (решение 125). */
    confirm: (tradeId: string, comment: string) =>
      client.post<unknown>(`/api/booking/trades/${tradeId}/confirm`, { comment }),
  };
}

export type ReconciliationApi = ReturnType<typeof reconciliationApi>;
