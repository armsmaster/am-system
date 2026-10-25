import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { fakeSession } from '../test/session';
import { Home } from './Home';

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(status: number, body: unknown) {
  const fetchMock = vi.fn(() =>
    Promise.resolve(new Response(JSON.stringify(body), { status })),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('Home', () => {
  it('shows how the services see the user', async () => {
    const fetchMock = stubFetch(200, { subject: 's', username: 'ivanov', roles: ['admin'] });

    render(<Home session={fakeSession('admin')} />);

    expect(screen.getByText('Проверяем связь с сервисами…')).toBeInTheDocument();
    expect(await screen.findByText('Администратор')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/portfolio/me', expect.anything());
  });

  it('says so when the user has no roles', async () => {
    stubFetch(200, { subject: 's', username: 'ivanov', roles: [] });

    render(<Home session={fakeSession()} />);

    expect(await screen.findByText('нет')).toBeInTheDocument();
  });

  it('reports an unavailable service', async () => {
    stubFetch(503, { detail: 'down' });

    render(<Home session={fakeSession()} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('Сервис портфелей недоступен');
  });

  it('ignores a response that arrives after leaving the page', async () => {
    let respond: (response: Response) => void = () => undefined;
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => (respond = resolve)));
    vi.stubGlobal('fetch', fetchMock);

    const { unmount } = render(<Home session={fakeSession()} />);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    unmount();
    // Ответ без настоящего потока: вся цепочка обработки — микрозадачи,
    // и один setTimeout гарантированно дожидается её конца.
    const text = vi.fn(() => Promise.resolve('{"subject":"s","username":"x","roles":[]}'));
    respond({ ok: true, status: 200, text } as unknown as Response);

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(text).toHaveBeenCalledOnce();
    expect(screen.queryByText('x')).not.toBeInTheDocument();
  });
});
