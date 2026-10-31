import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import BookingApp from './App';
import { INSTRUMENTS, PORTFOLIOS, positions, session, trade } from './test/fixtures';

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Ответы сервисов по пути запроса, как их видит браузер. */
function stubServices() {
  const fetchMock = vi.fn((path: string) => {
    const body = path.includes('/api/portfolio/')
      ? PORTFOLIOS
      : path.includes('/api/refdata/')
        ? INSTRUMENTS
        : path.includes('/positions')
          ? positions()
          : path.includes('/trades/')
            ? trade()
            : [trade()];
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderInShell(path: string, ...roles: Parameters<typeof session>) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/booking/*" element={<BookingApp session={session(...roles)} basePath="/booking" />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('BookingApp', () => {
  it('opens the trades of the day', async () => {
    const fetchMock = stubServices();
    renderInShell('/booking', 'trading');

    expect(await screen.findByRole('link', { name: 'SU26238RMFS4' })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/portfolio/portfolios', expect.anything());
    expect(screen.getByRole('link', { name: 'Позиции' })).toHaveAttribute('href', '/booking/positions');
  });

  it.each([
    [['operations'] as const, true],
    [['trading'] as const, false],
  ])('opens a trade; confirmation for %s: %s', async (roles, canConfirm) => {
    stubServices();
    renderInShell('/booking/trades/t-1', ...roles);

    expect(await screen.findByRole('heading', { name: /SU26238RMFS4/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Подтвердить' }) !== null).toBe(canConfirm);
  });

  it('opens positions', async () => {
    stubServices();
    renderInShell('/booking/positions', 'trading');

    expect(await screen.findByRole('table', { name: 'Позиции' })).toBeInTheDocument();
  });
});
