import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fakeApi, navHistory, refused, renderBooking, STANDARD_FILTER } from '../test/fixtures';

// График рисует canvas — в jsdom его нет; что именно рисуем, проверяет NavChart.test.
const charts = vi.hoisted(() => ({ titles: [] as string[] }));
vi.mock('lightweight-charts', () => ({
  LineSeries: 'line',
  CrosshairMode: { Magnet: 1 },
  LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 },
  createChart: () => ({
    addSeries: (_kind: string, options: { title: string }) => {
      charts.titles.push(options.title);
      return { setData: () => undefined };
    },
    timeScale: () => ({ fitContent: () => undefined }),
    remove: () => undefined,
  }),
}));

const PAGE = '/booking/nav?fund=p-fund';

function cards(): string[] {
  return within(screen.getByLabelText('Комиссии и сравнение'))
    .getAllByRole('term')
    .map((dt) => `${dt.textContent}: ${dt.nextElementSibling?.textContent}`);
}

function lastChart(): string[] {
  return [...charts.titles];
}

describe('NavPage (decision 168)', () => {
  beforeEach(() => {
    localStorage.clear();
    charts.titles.length = 0;
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-12-03T09:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('shows the unit price now and the forecast for tomorrow, biggest of all', async () => {
    const api = renderBooking(fakeApi(), PAGE);

    const now = await screen.findByRole('region', { name: 'Стоимость пая сейчас' });
    expect(api.navHistory).toHaveBeenCalledWith('p-fund', '2026-11-04', undefined, null);
    expect(now).toHaveTextContent('Стоимость пая · 03.12.20261 000,95 ₽−0,06 ₽ за день · СЧА 100,1 млн ₽ · 100 000 паёв');
    expect(screen.getByRole('region', { name: 'Прогноз на следующий торговый день' })).toHaveTextContent(
      'Прогноз на 04.12.2026 · фильтр Калмана1 001,12 ₽68%: 1 000,98 – 1 001,26 · тренд 0,1050 ₽ за день начисления до комиссий · ≈ 3,83% годовых после комиссий',
    );
    expect(cards()).toEqual(['Комиссии от СЧА: 1,50% годовыхуправление 1,5']);
    expect(lastChart()).toEqual(['Стоимость пая']);
    expect(screen.queryByRole('table', { name: 'СЧА по дням' })).not.toBeInTheDocument();
  });

  it('adds Kalman and LQDT with their cards and remembers the choice', async () => {
    const user = userEvent.setup();
    renderBooking(fakeApi(), PAGE);
    await screen.findByRole('region', { name: 'Стоимость пая сейчас' });

    const layers = within(screen.getByRole('group', { name: 'Слои графика' }));
    await user.click(layers.getByRole('button', { name: 'Калман' }));
    await user.click(layers.getByRole('button', { name: 'LQDT' }));
    expect(cards()).toEqual([
      'Комиссии от СЧА: 1,50% годовыхуправление 1,5',
      'Сглаженная · Калман: 1 001,02 ₽≈ 3,83% годовых после комиссий',
      'LQDT · 1 месяц: +0,11%2,1024 ₽ · ≈ 6,95% годовых',
    ]);
    expect(lastChart().slice(-3)).toEqual(['Стоимость пая', 'Калман', 'LQDT']);
    expect([localStorage.getItem('am.booking.nav.kalman'), localStorage.getItem('am.booking.nav.benchmark')]).toEqual([
      'true',
      'true',
    ]);

    // СЧА фонда — одна линия, без сглаживания и сравнения.
    await user.click(within(screen.getByRole('group', { name: 'Показатель' })).getByRole('button', { name: 'СЧА фонда' }));
    expect(lastChart().at(-1)).toBe('СЧА фонда');
    expect(within(screen.getByRole('group', { name: 'Слои графика' })).getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Таблица',
    ]);
    expect(cards()).toEqual(['Комиссии от СЧА: 1,50% годовыхуправление 1,5']);
    expect(localStorage.getItem('am.booking.nav.metric')).toBe('nav');
  });

  it('picks the period, a custom one included, and remembers it', async () => {
    const user = userEvent.setup();
    const api = renderBooking(fakeApi(), PAGE);
    await screen.findByRole('region', { name: 'Стоимость пая сейчас' });
    const periods = within(screen.getByRole('group', { name: 'Период' }));

    await user.click(periods.getByRole('button', { name: '3 месяца' }));
    await waitFor(() => expect(api.navHistory).toHaveBeenLastCalledWith('p-fund', '2026-09-04', undefined, null));
    await user.click(periods.getByRole('button', { name: 'Год' }));
    await waitFor(() => expect(api.navHistory).toHaveBeenLastCalledWith('p-fund', '2025-12-04', undefined, null));
    await user.click(periods.getByRole('button', { name: 'Весь срок' }));
    await waitFor(() => expect(api.navHistory).toHaveBeenLastCalledWith('p-fund', undefined, undefined, null));
    expect(localStorage.getItem('am.booking.nav.period')).toBe('all');

    await user.click(periods.getByRole('button', { name: 'Свой период' }));
    await waitFor(() => expect(api.navHistory).toHaveBeenLastCalledWith('p-fund', '2026-11-01', '2026-11-30', null));
    fireEvent.change(screen.getByLabelText('Свой период с'), { target: { value: '2026-11-10' } });
    await waitFor(() => expect(api.navHistory).toHaveBeenLastCalledWith('p-fund', '2026-11-10', '2026-11-30', null));
    fireEvent.change(screen.getByLabelText('Свой период по'), { target: { value: '2026-11-20' } });
    await waitFor(() => expect(api.navHistory).toHaveBeenLastCalledWith('p-fund', '2026-11-10', '2026-11-20', null));
    fireEvent.change(screen.getByLabelText('Свой период с'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Свой период по'), { target: { value: '' } });
    expect(api.navHistory).toHaveBeenLastCalledWith('p-fund', '2026-11-10', '2026-11-20', null);
    // Свой период запомнен: снова выбрать его — те же даты.
    await user.click(periods.getByRole('button', { name: '1 месяц' }));
    await user.click(periods.getByRole('button', { name: 'Свой период' }));
    await waitFor(() => expect(api.navHistory).toHaveBeenLastCalledWith('p-fund', '2026-11-10', '2026-11-20', null));
  });

  it('shows the days in a table and exports them to Excel', async () => {
    const created = vi.fn(() => 'blob:nav');
    URL.createObjectURL = created;
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const user = userEvent.setup();
    const api = fakeApi();
    renderBooking(api, PAGE);
    await screen.findByRole('region', { name: 'Стоимость пая сейчас' });

    await user.click(within(screen.getByRole('group', { name: 'Слои графика' })).getByRole('button', { name: 'Таблица' }));
    const rows = within(screen.getByRole('table', { name: 'СЧА по дням' })).getAllByRole('row');
    expect(rows[0]).toHaveTextContent('ДатаСтоимость пая, ₽Сглаженная, ₽За день, ₽СЧА, ₽ПаёвLQDT, ₽');
    expect(rows[1]).toHaveTextContent('03.12.20261 000,951 001,02−0,06100 095 000,00100 0002,1024');
    expect(rows[3]).toHaveTextContent('01.12.20261 000,601 000,64−0,06');
    expect(rows[6]).toHaveTextContent('28.11.20261 000,251 000,260,00');
    expect(rows[8]).toHaveTextContent('26.11.20261 000,001 000,00—100 000 000,00100 000—');
    expect(within(rows[2]!).getByText('+0,41')).toHaveClass('up');

    await user.click(screen.getByRole('button', { name: 'Экспортировать в Excel' }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(api.navWorkbook).toHaveBeenCalledWith('p-fund', '2026-11-04', undefined, null);
    expect(created).toHaveBeenCalled();

    api.navWorkbook.mockRejectedValueOnce(refused('Сервис сделок недоступен.'));
    await user.click(screen.getByRole('button', { name: 'Экспортировать в Excel' }));
    expect(await screen.findByText('Сервис сделок недоступен.')).toBeInTheDocument();
  });

  it('opens the commission rates on the right and lets operations add one', async () => {
    const user = userEvent.setup();
    const api = renderBooking(fakeApi(), PAGE, true);
    await screen.findByRole('region', { name: 'Стоимость пая сейчас' });

    await user.click(screen.getByRole('button', { name: 'Ставки комиссий' }));
    const panel = within(screen.getByRole('dialog', { name: 'Комиссии от СЧА' }));
    expect(within(panel.getByRole('table', { name: 'Ставки комиссий' })).getAllByRole('row')[1]).toHaveTextContent(
      'вознаграждение УК1,526.11.2026ПДУ п. 92ops',
    );
    const form = within(panel.getByRole('form', { name: 'Новая ставка' }));
    await user.selectOptions(form.getByLabelText('Комиссия'), 'specdep');
    await user.type(form.getByLabelText('Ставка, % годовых'), '0,15');
    fireEvent.change(form.getByLabelText('Действует с'), { target: { value: '2026-12-01' } });
    await user.type(form.getByLabelText('Основание'), 'договор');
    await user.click(form.getByRole('button', { name: 'Сохранить ставку' }));
    expect(api.setFeeRate).toHaveBeenCalledWith('p-fund', {
      kind: 'specdep',
      rate: '0.15',
      valid_from: '2026-12-01',
      comment: 'договор',
    });
    await waitFor(() => expect(api.feeRates).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(screen.queryByRole('dialog', { name: 'Комиссии от СЧА' })).not.toBeInTheDocument();
  });

  it('explains what it cannot show', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.feeRates.mockResolvedValue([]);
    api.navHistory.mockResolvedValue(
      navHistory({
        waiting_since: '2026-11-30',
        benchmark: null,
        forecast: null,
        points: navHistory().points.slice(0, 1).map((p) => ({ ...p, smoothed: null, benchmark: null })),
      }),
    );
    renderBooking(api, PAGE);
    await screen.findByRole('region', { name: 'Стоимость пая сейчас' });

    expect(screen.getByText(/Фонд пересчитывается с 30.11.2026/)).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Стоимость пая сейчас' })).toHaveTextContent(
      'Стоимость пая · 26.11.20261 000,00 ₽СЧА 100,0 млн ₽ · 100 000 паёв',
    );
    expect(screen.getByRole('region', { name: 'Прогноз на следующий торговый день' })).toHaveTextContent(
      'Прогноз на следующий торговый день · фильтр Калмана—',
    );
    const layers = within(screen.getByRole('group', { name: 'Слои графика' }));
    await user.click(layers.getByRole('button', { name: 'Калман' }));
    await user.click(layers.getByRole('button', { name: 'LQDT' }));
    expect(cards()).toEqual([
      'Комиссии от СЧА: не начисляются',
      'Сглаженная · Калман: —',
      'Сравнение: бумаги нет в справочнике',
    ]);
    expect(screen.getByText(/Бумаги для сравнения нет в справочнике/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Ставки комиссий' }));
    expect(screen.getByText(/Ставок нет/)).toBeInTheDocument();
  });

  it('shows a fund without a calculation and a benchmark without prices', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.navHistory.mockResolvedValue(navHistory({ points: [], forecast: null, first_day: null }));
    renderBooking(api, PAGE);

    expect(await screen.findByText('Расчёта за период нет.')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Стоимость пая сейчас' })).toHaveTextContent('Стоимость пая · ——');
    await user.click(within(screen.getByRole('group', { name: 'Слои графика' })).getByRole('button', { name: 'LQDT' }));
    expect(cards()[1]).toBe('LQDT · 1 месяц: —цен нет');
  });

  it('colours a falling or flat benchmark, skips days without a price and waits for the rates', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.feeRates.mockReturnValue(new Promise(() => undefined));
    const [first, second, third] = navHistory().points;
    api.navHistory.mockResolvedValue(
      navHistory({
        points: [
          { ...first!, benchmark: '2.1000' },
          { ...second!, benchmark: '2.0990', unit_price: null },
          { ...third!, benchmark: '2.0980' },
        ],
      }),
    );
    renderBooking(api, PAGE);
    await screen.findByRole('region', { name: 'Стоимость пая сейчас' });

    expect(cards()).toEqual(['Комиссии от СЧА: не начисляются']);
    const layers = within(screen.getByRole('group', { name: 'Слои графика' }));
    await user.click(layers.getByRole('button', { name: 'LQDT' }));
    expect(screen.getByText('−0,10%')).toHaveClass('down');
    await user.click(layers.getByRole('button', { name: 'Таблица' }));
    const rows = within(screen.getByRole('table', { name: 'СЧА по дням' })).getAllByRole('row');
    // День без стоимости пая: ни его изменения, ни изменения следующего дня.
    expect(rows[1]).toHaveTextContent('28.11.20261 000,251 000,26—');
    expect(rows[2]).toHaveTextContent('27.11.2026—1 000,25—');
  });

  it('shows a flat benchmark without a sign', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    const [first, second] = navHistory().points;
    api.navHistory.mockResolvedValue(navHistory({ points: [{ ...first!, benchmark: '2.1000' }, { ...second!, benchmark: '2.1000' }] }));
    renderBooking(api, PAGE);
    await screen.findByRole('region', { name: 'Стоимость пая сейчас' });

    await user.click(within(screen.getByRole('group', { name: 'Слои графика' })).getByRole('button', { name: 'LQDT' }));
    const flat = screen.getByText('0,00%');
    expect(flat).not.toHaveClass('up');
    expect(flat).not.toHaveClass('down');
  });

  it('saves a rate without a basis and shows rates without comments', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    const [rate] = await fakeApi().feeRates();
    api.feeRates.mockResolvedValue([{ ...rate!, comment: null }]);
    renderBooking(api, PAGE, true);
    await screen.findByRole('region', { name: 'Стоимость пая сейчас' });

    await user.click(screen.getByRole('button', { name: 'Ставки комиссий' }));
    const panel = within(screen.getByRole('dialog', { name: 'Комиссии от СЧА' }));
    expect(within(panel.getByRole('table', { name: 'Ставки комиссий' })).getAllByRole('row')[1]).toHaveTextContent('—');
    const form = within(panel.getByRole('form', { name: 'Новая ставка' }));
    await user.type(form.getByLabelText('Ставка, % годовых'), '1,2');
    await user.click(form.getByRole('button', { name: 'Сохранить ставку' }));
    expect(api.setFeeRate).toHaveBeenCalledWith('p-fund', expect.objectContaining({ kind: 'management', rate: '1.2', comment: null }));
  });

  it('opens own Kalman settings and marks the forecast made with them (decision 174)', async () => {
    const user = userEvent.setup();
    localStorage.setItem('am.booking.nav.kalman', 'true');
    localStorage.setItem('am.booking.nav.filter', JSON.stringify({ halfLife: 1, memory: 20 }));
    const api = fakeApi();
    api.navHistory.mockResolvedValue(
      navHistory({ filter: { ...STANDARD_FILTER, half_life: '1.00', trend_memory: '20.0', standard: false } }),
    );
    renderBooking(api, PAGE);

    await screen.findByRole('region', { name: 'Стоимость пая сейчас' });
    expect(api.navHistory).toHaveBeenCalledWith('p-fund', '2026-11-04', undefined, { halfLife: 1, memory: 20 });
    expect(screen.getByRole('region', { name: 'Прогноз на следующий торговый день' })).toHaveTextContent(
      'фильтр Калмана, свои настройки',
    );
    expect(cards()[1]).toContain('свои настройки: 1,0 и 20 дн.');

    await user.click(screen.getByRole('button', { name: 'Настройки фильтра' }));
    const panel = within(screen.getByRole('dialog', { name: 'Настройки фильтра Калмана' }));
    await user.click(panel.getByRole('button', { name: /По умолчанию/ }));
    await waitFor(() => expect(api.navHistory).toHaveBeenLastCalledWith('p-fund', '2026-11-04', undefined, null));
    expect(localStorage.getItem('am.booking.nav.filter')).toBe('null');
    await user.click(panel.getByRole('button', { name: 'Закрыть' }));
    expect(screen.queryByRole('dialog', { name: 'Настройки фильтра Калмана' })).not.toBeInTheDocument();
  });

  it('switches the fund and explains when there are no funds', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.portfolios.mockResolvedValue([
      ...(await fakeApi().portfolios()),
      { id: 'p-fund-2', code: 'OPIF2', name: 'ОПИФ 2', kind: 'compound', is_fund: true, parent_ids: [], fund_id: null },
    ]);
    renderBooking(api, PAGE);
    await screen.findByRole('region', { name: 'Стоимость пая сейчас' });
    await user.selectOptions(screen.getByRole('combobox', { name: 'Фонд' }), 'p-fund-2');
    await waitFor(() => expect(api.navHistory).toHaveBeenLastCalledWith('p-fund-2', '2026-11-04', undefined, null));

    const none = fakeApi();
    none.portfolios.mockResolvedValue([]);
    renderBooking(none, '/booking/nav');
    expect(await screen.findByText(/Нет доступных фондов/)).toBeInTheDocument();
  });
});
