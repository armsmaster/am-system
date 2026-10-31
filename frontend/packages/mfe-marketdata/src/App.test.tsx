import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import MarketdataApp from './App';
import { BATCH, fixedAnalytics, series, session, view } from './test/fixtures';

vi.mock('./components/PriceChart', () => ({ PriceChart: () => <div role="img" aria-label="График цен" /> }));

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Ответы сервиса по пути запроса, как их видит браузер. */
function stubService() {
  const fetchMock = vi.fn((path: string) => {
    const body = path.includes('/feed/batches')
      ? [BATCH]
      : path.includes('/analytics')
        ? fixedAnalytics()
        : path.includes('/series')
        ? series()
        : path.includes('/price')
          ? view()
          : [view()];
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/** Как в shell: раздел смонтирован под /marketdata/*. */
function renderInShell(roles: Parameters<typeof session>, path = '/marketdata') {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/marketdata/*" element={<MarketdataApp session={session(...roles)} basePath="/marketdata" />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('MarketdataApp', () => {
  it('opens the overview and the script feed', async () => {
    const fetchMock = stubService();
    renderInShell(['operations']);

    expect(await screen.findByRole('link', { name: 'SU26238RMFS4' })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/marketdata/overview', expect.anything());

    await userEvent.click(screen.getByRole('link', { name: 'Лента скрипта' }));
    expect(await screen.findByText('снимки цен')).toBeInTheDocument();
  });

  it.each([
    [['trading'] as const, true],
    [['operations'] as const, false],
  ])('opens the price card; editing for %s: %s', async (roles, canEdit) => {
    stubService();
    renderInShell([...roles], '/marketdata/instruments/i-ofz');

    expect(await screen.findByRole('heading', { name: 'SU26238RMFS4 — ОФЗ 26238' })).toBeInTheDocument();
    expect(screen.queryByRole('form', { name: 'Ручная цена' }) !== null).toBe(canEdit);
  });
});
