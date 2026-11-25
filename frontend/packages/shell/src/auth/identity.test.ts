import { afterEach, describe, expect, it, vi } from 'vitest';

import { AuthError, authMessage, createIdentityApi, identity } from './identity';

function respond(status: number, body: unknown = null) {
  const text = body === null ? '' : typeof body === 'string' ? body : JSON.stringify(body);
  return vi.fn((_input: string, _init: RequestInit) => Promise.resolve(new Response(text || null, { status })));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('identity api', () => {
  it('sends each request to the identity service', async () => {
    const fetchImpl = respond(200, { ok: true });
    const api = createIdentityApi(fetchImpl);

    await api.login('ivanov', 'secret', true);
    await api.firstLogin('ivanov', 'temporary', 'new-secret', false);
    await api.refresh();
    await api.logout();
    await api.invite('a/b');
    await api.acceptInvite('a/b', 'new-secret', true);
    await api.changePassword('token-1', 'old', 'new');

    const calls = fetchImpl.mock.calls.map(([url, init]) => [init.method, url, init.body ?? null]);
    expect(calls).toEqual([
      ['POST', '/api/identity/public/auth/login', '{"username":"ivanov","password":"secret","remember":true}'],
      [
        'POST',
        '/api/identity/public/auth/first-login',
        '{"username":"ivanov","password":"temporary","new_password":"new-secret","remember":false}',
      ],
      ['POST', '/api/identity/public/auth/refresh', null],
      ['POST', '/api/identity/public/auth/logout', null],
      ['GET', '/api/identity/public/invites/a%2Fb', null],
      ['POST', '/api/identity/public/invites/a%2Fb/accept', '{"password":"new-secret","remember":true}'],
      ['POST', '/api/identity/me/password', '{"current_password":"old","new_password":"new"}'],
    ]);
    // Cookie с refresh token ходит только на свой адрес; токен — только при смене пароля.
    expect(fetchImpl.mock.calls[0]![1].credentials).toBe('same-origin');
    expect(fetchImpl.mock.calls[0]![1].headers).not.toHaveProperty('Authorization');
    expect(fetchImpl.mock.calls[6]![1].headers).toMatchObject({ Authorization: 'Bearer token-1' });
  });

  it('returns the payload, or nothing for an empty answer', async () => {
    await expect(createIdentityApi(respond(200, { access_token: 't' })).refresh()).resolves.toEqual({
      access_token: 't',
    });
    await expect(createIdentityApi(respond(204)).logout()).resolves.toBeNull();
  });

  it('turns refusals into errors with a code and a message', async () => {
    const refusal = { detail: { code: 'invalid_credentials', message: 'Неверный логин или пароль.' } };
    await expect(createIdentityApi(respond(401, refusal)).login('a', 'b', false)).rejects.toEqual(
      new AuthError(401, 'invalid_credentials', 'Неверный логин или пароль.'),
    );
    await expect(createIdentityApi(respond(422, { detail: [{ loc: ['body'] }] })).refresh()).rejects.toMatchObject({
      code: 'invalid',
      message: 'Проверьте введённые данные.',
    });
    await expect(createIdentityApi(respond(502, 'Bad gateway')).refresh()).rejects.toMatchObject({
      status: 502,
      code: 'server',
      message: 'Сервер ответил ошибкой 502. Повторите позже.',
    });
    await expect(createIdentityApi(respond(500)).refresh()).rejects.toMatchObject({ code: 'server' });
  });

  it('reports a network failure', async () => {
    const api = createIdentityApi(() => Promise.reject(new TypeError('Failed to fetch')));

    await expect(api.refresh()).rejects.toMatchObject({ status: 0, code: 'network' });
  });

  it('uses the browser fetch by default', async () => {
    const fetchMock = respond(200, { access_token: 't' });
    vi.stubGlobal('fetch', fetchMock);

    await identity.refresh();

    expect(fetchMock).toHaveBeenCalledWith('/api/identity/public/auth/refresh', expect.anything());
  });

  it('explains any error to the user', () => {
    expect(authMessage(new AuthError(423, 'account_locked', 'Подождите.'))).toBe('Подождите.');
    expect(authMessage(new Error('boom'))).toBe('Что-то пошло не так. Обновите страницу и повторите.');
  });
});
