import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import RefdataApp from './App';
import { CLASSIFICATIONS, instrument, ISSUER, RATINGS, SCALE, SENIORITY, session } from './test/fixtures';

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Ответы сервиса по началу пути запроса, как их видит браузер; остальное — пустой список. */
const ANSWERS: readonly (readonly [string, unknown])[] = [
  ['/api/refdata/instruments/', instrument()],
  ['/api/refdata/issuers/', ISSUER],
  ['/api/refdata/ratings/scale', SCALE],
  ['/api/refdata/ratings?', RATINGS],
  ['/api/refdata/classifiers', [SENIORITY]],
  ['/api/refdata/classifications?', CLASSIFICATIONS],
];

function stubService() {
  const fetchMock = vi.fn((path: string) => {
    const body = ANSWERS.find(([prefix]) => path.startsWith(prefix))?.[1] ?? [];
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/** Как в shell: раздел смонтирован под /refdata/*. */
function renderInShell(roles: Parameters<typeof session>, path = '/refdata') {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/refdata/*" element={<RefdataApp session={session(...roles)} basePath="/refdata" />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('RefdataApp', () => {
  it('opens instruments by default and switches to the calendar', async () => {
    const fetchMock = stubService();
    renderInShell(['trading']);

    expect(await screen.findByText(/пока нет бумаг/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/refdata/instruments', expect.anything());

    await userEvent.click(screen.getByRole('link', { name: 'Торговый календарь' }));
    expect(await screen.findByText(/Исключений за \d{4} год нет/)).toBeInTheDocument();
    // Трейдинг календарь не ведёт.
    expect(screen.queryByRole('form', { name: 'Исключение календаря' })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('link', { name: 'Бумаги' }));
    expect(await screen.findByText(/пока нет бумаг/)).toBeInTheDocument();
  });

  it('lets operations keep the calendar', async () => {
    stubService();
    renderInShell(['operations'], '/refdata/calendar');

    expect(await screen.findByRole('form', { name: 'Исключение календаря' })).toBeInTheDocument();
  });

  it.each([
    [['operations'] as const, true],
    [['trading'] as const, false],
  ])('opens the instrument card; editing for %s: %s', async (roles, canEdit) => {
    const fetchMock = stubService();
    renderInShell([...roles], '/refdata/instruments/i-1');

    expect(await screen.findByText('ОФЗ 26238', { selector: '.tag' })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/refdata/instruments/i-1', expect.anything());
    expect(screen.queryByRole('button', { name: 'Изменить' }) !== null).toBe(canEdit);
  });

  it('switches to issuers and classifiers; risk management keeps classifiers', async () => {
    const fetchMock = stubService();
    renderInShell(['risk']);

    await userEvent.click(screen.getByRole('link', { name: 'Эмитенты' }));
    expect(await screen.findByText(/Эмитентов пока нет/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/refdata/issuers', expect.anything());

    await userEvent.click(screen.getByRole('link', { name: 'Классификаторы' }));
    expect(await screen.findByRole('button', { name: 'Новый классификатор' })).toBeInTheDocument();
  });

  it.each([
    [['risk'] as const, true],
    [['operations'] as const, false],
  ])('opens the issuer card; ratings for %s: %s', async (roles, canRate) => {
    stubService();
    renderInShell([...roles], '/refdata/issuers/e-1');

    expect(await screen.findByRole('heading', { name: 'Министерство финансов Российской Федерации' })).toBeInTheDocument();
    expect(await screen.findByRole('list', { name: 'Действующие рейтинги' })).toBeInTheDocument();
    if (canRate) {
      expect(await screen.findByRole('button', { name: 'Добавить запись' })).toBeInTheDocument();
    } else {
      expect(screen.queryByRole('button', { name: 'Добавить запись' })).not.toBeInTheDocument();
    }
  });

  it('shows loading from ISS only to those who may load', async () => {
    const fetchMock = stubService();
    fetchMock.mockImplementation((path: string) =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            path.startsWith('/api/refdata/lookup')
              ? { secid: 'SU26238RMFS4', isin: null, kind: 'bond', params: instrument().current.params, instrument_id: null }
              : [],
          ),
          { status: 200 },
        ),
      ),
    );
    renderInShell([]);

    // Без права загрузки — «Найти в ISS»: посмотреть бумагу можно, загрузить нельзя.
    await userEvent.click(await screen.findByRole('button', { name: 'Найти в ISS' }));
    await userEvent.type(screen.getByLabelText('Код бумаги'), 'SU26238RMFS4');
    await userEvent.click(screen.getByRole('button', { name: 'Найти в MOEX ISS' }));

    expect(await screen.findByText(/могут трейдинг и сопровождение/)).toBeInTheDocument();
  });
});
