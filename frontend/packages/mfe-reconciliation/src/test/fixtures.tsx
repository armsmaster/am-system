import type { Role, Session } from '@am/platform';
import { render } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { vi } from 'vitest';

import { Reconciliation } from '../App';
import type {
  Account,
  AccountDay,
  BalanceLine,
  Closing,
  FundDay,
  MovementLine,
  Portfolio,
  ReconciliationApi,
  Received,
  Statement,
  TradeLine,
} from '../api';

export function session(...roles: Role[]): Session {
  return {
    username: 'user',
    displayName: 'Пользователь',
    roles: new Set(roles),
    getAccessToken: () => Promise.resolve('t'),
    logout: () => undefined,
  };
}

/** Фонд «ОПИФ 1» и его счета (design/08, сквозной пример). */
export const PORTFOLIOS: Portfolio[] = [
  { id: 'p-fund', code: 'OPIF1', name: 'ОПИФ 1', is_fund: true },
  { id: 'p-bonds', code: 'OPIF1-BONDS', name: 'Облигации', is_fund: false },
];

export const ACCOUNTS: Account[] = [
  { id: 'a-broker', fund_id: 'p-fund', code: 'OPIF1-BROKER', name: 'Брокерский', cash_kind: 'broker', institution: 'Брокер Х' },
  { id: 'a-bank', fund_id: 'p-fund', code: 'OPIF1-BANK', name: 'Банковский', cash_kind: 'bank', institution: 'Банк Y' },
];

export function statement(fields: Partial<Statement> = {}): Statement {
  return {
    id: 's-1',
    batch_id: 'b-1',
    source: 'broker-x',
    date: '2026-12-02',
    opening_cash: '46026949.95',
    closing_cash: '46026949.95',
    trades: [],
    movements: [],
    received_by: 'service-account-am-statement-feed',
    received_at: '2026-12-02T20:15:00Z',
    ...fields,
  };
}

export function balance(fields: Partial<BalanceLine> = {}): BalanceLine {
  return {
    line: 'balance:opening',
    status: 'matched',
    system: '46026949.95',
    statement: '46026949.95',
    discrepancy: '0.00',
    expected: '0',
    acceptance: null,
    ...fields,
  };
}

/** Купон 02.12: в системе есть, брокер зачислит его 03.12. */
export function coupon(fields: Partial<MovementLine> = {}): MovementLine {
  return {
    line: 'movement:coupon::i-ofz',
    status: 'not_in_statement',
    kind: 'coupon',
    trade: null,
    secid: 'SU26238RMFS4',
    system: '885000.00',
    statement: null,
    discrepancy: '-885000.00',
    comments: [],
    paired: null,
    acceptance: null,
    ...fields,
  };
}

/** Покупка 27.11: в системе нет комиссии, у брокера — 2 800,05 ₽. */
export function purchase(fields: Partial<TradeLine> = {}): TradeLine {
  return {
    line: 'trade:1000000001',
    status: 'mismatched',
    system: {
      trade_id: 't-buy',
      status: 'entered',
      external_id: '1000000001',
      kind: 'outright',
      side: 'buy',
      secid: 'SU26238RMFS4',
      quantity: 50000,
      amount: '28000500.00',
      fee: '0.00',
    },
    statement: {
      external_id: '1000000001',
      kind: 'outright',
      side: 'buy',
      code: 'SU26238RMFS4',
      quantity: 50000,
      amount: '28000500.00',
      fee: '2800.05',
    },
    differences: [{ field: 'fee', system: '0', statement: '2800.05' }],
    acceptance: null,
    ...fields,
  };
}

export function broker(fields: Partial<AccountDay> = {}): AccountDay {
  return {
    account_id: 'a-broker',
    code: 'OPIF1-BROKER',
    name: 'Брокерский',
    cash_kind: 'broker',
    institution: 'Брокер Х',
    portfolios: ['OPIF1-BONDS'],
    status: 'discrepancies',
    waiting_since: null,
    trading: true,
    calculated: true,
    statement: statement(),
    previous: { date: '2026-12-01', closing_cash: '46026949.95' },
    trades: [],
    movements: [coupon()],
    opening: balance(),
    closing: balance({
      line: 'balance:closing',
      status: 'mismatched',
      system: '46911949.95',
      discrepancy: '-885000.00',
    }),
    waiting: [],
    ...fields,
  };
}

