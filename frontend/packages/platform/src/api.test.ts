import { describe, expect, it, vi } from 'vitest';

import { ApiError, createApiClient, errorMessage } from './api';

const session = { getAccessToken: () => Promise.resolve('token-123') };

function respond(status: number, body: string) {
  return vi.fn((_input: string, _init: RequestInit) =>
    Promise.resolve(new Response(body === '' ? null : body, { status })),
  );
}

describe('createApiClient', () => {
  it('sends the bearer token and returns parsed JSON', async () => {
    const fetchImpl = respond(200, '{"username":"ivanov"}');

    const result = await createApiClient(session, fetchImpl).get<{ username: string }>(
      '/api/portfolio/me',
    );

    expect(result).toEqual({ username: 'ivanov' });
    const [path, init] = fetchImpl.mock.calls[0]!;
    expect(path).toBe('/api/portfolio/me');
    expect(init).toEqual({
      method: 'GET',
      headers: { Accept: 'application/json', Authorization: 'Bearer token-123' },
    });
  });

  it.each([
    ['post', 'POST'],
    ['put', 'PUT'],
    ['patch', 'PATCH'],
  ] as const)('%s sends a JSON body', async (method, verb) => {
    const fetchImpl = respond(200, '{}');

    await createApiClient(session, fetchImpl)[method]('/api/x', { code: 'F1' });

    const [, init] = fetchImpl.mock.calls[0]!;
    expect(init.method).toBe(verb);
    expect(init.body).toBe('{"code":"F1"}');
    expect(init.headers).toMatchObject({ 'Content-Type': 'application/json' });
  });

  it('sends PUT and DELETE without a body', async () => {
    const fetchImpl = respond(204, '');
    const client = createApiClient(session, fetchImpl);

    await client.put('/api/x');
    await client.delete('/api/x');

    expect(fetchImpl.mock.calls.map(([, init]) => [init.method, init.body])).toEqual([
      ['PUT', undefined],
      ['DELETE', undefined],
    ]);
  });

  it('throws ApiError with status and parsed body on failure', async () => {
    const client = createApiClient(session, respond(403, '{"detail":"forbidden"}'));

    const error = await client.get('/api/x').catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 403, body: { detail: 'forbidden' } });
  });

  it('keeps a non-JSON error body as text', async () => {
    const client = createApiClient(session, respond(502, 'Bad Gateway'));

    await expect(client.get('/api/x')).rejects.toMatchObject({ status: 502, body: 'Bad Gateway' });
  });

  it('returns null for an empty body', async () => {
    const client = createApiClient(session, respond(204, ''));

    expect(await client.get('/api/x')).toBeNull();
  });

  it('uses global fetch by default', async () => {
    const globalFetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{"ok":true}', { status: 200 }));

    expect(await createApiClient(session).get('/api/x')).toEqual({ ok: true });
    expect(globalFetch).toHaveBeenCalledOnce();
    globalFetch.mockRestore();
  });
});

describe('errorMessage', () => {
  it('shows the rule message from the service', () => {
    const error = new ApiError(422, {
      detail: { code: 'physical_fund_count', message: 'Physical должен принадлежать одному фонду.' },
    });

    expect(errorMessage(error)).toBe('Physical должен принадлежать одному фонду.');
  });

  it.each([
    [403, 'Недостаточно прав'],
    [404, 'не найден'],
    [422, 'Проверьте введённые данные'],
    [500, 'ошибкой 500'],
  ])('explains status %i', (status, text) => {
    expect(errorMessage(new ApiError(status, { detail: 'plain' }))).toContain(text);
    expect(errorMessage(new ApiError(status, null))).toContain(text);
  });

  it('explains network failures', () => {
    expect(errorMessage(new TypeError('Failed to fetch'))).toContain('Не удалось связаться');
  });
});
