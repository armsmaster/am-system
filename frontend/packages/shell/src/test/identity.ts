import { vi } from 'vitest';

import type { IdentityApi, SessionData } from '../auth/identity';

export const SESSION: SessionData = {
  access_token: 'token-1',
  expires_in: 300,
  user: { id: 'u-1', username: 'ivanov', display_name: 'Иван Иванов', roles: ['trading', 'unknown'] },
};

export const INVITE = { username: 'ivan.petrov', full_name: 'Иван Петров', expires_at: '2026-11-28T11:05:00Z' };

/** Сервис входа без сети: по умолчанию всё удаётся. */
export function fakeApi(overrides: Partial<IdentityApi> = {}): IdentityApi {
  return {
    login: vi.fn(() => Promise.resolve(SESSION)),
    firstLogin: vi.fn(() => Promise.resolve(SESSION)),
    refresh: vi.fn(() => Promise.resolve(SESSION)),
    logout: vi.fn(() => Promise.resolve()),
    invite: vi.fn(() => Promise.resolve(INVITE)),
    acceptInvite: vi.fn(() => Promise.resolve(SESSION)),
    changePassword: vi.fn(() => Promise.resolve()),
    ...overrides,
  };
}
