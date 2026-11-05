import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import LimitsApp from './App';
import { CLASSIFIERS, fakeApi, FUNDS, fundDay, limitSet, renderLimits, session } from './test/fixtures';

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Ответы сервисов по пути запроса, как их видит браузер. */
function stubServices() {
  const answers: readonly (readonly [string, unknown])[] = [
    ['/api/limits/funds/f-dohod/days/', fundDay()],
    ['/api/limits/funds/f-dohod/sets', [limitSet(1)]],
    ['/api/limits/funds', FUNDS],
    ['/api/refdata/classifiers', CLASSIFIERS],
  ];
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      Promise.resolve(
        new Response(JSON.stringify(answers.find(([prefix]) => path.startsWith(prefix))?.[1] ?? []), {
          status: 200,
        }),
      ),
    ),
  );
}

function renderInShell(...roles: Parameters<typeof session>) {
  render(
    <MemoryRouter initialEntries={['/limits']}>
      <Routes>
        <Route path="/limits/*" element={<LimitsApp session={session(...roles)} basePath="/limits" />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('LimitsApp', () => {
  it('opens the usage and switches to the set; risk management edits it', async () => {
    stubServices();
    renderInShell('risk');

    expect(await screen.findByRole('table', { name: 'Лимиты' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('link', { name: 'Набор лимитов' }));
    expect(await screen.findByRole('form', { name: 'Новая версия набора' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('link', { name: 'Использование' }));
    expect(await screen.findByRole('table', { name: 'Лимиты' })).toBeInTheDocument();
  });

  it('opens the breaches and the monthly report', async () => {
    renderLimits();

    await userEvent.click(await screen.findByRole('link', { name: 'Нарушения' }));
    expect(await screen.findByRole('table', { name: 'Журнал нарушений' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('link', { name: 'Отчёт за месяц' }));
    expect(await screen.findByRole('button', { name: 'Скачать для Excel' })).toBeInTheDocument();
  });

  it('shows the set to the trader without the editor', async () => {
    stubServices();
    renderInShell('trading');
    await userEvent.click(await screen.findByRole('link', { name: 'Набор лимитов' }));

    expect(await screen.findByText(/Набор лимитов ведёт риск-менеджмент/)).toBeInTheDocument();
  });

  it('says when there is no fund to see', async () => {
    const api = fakeApi();
    api.funds.mockResolvedValue([]);
    renderLimits(api);

    expect(await screen.findByText('Нет фондов, к которым у вас есть доступ.')).toBeInTheDocument();
  });
});
