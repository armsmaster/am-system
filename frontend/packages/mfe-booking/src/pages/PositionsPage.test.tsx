import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { fakeApi, positions, renderBooking } from '../test/fixtures';

describe('PositionsPage', () => {
  it('shows positions on two dates, lots and the movements of the day', async () => {
    const user = userEvent.setup();
    const api = renderBooking(fakeApi(), '/booking/positions?portfolio=p-bonds&date=2026-12-02');

    const table = await screen.findByRole('table', { name: 'Позиции' });
    expect(api.positions).toHaveBeenCalledWith('p-bonds', '2026-12-02');
    expect(screen.getByText(/Деньги по дате расчётов/)).toHaveTextContent(
      'Деньги по дате расчётов на конец 02.12.2026: −15 714 300,00 ₽',
    );
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent(
      'SU26238RMFS4 — ОФЗ 2623830 00030 00052,5%15 750 000,001 050 300,00',
    );
    await user.click(screen.getByText('Лоты FIFO: 1'));
    expect(within(screen.getByRole('table', { name: 'Лоты SU26238RMFS4' })).getAllByRole('row')[1]).toHaveTextContent(
      'OPIF1-BONDS27.11, 11:4230 00052,5%15 750 000,00',
    );
    expect(within(screen.getByRole('table', { name: 'Движения дня' })).getAllByRole('row')[1]).toHaveTextContent(
      'OPIF1-BONDSкупонSU26238RMFS4—1 062 000,00',
    );
  });

  it('explains stale and missing calculations', async () => {
    const api = fakeApi();
    const [state] = positions().portfolios;
    api.positions.mockResolvedValue(
      positions({
        lines: [],
        portfolios: [
          { ...state!, code: 'A', waiting_since: '2026-11-30' },
          { ...state!, code: 'B', as_of: null, movements: [] },
          { ...state!, code: 'C', as_of: '2026-12-01', movements: [] },
          {
            ...state!,
            id: 'x',
            code: 'D',
            movements: [
              { kind: 'settlement', instrument_id: 'gone', trade_id: 't', operation_id: null, quantity: -20000, cash: '11224200.00' },
              { kind: 'transfer_in', instrument_id: null, trade_id: null, operation_id: 'o', quantity: 0, cash: '60000000.00' },
            ],
          },
        ],
      }),
    );
    renderBooking(api, '/booking/positions?portfolio=p-fund&date=2026-12-02');

    expect(await screen.findByText(/A: пересчитывается с 30.11.2026/)).toBeInTheDocument();
    expect(screen.getByText(/B: расчёта ещё нет/)).toBeInTheDocument();
    expect(screen.getByText('C: последний расчёт — на 01.12.2026.')).toBeInTheDocument();
    expect(screen.getByText('Бумаг нет.')).toBeInTheDocument();
    const moves = within(screen.getByRole('table', { name: 'Движения дня' })).getAllByRole('row');
    expect(moves[2]).toHaveTextContent('Dрасчёты по сделкеgone−20 00011 224 200,00');
    expect(moves[3]).toHaveTextContent('Dперевод: пришло——60 000 000,00');
  });

  it('shows a fund share price in roubles and a day without movements', async () => {
    const api = fakeApi();
    const [line] = positions().lines;
    const [state] = positions().portfolios;
    api.positions.mockResolvedValue(
      positions({
        portfolios: [{ ...state!, movements: [] }],
        lines: [{ ...line!, kind: 'fund_share', average_price: null, lots: [] }],
      }),
    );
    renderBooking(api, '/booking/positions?portfolio=p-bonds&date=2026-12-02');

    const table = await screen.findByRole('table', { name: 'Позиции' });
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('30 000—15 750');
    expect(screen.queryByRole('table', { name: 'Движения дня' })).not.toBeInTheDocument();
  });

  it('shows units and settlements with unit holders of the treasury portfolio', async () => {
    const api = fakeApi();
    api.positions.mockResolvedValue(
      positions({ units: '100000.50000', claims: '1000.00', obligations: '0', expenses: '20000.00', lines: [] }),
    );
    renderBooking(api, '/booking/positions?portfolio=p-treasury&date=2026-11-26');

    const list = await screen.findByLabelText('Паи и расчёты с пайщиками');
    expect(list).toHaveTextContent('паи фонда100 000,5 шт.требования к пайщикам1 000,00 ₽начисленные расходы20 000,00 ₽');
    expect(list).not.toHaveTextContent('обязательства');
  });

  it('explains when there are no portfolios', async () => {
    const api = fakeApi();
    api.portfolios.mockResolvedValue([]);
    renderBooking(api, '/booking/positions');

    expect(await screen.findByText(/Нет доступных портфелей/)).toBeInTheDocument();
  });
});
