import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ResultSummary } from '../api';
import { fakeApi, period, renderBooking, resultSummary } from '../test/fixtures';

const PAGE = '/booking/results?portfolio=p-fund&date=2026-12-03';

function rows(): string[] {
  return within(screen.getByRole('table', { name: 'Финансовый результат' }))
    .getAllByRole('row')
    .map((row) => row.textContent ?? '');
}

function tiles(): string[] {
  return within(screen.getByLabelText('Итоги результата'))
    .getAllByRole('term')
    .map((dt) => `${dt.textContent}: ${dt.nextElementSibling?.textContent}`);
}

describe('ResultsPage (decision 167)', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-12-03T09:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows the statement: articles as rows, periods as columns', async () => {
    const api = renderBooking(fakeApi(), PAGE);

    await screen.findByRole('table', { name: 'Финансовый результат' });
    expect(api.resultSummary).toHaveBeenCalledWith('p-fund', '2026-12-03', null);
    expect(rows()).toEqual([
      'Статья, ₽День03.12Неделя27.11–03.1230 дней04.11–03.12С нач. кварталас 01.10С нач. годас 01.01',
      'Бумаги',
      'Переоценка−6 000+48 000+48 000+48 000+48 000',
      'Реализованный результат0+20 000+20 000+20 000+24 484',
      'Купонный доход0+27 400+27 400+27 400+27 400',
      '+Результат по бумагам−6 000+95 400+95 400+95 400+99 884',
      'Финансовый результат−6 000+95 400+95 400+95 400+99 884',
      'Справочно',
      'Стоимость на конец периода100 095 400100 095 400100 095 400100 095 400100 095 400',
      'Приток: паи и переводы00100 000 000100 000 000100 000 000',
    ]);
    expect(tiles()).toEqual([
      'Стоимость портфеля: 100,1 млн ₽',
      'Результат за день: −6 000 ₽',
      'С начала года: +99 884 ₽',
      'С начала года, % годовых: 4,35%',
    ]);
    expect(screen.getByText('Портфели: OPIF1-BONDS, OPIF1-TREASURY.')).toBeInTheDocument();
  });

  it('chooses columns and the return rows and remembers them', async () => {
    const user = userEvent.setup();
    renderBooking(fakeApi(), PAGE);
    await screen.findByRole('table', { name: 'Финансовый результат' });

    const columns = within(screen.getByRole('group', { name: 'Колонки' }));
    await user.click(columns.getByRole('button', { name: 'День' }));
    await user.click(columns.getByRole('button', { name: 'Всё время' }));
    expect(rows()[0]).toBe(
      'Статья, ₽Неделя27.11–03.1230 дней04.11–03.12С нач. кварталас 01.10С нач. годас 01.01Всё времяс 26.11.2026',
    );
    expect(localStorage.getItem('am.booking.results.columns')).toBe('["week","days30","quarter","year","all"]');

    const returns = within(screen.getByRole('group', { name: 'Доходность' }));
    await user.click(returns.getByRole('button', { name: '% от вложений' }));
    await user.click(returns.getByRole('button', { name: '% годовых' }));
    expect(rows()).toContain('% от вложений0,10%0,36%0,76%4,21%0,10%');
    expect(rows()).toContain('% годовых4,97%4,35%4,35%4,35%4,35%');
    expect(rows()).toContain('Вложено, средневзвешенно100 000 00026 666 66712 500 0002 373 887100 000 000');
    expect([localStorage.getItem('am.booking.results.percent'), localStorage.getItem('am.booking.results.annual')]).toEqual([
      'true',
      'true',
    ]);

    // Ни одной колонки — подсказка вместо таблицы.
    for (const name of ['Неделя', '30 дней', 'С нач. квартала', 'С нач. года', 'Всё время']) {
      await user.click(columns.getByRole('button', { name }));
    }
    expect(screen.getByText(/Колонки не выбраны/)).toBeInTheDocument();
  });

  it('adds a custom period column, changes and removes it', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.resultSummary.mockImplementation((...args: unknown[]) => {
      const custom = args[2] as { from: string; till: string } | null;
      const base = resultSummary();
      return Promise.resolve<ResultSummary>(
        custom === null
          ? base
          : { ...base, periods: [...base.periods, period({ key: 'custom', first: custom.from, last: custom.till, days: 30 })] },
      );
    });
    renderBooking(api, PAGE);
    await screen.findByRole('table', { name: 'Финансовый результат' });

    await user.click(screen.getByRole('button', { name: '+ Свой период' }));
    await waitFor(() =>
      expect(api.resultSummary).toHaveBeenLastCalledWith('p-fund', '2026-12-03', { from: '2026-11-01', till: '2026-11-30' }),
    );
    expect(await screen.findByText('Ноябрь')).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem('am.booking.results.custom') ?? 'null')).toEqual({
      from: '2026-11-01',
      till: '2026-11-30',
    });

    fireEvent.change(screen.getByLabelText('Свой период с'), { target: { value: '2026-11-10' } });
    await waitFor(() =>
      expect(api.resultSummary).toHaveBeenLastCalledWith('p-fund', '2026-12-03', { from: '2026-11-10', till: '2026-11-30' }),
    );
    fireEvent.change(screen.getByLabelText('Свой период по'), { target: { value: '2026-11-20' } });
    await waitFor(() =>
      expect(api.resultSummary).toHaveBeenLastCalledWith('p-fund', '2026-12-03', { from: '2026-11-10', till: '2026-11-20' }),
    );
    expect(await screen.findByText('10.11–20.11')).toBeInTheDocument();
    // Стёртая дата ничего не меняет.
    fireEvent.change(screen.getByLabelText('Свой период с'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Свой период по'), { target: { value: '' } });
    expect(api.resultSummary).toHaveBeenLastCalledWith('p-fund', '2026-12-03', { from: '2026-11-10', till: '2026-11-20' });

    await user.click(screen.getByRole('button', { name: 'Убрать свой период' }));
    await waitFor(() => expect(api.resultSummary).toHaveBeenLastCalledWith('p-fund', '2026-12-03', null));
    expect(localStorage.getItem('am.booking.results.custom')).toBe('null');
    // Снова «Свой период» в переключателях — и снова выключить его там же.
    await user.click(screen.getByRole('button', { name: '+ Свой период' }));
    await user.click(await screen.findByRole('button', { name: 'Свой период' }));
    await waitFor(() => expect(api.resultSummary).toHaveBeenLastCalledWith('p-fund', '2026-12-03', null));
  });

  it('expands the securities result into bonds, the sold and redeemed ones apart', async () => {
    const user = userEvent.setup();
    renderBooking(fakeApi(), PAGE);
    await screen.findByRole('table', { name: 'Финансовый результат' });

    await user.click(screen.getByRole('button', { name: 'По бумагам' }));
    expect(rows().slice(5, 9)).toEqual([
      '−Результат по бумагам−6 000+95 400+95 400+95 400+99 884',
      'ОФЗ 26238 SU26238RMFS4−6 000+95 400+95 400+95 400+95 400',
      'Проданные и погашенные',
      'Старый выпуск RU000A10OLD10000+4 484',
    ]);
    await user.click(screen.getByRole('button', { name: 'Свернуть по бумагам' }));
    expect(screen.queryByText('Проданные и погашенные')).not.toBeInTheDocument();
  });

  it('shows REPO interest and fund expenses when there are any', async () => {
    const api = fakeApi();
    const [day, week, ...rest] = resultSummary().periods;
    api.resultSummary.mockResolvedValue(
      resultSummary({
        waiting_since: '2026-11-30',
        lines: [],
        periods: [
          { ...day!, flags: ['stale_price'], return_percent: null, annual_percent: null },
          { ...week!, repo: '2866.00', expenses: '-3000.00', charges: '-500.00', correction: '0.00' },
          ...rest,
        ],
      }),
    );
    const user = userEvent.setup();
    renderBooking(api, PAGE);
    await screen.findByRole('table', { name: 'Финансовый результат' });

    expect(rows()).toContain('РЕПО');
    expect(rows()).toContain('Проценты по РЕПО0+2 866000');
    expect(rows()).toContain('Расходы фонда0−3 000000');
    expect(rows()).toContain('Вознаграждение и комиссии от СЧА0−500000');
    expect(rows()).not.toContain('Корректировка комиссий при выплате00000');
    expect(rows()).toContain('Итого расходы0−3 500000');
    // Без бумаг раскрывать нечего; неполная оценка — звёздочкой у колонки.
    expect(screen.queryByRole('button', { name: 'По бумагам' })).not.toBeInTheDocument();
    expect(rows()[0]).toContain('День *03.12');
    expect(screen.getByText(/Оценка неполная в периодах со звёздочкой/)).toBeInTheDocument();
    expect(screen.getByText(/Пересчитывается с 30.11.2026/)).toBeInTheDocument();
    await user.click(within(screen.getByRole('group', { name: 'Доходность' })).getByRole('button', { name: '% годовых' }));
    expect(rows()).toContain('% годовых—4,97%4,35%4,35%4,35%');
  });

  it('colours returns by sign and counts a missing bond total as zero', async () => {
    localStorage.setItem('am.booking.results.percent', 'true');
    const api = fakeApi();
    const [day, week, ...rest] = resultSummary().periods;
    const [ofz] = resultSummary().lines;
    api.resultSummary.mockResolvedValue(
      resultSummary({
        periods: [{ ...day!, total: '0.00', revaluation: '0.00' }, { ...week!, return_percent: '0.0000' }, ...rest],
        lines: [{ ...ofz!, totals: { week: '95400.00' } }],
      }),
    );
    const user = userEvent.setup();
    renderBooking(api, PAGE);
    await screen.findByRole('table', { name: 'Финансовый результат' });

    expect(screen.getByText('−0,01%')).toHaveClass('down');
    expect(screen.getByText('0,00%')).not.toHaveClass('up');
    expect(tiles()[1]).toBe('Результат за день: 0 ₽');
    await user.click(screen.getByRole('button', { name: 'По бумагам' }));
    expect(rows()).toContain('ОФЗ 26238 SU26238RMFS40+95 400000');
  });

  it('keeps the portfolio and the date in the address', async () => {
    const user = userEvent.setup();
    const api = renderBooking(fakeApi(), '/booking/results');
    await screen.findByRole('table', { name: 'Финансовый результат' });
    expect(api.resultSummary).toHaveBeenLastCalledWith('p-fund', '2026-12-03', null);

    await user.selectOptions(screen.getByRole('combobox', { name: 'Портфель' }), 'p-bonds');
    await waitFor(() => expect(api.resultSummary).toHaveBeenLastCalledWith('p-bonds', '2026-12-03', null));
    fireEvent.change(screen.getByLabelText('Дата'), { target: { value: '2026-11-30' } });
    await waitFor(() => expect(api.resultSummary).toHaveBeenLastCalledWith('p-bonds', '2026-11-30', null));
    fireEvent.change(screen.getByLabelText('Дата'), { target: { value: '' } });
    expect(api.resultSummary).toHaveBeenLastCalledWith('p-bonds', '2026-11-30', null);
    expect(screen.getByRole('button', { name: 'Сегодня' })).toHaveAttribute('aria-pressed', 'false');
    await user.click(screen.getByRole('button', { name: 'Сегодня' }));
    await waitFor(() => expect(api.resultSummary).toHaveBeenLastCalledWith('p-bonds', '2026-12-03', null));
    expect(screen.getByRole('status', { name: 'Адрес' })).toHaveTextContent('portfolio=p-bonds&date=2026-12-03');
  });

  it('explains when there are no portfolios', async () => {
    const api = fakeApi();
    api.portfolios.mockResolvedValue([]);
    renderBooking(api, '/booking/results');

    expect(await screen.findByText(/Нет доступных портфелей/)).toBeInTheDocument();
  });
});
