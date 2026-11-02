import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import ReconciliationApp from './App';
import { ACCOUNTS, fundDay, PORTFOLIOS, session, statement } from './test/fixtures';

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Ответы сервисов по пути запроса, как их видит браузер. */
function stubServices() {
  const fetchMock = vi.fn((path: string) => {
    const body = path.endsWith('/portfolios')
      ? PORTFOLIOS
      : path.endsWith('/accounts')
        ? ACCOUNTS
        : path.includes('/statements')
          ? [statement()]
          : fundDay();
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderInShell(...roles: Parameters<typeof session>) {
  render(
    <MemoryRouter initialEntries={['/reconciliation']}>
      <Routes>
        <Route
          path="/reconciliation/*"
          element={<ReconciliationApp session={session(...roles)} basePath="/reconciliation" />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ReconciliationApp', () => {
  it('loads the day and switches to statements', async () => {
    const fetchMock = stubServices();
    renderInShell('operations');

    expect(await screen.findByRole('region', { name: 'OPIF1-BROKER' })).toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([path]) => path)).toContain('/api/portfolio/accounts');
    // Сопровождение может ввести «движений нет» по банку.
    expect(screen.getByRole('button', { name: /Движений нет/ })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('link', { name: 'Выписки' }));
    expect(await screen.findByRole('article', { name: 'Действующая выписка' })).toBeInTheDocument();
    expect(screen.getByRole('form', { name: 'Ввод выписки' })).toBeInTheDocument();
  });

  it('shows the trader the day without actions', async () => {
    stubServices();
    renderInShell('trading');

    expect(await screen.findByRole('region', { name: 'OPIF1-BROKER' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Движений нет/ })).not.toBeInTheDocument();
  });
});
