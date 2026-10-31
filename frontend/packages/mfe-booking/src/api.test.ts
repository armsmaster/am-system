import type { ApiClient } from '@am/platform';
import { describe, expect, it, vi } from 'vitest';

import { bookingApi, type TradeInput } from './api';

/** Каждый вызов — с методом и путём, которые ждут сервисы. */
describe('bookingApi', () => {
  const client = {
    get: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    post: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    put: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    patch: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    delete: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
  };
  const api = bookingApi(client as unknown as ApiClient);
  const body = { portfolio_id: 'p' } as unknown as TradeInput;

  it.each([
    ['portfolios', () => api.portfolios(), 'get', ['/api/portfolio/portfolios']],
    ['instruments', () => api.instruments(), 'get', ['/api/refdata/instruments']],
    [
      'defaults',
      () => api.defaults('i-1', '2026-11-27'),
      'get',
      ['/api/booking/trade-defaults?instrument_id=i-1&trade_date=2026-11-27'],
    ],
    ['trades', () => api.trades('p-1', '2026-11-27'), 'get', ['/api/booking/trades?portfolio_id=p-1&date=2026-11-27']],
    ['trade', () => api.trade('t-1'), 'get', ['/api/booking/trades/t-1']],
    ['enter', () => api.enter(body), 'post', ['/api/booking/trades', body]],
    ['amend', () => api.amend('t-1', body), 'post', ['/api/booking/trades/t-1/versions', body]],
    ['cancel', () => api.cancel('t-1', 'ошибка'), 'post', ['/api/booking/trades/t-1/cancel', { comment: 'ошибка' }]],
    ['confirm', () => api.confirm('t-1', ''), 'post', ['/api/booking/trades/t-1/confirm', { comment: null }]],
    ['confirm with a note', () => api.confirm('t-1', 'сверено'), 'post', ['/api/booking/trades/t-1/confirm', { comment: 'сверено' }]],
    [
      'positions',
      () => api.positions('p-1', '2026-12-02'),
      'get',
      ['/api/booking/positions?portfolio_id=p-1&date=2026-12-02'],
    ],
  ] as const)('%s', async (_name, call, method, args) => {
    await call();
    expect(client[method]).toHaveBeenLastCalledWith(...args);
  });
});
