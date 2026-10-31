import type { ApiClient } from '@am/platform';
import { describe, expect, it, vi } from 'vitest';

import { marketdataApi } from './api';

/** Каждый вызов — с методом и путём, которые ждёт сервис рыночных данных. */
describe('marketdataApi', () => {
  const client = {
    get: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    post: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    put: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    patch: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    delete: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
  };
  const api = marketdataApi(client as unknown as ApiClient);
  const rule = {
    price_types: ['vwap' as const],
    dema_timeframe: null,
    dema_half_life: null,
    spread_threshold_bp: 50,
    comment: 'проверка',
  };

  it.each([
    ['overview now', () => api.overview(), 'get', ['/api/marketdata/overview']],
    [
      'overview at a moment',
      () => api.overview('2026-10-27T11:05:00+03:00'),
      'get',
      ['/api/marketdata/overview?at=2026-10-27T11%3A05%3A00%2B03%3A00'],
    ],
    ['price', () => api.price('i-1'), 'get', ['/api/marketdata/instruments/i-1/price']],
    ['series', () => api.series('i-1'), 'get', ['/api/marketdata/instruments/i-1/series']],
    [
      'series for a period',
      () => api.series('i-1', '2026-10-26T00:00:00Z', '2026-10-27T00:00:00Z'),
      'get',
      [
        '/api/marketdata/instruments/i-1/series?from=2026-10-26T00%3A00%3A00Z&to=2026-10-27T00%3A00%3A00Z',
      ],
    ],
    ['rules', () => api.rules('i-1'), 'get', ['/api/marketdata/instruments/i-1/rules']],
    ['setRule', () => api.setRule('i-1', rule), 'put', ['/api/marketdata/instruments/i-1/rule', rule]],
    [
      'manualPrice',
      () => api.manualPrice('i-1', { value: '99.5', comment: 'брокер' }),
      'post',
      ['/api/marketdata/instruments/i-1/manual-prices', { value: '99.5', comment: 'брокер' }],
    ],
    ['batches', () => api.batches(), 'get', ['/api/marketdata/feed/batches']],
    ['analytics', () => api.analytics('i-1'), 'get', ['/api/marketdata/instruments/i-1/analytics']],
    [
      'analytics what if',
      () => api.analytics('i-1', { yield: '15.5' }),
      'get',
      ['/api/marketdata/instruments/i-1/analytics?yield=15.5'],
    ],
    ['rates', () => api.rates(), 'get', ['/api/marketdata/rates']],
    ['rates on a date', () => api.rates('2026-10-26'), 'get', ['/api/marketdata/rates?date=2026-10-26']],
  ] as const)('%s', async (_name, call, method, args) => {
    await call();

    expect(client[method]).toHaveBeenLastCalledWith(...args);
  });
});
