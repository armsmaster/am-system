import type { ApiClient } from '@am/platform';
import { describe, expect, it, vi } from 'vitest';

import { reconciliationApi, type StatementInput } from './api';

/** Каждый вызов — с методом и путём, которые ждут сервисы. */
describe('reconciliationApi', () => {
  const client = {
    get: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    post: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    put: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    patch: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    delete: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
  };
  const api = reconciliationApi(client as unknown as ApiClient);
  const body = { account_id: 'a-bank' } as unknown as StatementInput;
  const accept = { account_id: 'a-broker', date: '2026-12-02', line: 'balance:closing', comment: 'Проверили' };

  it.each([
    ['portfolios', () => api.portfolios(), 'get', ['/api/portfolio/portfolios']],
    ['accounts', () => api.accounts(), 'get', ['/api/portfolio/accounts']],
    ['day', () => api.day('p-fund', '2026-12-02'), 'get', ['/api/reconciliation/funds/p-fund/days/2026-12-02']],
    [
      'statements',
      () => api.statements('a-bank', '2026-12-02'),
      'get',
      ['/api/reconciliation/statements?account_id=a-bank&date=2026-12-02'],
    ],
    ['batch', () => api.batch('b-1'), 'get', ['/api/reconciliation/batches/b-1']],
    ['enter', () => api.enter(body), 'post', ['/api/reconciliation/statements', body]],
    ['accept', () => api.accept(accept), 'post', ['/api/reconciliation/acceptances', accept]],
    [
      'confirm',
      () => api.confirm('t-1', 'Сошлась с выпиской за 27.11.2026'),
      'post',
      ['/api/booking/trades/t-1/confirm', { comment: 'Сошлась с выпиской за 27.11.2026' }],
    ],
  ] as const)('%s', async (_name, call, method, args) => {
    await call();
    expect(client[method]).toHaveBeenLastCalledWith(...args);
  });
});
