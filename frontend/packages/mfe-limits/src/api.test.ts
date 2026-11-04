import type { ApiClient } from '@am/platform';
import { describe, expect, it, vi } from 'vitest';

import { limitsApi } from './api';
import { BODY } from './test/fixtures';

/** Каждый вызов — с методом и путём, которые ждут сервис лимитов и справочник. */
describe('limitsApi', () => {
  const client = {
    get: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
    post: vi.fn((..._args: unknown[]) => Promise.resolve(null)),
  };
  const api = limitsApi(client as unknown as ApiClient);
  const save = { valid_from: '2026-11-01', comment: 'Политика', body: BODY };

  it.each([
    ['funds', () => api.funds(), 'get', ['/api/limits/funds']],
    ['day', () => api.day('f-1', '2026-11-10'), 'get', ['/api/limits/funds/f-1/days/2026-11-10']],
    ['sets', () => api.sets('f-1'), 'get', ['/api/limits/funds/f-1/sets']],
    ['saveSet', () => api.saveSet('f-1', save), 'post', ['/api/limits/funds/f-1/sets', save]],
    ['classifiers', () => api.classifiers(), 'get', ['/api/refdata/classifiers']],
    ['issuers', () => api.issuers(), 'get', ['/api/refdata/issuers']],
    ['instruments', () => api.instruments(), 'get', ['/api/refdata/instruments']],
  ] as const)('%s', async (_name, call, method, args) => {
    await call();

    expect(client[method]).toHaveBeenLastCalledWith(...args);
  });
});