export function bank(fields: Partial<AccountDay> = {}): AccountDay {
  return broker({
    account_id: 'a-bank',
    code: 'OPIF1-BANK',
    name: 'Банковский',
    cash_kind: 'bank',
    institution: 'Банк Y',
    portfolios: ['OPIF1-TREASURY'],
    status: 'no_statement',
    statement: null,
    previous: { date: '2026-12-01', closing_cash: '40000000.00' },
    movements: [],
    opening: null,
    closing: null,
    ...fields,
  });
}

export function fundDay(fields: Partial<FundDay> = {}): FundDay {
  return {
    fund_id: 'p-fund',
    code: 'OPIF1',
    name: 'ОПИФ 1',
    date: '2026-12-02',
    trading: true,
    reconciled: false,
    closed: false,
    closed_through: '2026-12-01',
    next_day: '2026-12-02',
    history: [],
    accounts: [bank(), broker()],
    ...fields,
  };
}

/** Неделя закрыта по 01.12; 30.11 закрыли вместе с выходными. */
export function closing(fields: Partial<Closing> = {}): Closing {
  return {
    fund_id: 'p-fund',
    closed_through: '2026-12-01',
    next_day: '2026-12-02',
    history: [
      { action: 'close', first_day: '2026-11-28', last_day: '2026-11-30', closed_through: '2026-11-30', author: 'ops', comment: null, created_at: '2026-12-01T06:10:00Z' },
      { action: 'reopen', first_day: '2026-11-30', last_day: '2026-11-30', closed_through: '2026-11-29', author: 'admin', comment: 'Брокер исправил отчёт', created_at: '2026-12-01T07:00:00Z' },
      { action: 'close', first_day: '2026-11-30', last_day: '2026-11-30', closed_through: '2026-11-30', author: 'ops', comment: null, created_at: '2026-12-01T08:00:00Z' },
    ],
    ...fields,
  };
}

export function fakeApi() {
  return {
    portfolios: vi.fn(() => Promise.resolve(PORTFOLIOS)),
    accounts: vi.fn(() => Promise.resolve(ACCOUNTS)),
    day: vi.fn((..._args: unknown[]) => Promise.resolve(fundDay())),
    statements: vi.fn((..._args: unknown[]) => Promise.resolve([statement()])),
    batch: vi.fn((..._args: unknown[]) =>
      Promise.resolve({
        id: 'b-1',
        source: 'broker-x',
        received_by: 'feed',
        received_at: '2026-12-02T20:15:00Z',
        payload: { source: 'broker-x', statements: [{ account: 'OPIF1-BROKER' }] },
      }),
    ),
    enter: vi.fn((..._args: unknown[]): Promise<Received> => Promise.resolve({ statement_id: 's-2', result: 'created' })),
    accept: vi.fn((..._args: unknown[]) => Promise.resolve(broker())),
    confirm: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    close: vi.fn((..._args: unknown[]) => Promise.resolve(fundDay({ closed: true, closed_through: '2026-12-02' }))),
    closing: vi.fn((..._args: unknown[]) => Promise.resolve(closing())),
    reopen: vi.fn((..._args: unknown[]) => Promise.resolve(closing())),
  };
}

export type FakeApi = ReturnType<typeof fakeApi>;

export function renderReconciliation(api: FakeApi, path: string, isOperations = true, isAdmin = false): FakeApi {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/reconciliation/*"
          element={
            <Reconciliation
              api={api as unknown as ReconciliationApi}
              basePath="/reconciliation"
              isOperations={isOperations}
              isAdmin={isAdmin}
            />
          }
        />
      </Routes>
    </MemoryRouter>,
  );
  return api;
}
