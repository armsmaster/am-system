import { ApiError, type Role, type Session } from '@am/platform';
import { vi } from 'vitest';

import type { Account, Portfolio, PortfolioApi, User } from '../api';

export function session(...roles: Role[]): Session {
  return {
    username: 'admin',
    displayName: 'Админ',
    roles: new Set(roles),
    getAccessToken: () => Promise.resolve('t'),
    logout: () => undefined,
  };
}

function portfolio(code: string, fields: Partial<Portfolio> = {}): Portfolio {
  return {
    id: `id-${code}`,
    code,
    name: `Портфель ${code}`,
    kind: 'compound',
    is_fund: false,
    parent_ids: [],
    fund_id: null,
    account_id: null,
    treasury_id: null,
    ...fields,
  };
}

/**
 *     F1 (фонд, казначейство F1-TREASURY)      F2 (фонд)
 *     ├── F1-BONDS                              └── F2-MAIN (A2)
 *     │   └── F1-OFZ (A1)
 *     └── F1-TREASURY (A1)
 *     BOOK ── F1-OFZ, F2-MAIN
 */
export const PORTFOLIOS: readonly Portfolio[] = [
  portfolio('BOOK'),
  portfolio('F1', { is_fund: true, fund_id: 'id-F1', treasury_id: 'id-F1-TREASURY' }),
  portfolio('F1-BONDS', { parent_ids: ['id-F1'], fund_id: 'id-F1' }),
  portfolio('F1-OFZ', {
    kind: 'physical',
    parent_ids: ['id-F1-BONDS', 'id-BOOK'],
    fund_id: 'id-F1',
    account_id: 'id-A1',
  }),
  portfolio('F1-TREASURY', {
    kind: 'physical',
    parent_ids: ['id-F1'],
    fund_id: 'id-F1',
    account_id: 'id-A1',
  }),
  portfolio('F2', { is_fund: true, fund_id: 'id-F2' }),
  portfolio('F2-MAIN', {
    kind: 'physical',
    parent_ids: ['id-F2', 'id-BOOK'],
    fund_id: 'id-F2',
    account_id: 'id-A2',
  }),
];

function account(code: string, fundId: string, fields: Partial<Account> = {}): Account {
  return {
    id: `id-${code}`,
    fund_id: fundId,
    code,
    name: `Счёт ${code}`,
    cash_kind: 'broker',
    institution: 'Брокер',
    cash_account: '30601',
    depo_account: 'D-1',
    stock_market: true,
    derivatives_market: false,
    ...fields,
  };
}

export const ACCOUNTS: readonly Account[] = [
  account('A1', 'id-F1'),
  account('A1-BANK', 'id-F1', {
    cash_kind: 'bank',
    institution: 'Банк',
    depo_account: null,
    stock_market: false,
  }),
  account('A2', 'id-F2', { derivatives_market: true }),
];

export const USERS: readonly User[] = [
  {
    subject: 'u-admin',
    username: 'admin',
    full_name: 'Админ',
    enabled: true,
    roles: ['admin'],
    grant_ids: [],
  },
  {
    subject: 'u-trader',
    username: 'trader',
    full_name: 'Тимур Трейдеров',
    enabled: true,
    roles: ['trading'],
    grant_ids: ['id-F1-BONDS'],
  },
  {
    subject: 'u-ops',
    username: 'ops',
    full_name: 'ops',
    enabled: true,
    roles: [],
    grant_ids: [],
  },
];

/** Подменный API: читает фиксированные данные, изменения только запоминает. */
export function fakeApi(
  data: {
    portfolios?: readonly Portfolio[];
    accounts?: readonly Account[];
    users?: readonly User[];
  } = {},
) {
  const ok = () => vi.fn((..._args: unknown[]) => Promise.resolve(null));
  return {
    portfolios: vi.fn(() => Promise.resolve([...(data.portfolios ?? PORTFOLIOS)])),
    accounts: vi.fn(() => Promise.resolve([...(data.accounts ?? ACCOUNTS)])),
    users: vi.fn(() => Promise.resolve([...(data.users ?? USERS)])),
    createPortfolio: ok(),
    updatePortfolio: ok(),
    link: ok(),
    unlink: ok(),
    setAccount: ok(),
    setTreasury: ok(),
    createAccount: ok(),
    updateAccount: ok(),
    grant: ok(),
    revoke: ok(),
  } satisfies Record<keyof PortfolioApi, unknown>;
}

export type FakeApi = ReturnType<typeof fakeApi>;

export function asApi(fake: FakeApi): PortfolioApi {
  return fake as unknown as PortfolioApi;
}

export function ruleViolation(message: string): ApiError {
  return new ApiError(422, { detail: { code: 'rule', message } });
}
