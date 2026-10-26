import type { ApiClient } from '@am/platform';
import { describe, expect, it, vi } from 'vitest';

import { portfolioApi } from './api';

/** Каждый вызов админки — с методом и путём, которые ждёт сервис портфелей. */
describe('portfolioApi', () => {
  const client = {
    get: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    post: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    put: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    patch: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    delete: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
  };
  const api = portfolioApi(client as unknown as ApiClient);

  it.each([
    ['portfolios', () => api.portfolios(), 'get', ['/api/portfolio/portfolios']],
    ['accounts', () => api.accounts(), 'get', ['/api/portfolio/accounts']],
    ['users', () => api.users(), 'get', ['/api/portfolio/users']],
    [
      'createPortfolio',
      () => api.createPortfolio({ code: 'F1', name: 'Фонд', kind: 'compound', is_fund: true }),
      'post',
      ['/api/portfolio/portfolios', { code: 'F1', name: 'Фонд', kind: 'compound', is_fund: true }],
    ],
    [
      'updatePortfolio',
      () => api.updatePortfolio('p1', { name: 'Новое' }),
      'patch',
      ['/api/portfolio/portfolios/p1', { name: 'Новое' }],
    ],
    ['link', () => api.link('p1', 'p2'), 'put', ['/api/portfolio/portfolios/p1/children/p2']],
    ['unlink', () => api.unlink('p1', 'p2'), 'delete', ['/api/portfolio/portfolios/p1/children/p2']],
    [
      'setAccount',
      () => api.setAccount('p1', 'a1'),
      'put',
      ['/api/portfolio/portfolios/p1/account', { account_id: 'a1' }],
    ],
    [
      'setTreasury',
      () => api.setTreasury('f1', null),
      'put',
      ['/api/portfolio/funds/f1/treasury', { portfolio_id: null }],
    ],
    [
      'createAccount',
      () =>
        api.createAccount({
          fund_id: 'f1',
          code: 'A1',
          name: 'Счёт',
          cash_kind: 'broker',
          institution: 'Брокер',
          cash_account: '1',
          depo_account: null,
          stock_market: true,
          derivatives_market: false,
        }),
      'post',
      ['/api/portfolio/accounts', expect.objectContaining({ fund_id: 'f1', code: 'A1' })],
    ],
    [
      'updateAccount',
      () => api.updateAccount('a1', { name: 'Новое' }),
      'patch',
      ['/api/portfolio/accounts/a1', { name: 'Новое' }],
    ],
    ['grant', () => api.grant('u1', 'p1'), 'put', ['/api/portfolio/users/u1/grants/p1']],
    ['revoke', () => api.revoke('u1', 'p1'), 'delete', ['/api/portfolio/users/u1/grants/p1']],
  ] as const)('%s', async (_name, call, method, args) => {
    client[method].mockClear();

    await call();

    expect(client[method]).toHaveBeenCalledWith(...args);
  });
});
