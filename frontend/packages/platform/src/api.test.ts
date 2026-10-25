import { describe, expect, it, vi } from 'vitest';

import { ApiError, createApiClient } from './api';

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
    expect(init.headers).toEqual({
      Accept: 'application/json',
      Authorization: 'Bearer token-123',
    });
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
