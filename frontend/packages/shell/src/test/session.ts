import type { Role, Session } from '@am/platform';
import { vi } from 'vitest';

export function fakeSession(...roles: Role[]): Session {
  return {
    username: 'ivanov',
    displayName: 'Иван Иванов',
    roles: new Set(roles),
    getAccessToken: () => Promise.resolve('token'),
    logout: vi.fn(),
  };
}
