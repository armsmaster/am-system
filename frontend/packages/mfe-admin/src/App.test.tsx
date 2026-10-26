import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AdminApp from './App';
import { session } from './test/fixtures';

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Как в shell: раздел смонтирован под /admin/*. */
function renderInShell(roles: Parameters<typeof session>, path = '/admin') {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/admin/*" element={<AdminApp session={session(...roles)} basePath="/admin" />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('AdminApp', () => {
  it('refuses everyone but admin', () => {
    renderInShell(['trading', 'operations']);

    expect(screen.getByRole('alert')).toHaveTextContent('только администратору');
  });

  it('opens portfolios by default and switches sections', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response('[]', { status: 200 })));
    vi.stubGlobal('fetch', fetchMock);
    renderInShell(['admin']);

    expect(await screen.findByText(/Начните с фонда/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/portfolio/portfolios', expect.anything());

    await userEvent.click(screen.getByRole('link', { name: 'Счета' }));
    expect(await screen.findByText('Счетов пока нет.')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('link', { name: 'Доступы' }));
    expect(await screen.findByRole('columnheader', { name: 'Пользователь' })).toBeInTheDocument();
  });
});
