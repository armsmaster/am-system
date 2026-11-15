import { act, fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { asApi, fakeApi, renderPage, view, type FakeApi } from '../test/fixtures';
import { OverviewPage } from './OverviewPage';

// Вторник 27.10.2026, 11:05 по Москве.
const NOW = new Date('2026-10-27T08:05:00Z');

function renderOverview(api: FakeApi = fakeApi()) {
  renderPage(<OverviewPage api={asApi(api)} basePath="/marketdata" now={NOW} />);
  return api;
}

function cells() {
  return within(screen.getByRole('table', { name: 'Цены' }))
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell').map((c) => c.textContent));
}

afterEach(() => {
  vi.useRealTimers();
});

describe('OverviewPage', () => {
  it('shows the price, its kind, the change over a day and a week, face value and accrued interest', async () => {
    const api = renderOverview();

    await screen.findByRole('table', { name: 'Цены' });
    expect(api.overview).toHaveBeenCalledWith(undefined);
    expect(screen.getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Бумага',
      'Цена',
      'Тип',
      'День',
      'Неделя',
      'Номинал',
      'НКД',
    ]);
    // На телефоне тип и НКД — второй строкой; на компьютере эти строки скрыты.
    expect(cells()).toEqual([
      [
        'ОФЗ 26238SU26238RMFS4VWAP · quik',
        '51,823НКД 28,40 ₽',
        'VWAP · quik27.10, 11:00',
        '+0,14%',
        '+0,43%',
        '1 000,00 ₽',
        '28,40 ₽',
      ],
    ]);
    const [day, week] = screen.getAllByRole('cell').slice(3, 5);
    expect(day).toHaveClass('up');
    expect(day).toHaveAttribute('title', 'с 51,75 на конец 26.10.2026');
    expect(week).toHaveAttribute('title', 'с 51,6 на конец 20.10.2026');
    expect(
      screen.getByText('Текущие цены, обновляются каждые 30 секунд. Номинал и НКД — на дату расчётов 28.10.2026.'),
    ).toBeInTheDocument();
  });

  it('puts a stale price and a wide spread under the price; no price — dashes', async () => {
    const api = fakeApi();
    api.overview.mockResolvedValue([
      view({ price: { ...view().price!, value: '51.500000', stale: true }, wide_spread: true, spread_bp: '85.2' }),
      view({
        instrument: { id: 'i-fund', secid: 'LQDT', isin: null, kind: 'fund_share', short_name: 'LQDT ETF' },
        price: null,
        week_ago: { day: '2026-10-20', price: null },
        accrued: null,
        face_value: null,
      }),
    ]);
    renderOverview(api);

    await screen.findByRole('table', { name: 'Цены' });
    const [bond, fund] = cells();
    expect(bond!.slice(1, 4)).toEqual(['51,5НКД 28,40 ₽устареларазброс 85,2 бп', 'VWAP · quik27.10, 11:00', '−0,48%']);
    expect(screen.getAllByRole('row')[1]!.querySelectorAll('td')[3]).toHaveClass('down');
    expect(fund).toEqual(['LQDT ETFLQDT', 'нет цены', '—', '—', '—', '—', '—']);
    expect(screen.getAllByRole('row')[2]!.querySelectorAll('td')[4]).toHaveAttribute(
      'title',
      'нет цены на конец 20.10.2026',
    );
  });

  it('opens the price card by a click on the row or the name', async () => {
    renderOverview();

    await userEvent.click(await screen.findByText('1 000,00 ₽'));
    expect(screen.getByText('Открыта карточка i-ofz')).toBeInTheDocument();
  });

  it('opens the price card by the link', async () => {
    renderOverview();

    await userEvent.click(await screen.findByRole('link', { name: 'ОФЗ 26238' }));
    expect(screen.getByText('Открыта карточка i-ofz')).toBeInTheDocument();
  });

  it('says when there are no securities', async () => {
    const api = fakeApi();
    api.overview.mockResolvedValue([]);
    renderOverview(api);

    expect(await screen.findByText(/Бумаг пока нет/)).toBeInTheDocument();
    expect(screen.getByText('Текущие цены, обновляются каждые 30 секунд.')).toBeInTheDocument();
  });

  it('chooses the moment: yesterday, the end of last month, or a date and time by 15 minutes', async () => {
    const api = renderOverview();
    await screen.findByRole('table', { name: 'Цены' });

    await userEvent.click(screen.getByRole('button', { name: 'Вчера' }));
    expect(api.overview).toHaveBeenLastCalledWith('2026-10-26T23:59:59+03:00');
    expect(await screen.findByText(/^Цены на 26\.10, 23:59 МСК — как их видела система тогда\./)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Конец сентября' }));
    expect(api.overview).toHaveBeenLastCalledWith('2026-09-30T23:59:59+03:00');

    await userEvent.click(screen.getByRole('button', { name: 'Другой момент' }));
    expect(api.overview).toHaveBeenLastCalledWith('2026-10-27T11:00:00+03:00');
    await userEvent.click(screen.getByRole('button', { name: 'На 15 минут раньше' }));
    expect(api.overview).toHaveBeenLastCalledWith('2026-10-27T10:45:00+03:00');
    await userEvent.click(screen.getByRole('button', { name: 'На 15 минут позже' }));
    await userEvent.click(screen.getByRole('button', { name: 'На 15 минут позже' }));
    expect(screen.getByLabelText('Время, МСК')).toHaveValue('11:15');
    await userEvent.selectOptions(screen.getByLabelText('Время, МСК'), '23:59');
    expect(api.overview).toHaveBeenLastCalledWith('2026-10-27T23:59:59+03:00');
    fireEvent.change(screen.getByLabelText('Дата'), { target: { value: '2026-10-20' } });
    expect(api.overview).toHaveBeenLastCalledWith('2026-10-20T23:59:59+03:00');
    // Стёртая дата ничего не меняет.
    fireEvent.change(screen.getByLabelText('Дата'), { target: { value: '' } });
    expect(screen.getByLabelText('Дата')).toHaveValue('2026-10-20');

    await userEvent.click(screen.getByRole('button', { name: 'Сейчас' }));
    expect(api.overview).toHaveBeenLastCalledWith(undefined);
    expect(screen.queryByLabelText('Время, МСК')).not.toBeInTheDocument();
  });

  it('refreshes current prices every 30 seconds, but not a past moment', async () => {
    vi.useFakeTimers();
    const api = renderOverview();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(api.overview).toHaveBeenCalledTimes(1);

    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(api.overview).toHaveBeenCalledTimes(2);

    act(() => screen.getByRole('button', { name: 'Вчера' }).click());
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(api.overview).toHaveBeenCalledTimes(3);
  });
});
