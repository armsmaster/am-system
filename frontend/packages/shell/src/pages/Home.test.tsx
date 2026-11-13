import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { accessOf } from '../sections';
import { fakeSession } from '../test/session';
import { Home } from './Home';

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(status: number) {
  const fetchMock = vi.fn(() => Promise.resolve(new Response('{}', { status })));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/** 13.11.2026 в заданный час по Москве (UTC+3). */
function at(hour: number): Date {
  return new Date(Date.UTC(2026, 10, 13, hour - 3, 30));
}

function renderHome(session = fakeSession('trading'), now = at(15)) {
  return render(
    <MemoryRouter>
      <Home session={session} access={accessOf(session)} now={now} />
    </MemoryRouter>,
  );
}

describe('Home', () => {
  it('greets the user and lists the sections they can open', async () => {
    const fetchMock = stubFetch(200);

    renderHome(fakeSession('trading', 'operations'));

    expect(screen.getByRole('heading', { name: 'Добрый день, Иван Иванов' })).toBeInTheDocument();
    expect(screen.getByText('Трейдинг · Сопровождение · 13.11.2026')).toBeInTheDocument();
    const cards = within(screen.getByRole('list', { name: 'Разделы' })).getAllByRole('link');
    expect(cards.map((c) => c.getAttribute('href'))).toEqual([
      '/refdata',
      '/marketdata',
      '/booking',
      '/reconciliation',
      '/limits',
    ]);
    expect(cards[2]).toHaveTextContent('СделкиВвод, позиции, результат, СЧА');
    expect(screen.getByText('Проверяем связь с сервисами…')).toBeInTheDocument();
    expect(await screen.findByText('Сервисы на связи')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/portfolio/me', expect.anything());
  });

  it.each([
    [3, 'Доброй ночи'],
    [9, 'Доброе утро'],
    [20, 'Добрый вечер'],
  ])('greets by the Moscow time: %s h', (hour, text) => {
    stubFetch(200);
    renderHome(fakeSession(), at(hour));

    expect(screen.getByRole('heading')).toHaveTextContent(text);
    // Без ролей — только дата; разделы — те, что видны всем.
    expect(screen.getByText('13.11.2026')).toBeInTheDocument();
    expect(within(screen.getByRole('list', { name: 'Разделы' })).getAllByRole('link')).toHaveLength(2);
  });

  it('reports an unavailable service', async () => {
    stubFetch(503);

    renderHome();

    expect(await screen.findByRole('alert')).toHaveTextContent('Сервис портфелей недоступен');
  });

  it('uses the current time by default', () => {
    stubFetch(200);
    const session = fakeSession();
    render(
      <MemoryRouter>
        <Home session={session} access={accessOf(session)} />
      </MemoryRouter>,
    );

    expect(screen.getByRole('heading')).toHaveTextContent('Иван Иванов');
  });

  it('ignores a response that arrives after leaving the page', async () => {
    let respond: (response: Response) => void = () => undefined;
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => (respond = resolve)));
    vi.stubGlobal('fetch', fetchMock);

    const { unmount } = renderHome();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    unmount();
    // Ответ без настоящего потока: вся цепочка обработки — микрозадачи,
    // и один setTimeout гарантированно дожидается её конца.
    const text = vi.fn(() => Promise.resolve('{}'));
    respond({ ok: true, status: 200, text } as unknown as Response);

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(text).toHaveBeenCalledOnce();
    expect(screen.queryByText('Сервисы на связи')).not.toBeInTheDocument();
  });
});
