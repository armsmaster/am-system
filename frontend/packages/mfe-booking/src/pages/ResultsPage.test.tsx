import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { fakeApi, renderBooking, results } from '../test/fixtures';

const PAGE = '/booking/results?portfolio=p-fund&from=2026-11-27&till=2026-11-30';

describe('ResultsPage', () => {
  it('shows the result by day and by bond with totals', async () => {
    const api = renderBooking(fakeApi(), PAGE);

    const days = within(await screen.findByRole('table', { name: 'Результат по дням' })).getAllByRole('row');
    expect(api.results).toHaveBeenCalledWith('p-fund', '2026-11-27', '2026-11-30');
    expect(days[1]).toHaveTextContent('27.11.2026100 025 000,000,0025 000,000,000,000,000,000,000,000,0025 000,00');
    // Итого: стоимость — на конец периода, части и результат — суммы по дням.
    expect(days[3]).toHaveTextContent('Итого100 066 000,000,0036 000,0020 000,0010 000,000,000,000,000,000,0066 000,00');
    const bonds = within(screen.getByRole('table', { name: 'Результат по бумагам' })).getAllByRole('row');
    expect(bonds[1]).toHaveTextContent('SU26238RMFS4 — ОФЗ 2623836 000,0020 000,0010 000,000,0066 000,00');
    expect(screen.getByText('Портфели: OPIF1-BONDS, OPIF1-TREASURY.')).toBeInTheDocument();
    // Дата дня ведёт к сделкам портфеля за этот день.
    expect(within(days[1]!).getByRole('link', { name: '27.11.2026' })).toHaveAttribute(
      'href',
      '/booking?portfolio=p-fund&date=2026-11-27',
    );
  });

  it('changes the portfolio and the period', async () => {
    const user = userEvent.setup();
    const api = renderBooking(fakeApi(), PAGE);

    await screen.findByRole('table', { name: 'Результат по дням' });
    await user.selectOptions(screen.getByRole('combobox', { name: 'Портфель' }), 'p-bonds');
    expect(api.results).toHaveBeenLastCalledWith('p-bonds', '2026-11-27', '2026-11-30');
    fireEvent.change(screen.getByLabelText('С'), { target: { value: '2026-11-01' } });
    fireEvent.change(screen.getByLabelText('По'), { target: { value: '2026-11-15' } });
    expect(api.results).toHaveBeenLastCalledWith('p-bonds', '2026-11-01', '2026-11-15');
    // Пустая дата — не запрос.
    fireEvent.change(screen.getByLabelText('По'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('С'), { target: { value: '' } });
    expect(api.results).toHaveBeenLastCalledWith('p-bonds', '2026-11-01', '2026-11-15');
    expect(fireEvent.submit(screen.getByRole('form', { name: 'Портфель и период' }))).toBe(false);
  });

  it('marks incomplete days and a pending recalculation', async () => {
    const api = fakeApi();
    const base = results();
    api.results.mockResolvedValue(
      results({
        waiting_since: '2026-11-30',
        days: [{ ...base.days[0]!, flags: ['no_price'] }],
        lines: [],
      }),
    );
    renderBooking(api, PAGE);

    expect(await screen.findByText(/Пересчитывается с 30.11.2026/)).toBeInTheDocument();
    expect(screen.getByText(/Оценка неполная в днях со звёздочкой/)).toHaveTextContent('цены не было');
    const days = within(screen.getByRole('table', { name: 'Результат по дням' })).getAllByRole('row');
    expect(days[1]).toHaveTextContent('27.11.2026 *');
    expect(screen.queryByRole('table', { name: 'Результат по бумагам' })).not.toBeInTheDocument();
  });

  it('defaults to this month and explains an empty period', async () => {
    const api = fakeApi();
    api.results.mockResolvedValue(results({ days: [], lines: [] }));
    renderBooking(api, '/booking/results');

    expect(await screen.findByText('Расчёта за период нет.')).toBeInTheDocument();
    const [portfolio, first, last] = api.results.mock.calls[0]!;
    expect(portfolio).toBe('p-fund');
    expect(String(first).slice(8)).toBe('01');
    expect(String(first).slice(0, 8)).toBe(String(last).slice(0, 8));
  });

  it('explains when there are no portfolios', async () => {
    const api = fakeApi();
    api.portfolios.mockResolvedValue([]);
    renderBooking(api, '/booking/results');

    expect(await screen.findByText(/Нет доступных портфелей/)).toBeInTheDocument();
  });
});
