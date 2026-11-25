import { describe, expect, it, vi } from 'vitest';

import { fakeApi, SESSION } from '../test/identity';
import { AuthError } from './identity';
import { createSession } from './session';

function clock(start = 1_000_000) {
  let now = start;
  return { now: () => now, advance: (ms: number) => (now += ms) };
}

function place() {
  return { reload: vi.fn(), assign: vi.fn() };
}

describe('createSession', () => {
  it('takes the user and system roles from the identity service', () => {
    const session = createSession(SESSION, { api: fakeApi(), location: place() });

    expect(session.username).toBe('ivanov');
    expect(session.displayName).toBe('Иван Иванов');
    expect([...session.roles]).toEqual(['trading']);
  });

  it('keeps the token until shortly before it expires', async () => {
    const api = fakeApi();
    const time = clock();
    const session = createSession(SESSION, { api, now: time.now, location: place() });

    await expect(session.getAccessToken()).resolves.toBe('token-1');
    time.advance(269_000);
    await expect(session.getAccessToken()).resolves.toBe('token-1');
    expect(api.refresh).not.toHaveBeenCalled();
  });

  it('renews the token once for parallel requests', async () => {
    const api = fakeApi({ refresh: vi.fn(() => Promise.resolve({ ...SESSION, access_token: 'token-2' })) });
    const time = clock();
    const session = createSession(SESSION, { api, now: time.now, location: place() });
    time.advance(271_000);

    const tokens = await Promise.all([session.getAccessToken(), session.getAccessToken()]);

    expect(tokens).toEqual(['token-2', 'token-2']);
    expect(api.refresh).toHaveBeenCalledTimes(1);
    await expect(session.getAccessToken()).resolves.toBe('token-2');
    expect(api.refresh).toHaveBeenCalledTimes(1);
  });

  it('shows the login page when the session has ended', async () => {
    const ended = new AuthError(401, 'session_expired', 'Сессия закончилась — войдите снова.');
    const api = fakeApi({ refresh: vi.fn(() => Promise.reject(ended)) });
    const location = place();
    const time = clock();
    const session = createSession(SESSION, { api, now: time.now, location });
    time.advance(300_000);

    await expect(session.getAccessToken()).rejects.toBe(ended);

    expect(location.reload).toHaveBeenCalledOnce();
  });

  it('keeps the page when the service is only unavailable and retries later', async () => {
    const outage = new AuthError(0, 'network', 'Нет связи.');
    const refresh = vi.fn().mockRejectedValueOnce(outage).mockResolvedValueOnce(SESSION);
    const location = place();
    const time = clock();
    const session = createSession(SESSION, { api: fakeApi({ refresh }), now: time.now, location });
    time.advance(300_000);

    await expect(session.getAccessToken()).rejects.toBe(outage);
    await expect(session.getAccessToken()).resolves.toBe('token-1');

    expect(location.reload).not.toHaveBeenCalled();
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it('logs out and goes to the start page, even if the service is down', async () => {
    for (const logout of [vi.fn(() => Promise.resolve()), vi.fn(() => Promise.reject(new Error('down')))]) {
      const location = place();
      createSession(SESSION, { api: fakeApi({ logout }), location }).logout();

      await vi.waitFor(() => expect(location.assign).toHaveBeenCalledWith('/'));
      expect(logout).toHaveBeenCalledOnce();
    }
  });

  it('works with the defaults', async () => {
    await expect(createSession(SESSION).getAccessToken()).resolves.toBe('token-1');
  });
});
