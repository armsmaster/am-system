import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { dayResult, fakeApi, positions, renderBooking } from '../test/fixtures';

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
      'SU26238RMFS4 — ОФЗ 2623830 00030 00052,7%15 810 000,000,0060 000,0041 700,009 780,00',
    );
    // Стоимость и результат дня по частям — части в сумме дают результат.
    const result = screen.getByLabelText('Стоимость и результат дня');
    expect(result).toHaveTextContent('стоимость портфеля60 103 400,00 ₽результат дня41 700,00 ₽переоценка36 000,00 ₽');
    expect(result).toHaveTextContent('вознаграждение и комиссии от СЧА0,00 ₽');
    expect(result).toHaveTextContent('корректировка комиссий при выплате0,00 ₽');
    expect(result).not.toHaveTextContent('приток');
    expect(screen.getByText(/DV01 портфеля/)).toHaveTextContent('по доходности — 9 780,00 ₽');
    await user.click(screen.getByText('Подробнее и лоты FIFO: 1'));
    expect(screen.getByLabelText('Оценка SU26238RMFS4')).toHaveTextContent(
      'средняя цена покупки52,5%стоимость покупки, ₽15 750 000,00уплаченный НКД, ₽1 050 300,00',
    );
    expect(screen.getByLabelText('Оценка SU26238RMFS4')).toHaveTextContent('модифицированная дюрация5,89');
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
        lines: [
          {
            ...line!,
            kind: 'fund_share',
            average_price: null,
            lots: [],
            price: '1.5',
            dv01: null,
            duration: null,
            metrics_basis: null,
          },
        ],
        dv01_spread: '0.00',
        dv01_yield: '0.00',
      }),
    );
    const user = userEvent.setup();
    renderBooking(api, '/booking/positions?portfolio=p-bonds&date=2026-12-02');

    const table = await screen.findByRole('table', { name: 'Позиции' });
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('30 0001,5 ₽15 810 000,00');
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('41 700,00—');
    expect(screen.queryByText(/DV01 портфеля/)).not.toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'Движения дня' })).not.toBeInTheDocument();
    await user.click(screen.getByText('Подробнее и лоты FIFO: 0'));
    expect(screen.getByLabelText('Оценка SU26238RMFS4')).toHaveTextContent('средняя цена покупки—');
  });

  it('marks an incomplete valuation and shows flows and unsettled trades', async () => {
    const api = fakeApi();
    const [line] = positions().lines;
    api.positions.mockResolvedValue(
      positions({
        result: dayResult({
          flows: '60000000.00',
          receivable: '11224200.00',
          payable: '28000500.00',
          flags: ['no_price', 'stale_price'],
        }),
        lines: [
          {
            ...line!,
            price: null,
            coupon_due: '1062000.00',
            principal_due: '250000.00',
            flags: ['stale_price', 'coupon_unknown', 'principal_unknown'],
            metrics_basis: 'spread',
            duration: '0.41',
          },
        ],
      }),
    );
    const user = userEvent.setup();
    renderBooking(api, '/booking/positions?portfolio=p-bonds&date=2026-12-02');

    const result = await screen.findByLabelText('Стоимость и результат дня');
    expect(result).toHaveTextContent('приток: паи и переводы60 000 000,00 ₽');
    expect(result).toHaveTextContent('к получению по сделкам до расчётов11 224 200,00 ₽');
    expect(result).toHaveTextContent('к уплате по сделкам до расчётов28 000 500,00 ₽');
    expect(screen.getByText(/Оценка неполная/)).toHaveTextContent(
      'Оценка неполная: цены не было — по цене покупки; цена не за этот день.',
    );
    const row = within(screen.getByRole('table', { name: 'Позиции' })).getAllByRole('row')[1];
    expect(row).toHaveTextContent('30 000— *');
    await user.click(screen.getByText('Подробнее и лоты FIFO: 1'));
    const details = screen.getByLabelText('Оценка SU26238RMFS4');
    expect(details).toHaveTextContent('купоны к получению, ₽1 062 000,00');
    expect(details).toHaveTextContent('погашение номинала к получению, ₽250 000,00');
    expect(details).toHaveTextContent('спред-дюрация0,41');
    expect(details).toHaveTextContent(
      'оценкацена не за этот день; сумма купона к получению не объявлена; сумма погашения к получению не объявлена',
    );
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
