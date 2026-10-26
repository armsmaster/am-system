import { ApiError, type ApiClient } from '@am/platform';
import { describe, expect, it, vi } from 'vitest';

import { candidatesOf, refdataApi } from './api';

/** Каждый вызов — с методом и путём, которые ждёт сервис справочника. */
describe('refdataApi', () => {
  const client = {
    get: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    post: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    put: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    patch: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    delete: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
  };
  const api = refdataApi(client as unknown as ApiClient);
  const edit = { valid_from: '2026-10-26', comment: 'Маржа из проспекта', changes: {}, unpin: [] };

  it.each([
    ['lookup', () => api.lookup('SU26238RMFS4'), 'get', ['/api/refdata/lookup?code=SU26238RMFS4']],
    ['load', () => api.load('SU26238RMFS4'), 'post', ['/api/refdata/instruments', { code: 'SU26238RMFS4' }]],
    ['search all', () => api.search(''), 'get', ['/api/refdata/instruments']],
    ['search', () => api.search('ОФЗ 26'), 'get', ['/api/refdata/instruments?query=%D0%9E%D0%A4%D0%97+26']],
    ['instrument', () => api.instrument('i-1'), 'get', ['/api/refdata/instruments/i-1']],
    ['version', () => api.version('i-1', 2), 'get', ['/api/refdata/instruments/i-1/versions/2']],
    ['edit', () => api.edit('i-1', edit), 'post', ['/api/refdata/instruments/i-1/versions', edit]],
    ['sync', () => api.sync('i-1'), 'post', ['/api/refdata/instruments/i-1/sync', {}]],
    ['calendar', () => api.calendar(2026), 'get', ['/api/refdata/calendar/2026']],
    [
      'setDay',
      () => api.setDay('2026-11-04', { is_trading: false, comment: null }),
      'put',
      ['/api/refdata/calendar/days/2026-11-04', { is_trading: false, comment: null }],
    ],
    ['clearDay', () => api.clearDay('2026-11-04'), 'delete', ['/api/refdata/calendar/days/2026-11-04']],
    [
      'addTradingDays',
      () => api.addTradingDays('2026-10-30', 1),
      'get',
      ['/api/refdata/calendar/add-trading-days?date=2026-10-30&days=1'],
    ],
  ] as const)('%s', async (_name, call, method, args) => {
    await call();

    expect(client[method]).toHaveBeenLastCalledWith(...args);
  });
});

describe('candidatesOf', () => {
  const candidate = {
    secid: 'SU26238RMFS4',
    isin: 'RU000A1038V6',
    short_name: 'ОФЗ 26238',
    name: 'ОФЗ-ПД 26238',
    type_name: 'Государственная облигация',
  };

  it('reads similar securities from a "not in ISS" answer', () => {
    const error = new ApiError(404, { detail: { code: 'not_in_iss', candidates: [candidate] } });

    expect(candidatesOf(error)).toEqual([candidate]);
  });

  it.each([
    ['a network failure', new Error('offline')],
    ['another error', new ApiError(404, { detail: { code: 'not_found' } })],
    ['an empty body', new ApiError(500, null)],
    ['no candidates', new ApiError(404, { detail: { code: 'not_in_iss' } })],
  ])('is empty for %s', (_name, error) => {
    expect(candidatesOf(error)).toEqual([]);
  });
});
