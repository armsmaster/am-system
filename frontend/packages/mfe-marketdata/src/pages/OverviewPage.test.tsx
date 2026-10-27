import { act, fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { asApi, fakeApi, OFZ, renderPage, view, type FakeApi } from '../test/fixtures';
import { OverviewPage } from './OverviewPage';

function renderOverview(api: FakeApi = fakeApi()) {
  renderPage(<OverviewPage api={asApi(api)} basePath="/marketdata" />);
  return api;
}

function cells() {
  return screen
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell').map((c) => c.textContent));
}

afterEach(() => {
  vi.useRealTimers();
});

describe('OverviewPage', () => {
  it('shows the valuation price next to the other prices', async () => {
    renderOverview();

    await screen.findByRole('table');
    expect(cells()).toEqual([
      [
        'SU26238RMFS4ОФЗ 26238',
        '51,823 VWAP · quik, 27.10, 11:00',
        '51,823',
        '—',
        '51,784',
        '51,789',
        '—',
        '—',
        '7,7',
      ],
    ]);
    // Самая свежая цена типа — из скрипта, вчерашний итог ISS — нет.
    expect(screen.getAllByRole('cell')[2]).toHaveAttribute('title', 'quik, 27.10, 11:00');

    await userEvent.click(screen.getByRole('link', { name: 'SU26238RMFS4' }));
    expect(screen.getByText(`Открыта карточка ${OFZ.id}`)).toBeInTheDocument();
  });

  it('highlights a wide spread, a stale price and no price at all', async () => {
    const api = fakeApi();
    api.overview.mockResolvedValue([
      view({
        wide_spread: true,
        spread_bp: '193.1',
        price: { ...view().price!, stale: true },
        candidates: [
          { type: 'vwap', source: 'iss', value: '51.782000', time: '2026-10-26T20:59:59Z', today: false },
        ],
      }),
      view({ instrument: { ...OFZ, id: 'i-2', secid: 'LQDT' }, price: null, spread_bp: null, candidates: [] }),
    ]);
    renderOverview(api);

    const rows = (await screen.findAllByRole('row')).slice(1);
    expect(rows[0]).toHaveClass('wide');
    expect(within(rows[0]!).getByText('устарела')).toBeInTheDocument();
    expect(within(rows[0]!).getByText('51,782')).toHaveClass('muted');
    expect(within(rows[1]!).getByText('нет цены')).toBeInTheDocument();
  });

  it('takes the freshest price of a type whatever the order', async () => {
    const api = fakeApi();
    const [older, newer] = view().candidates;
    api.overview.mockResolvedValue([view({ candidates: [newer!, older!] })]);
    renderOverview(api);

    await screen.findByRole('table');
    expect(screen.getAllByRole('cell')[2]).toHaveTextContent('51,823');
  });

  it('says when there are no securities', async () => {
    const api = fakeApi();
    api.overview.mockResolvedValue([]);
    renderOverview(api);

    expect(await screen.findByText(/Бумаг пока нет/)).toBeInTheDocument();
  });

  it('shows prices at a chosen moment and returns to now', async () => {
    const api = renderOverview();
    await screen.findByRole('table');

    fireEvent.change(screen.getByLabelText('Момент, МСК'), { target: { value: '2026-10-26T18:00' } });
    // Enter в поле момента не перезагружает страницу.
    fireEvent.submit(screen.getByRole('form', { name: 'Момент' }));

    expect(await screen.findByText(/как их видела система тогда/)).toBeInTheDocument();
    expect(api.overview).toHaveBeenLastCalledWith('2026-10-26T18:00:00+03:00');

    await userEvent.click(screen.getByRole('button', { name: 'Сейчас' }));
    expect(api.overview).toHaveBeenLastCalledWith(undefined);
  });

  it('refreshes current prices every 30 seconds', async () => {
    vi.useFakeTimers();
    const api = renderOverview();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(api.overview).toHaveBeenCalledTimes(1);

    await act(() => vi.advanceTimersByTimeAsync(30_000));

    expect(api.overview).toHaveBeenCalledTimes(2);
  });
});
