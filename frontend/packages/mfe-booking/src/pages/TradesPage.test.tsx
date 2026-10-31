import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { fakeApi, refused, renderBooking, trade } from '../test/fixtures';

function rows(name: string): string[] {
  return within(screen.getByRole('table', { name }))
    .getAllByRole('row')
    .slice(1)
    .map((r) => r.textContent ?? '');
}

describe('TradesPage', () => {
  it('lists the trades of the fund for a day', async () => {
    const api = renderBooking(fakeApi(), '/booking?portfolio=p-fund&date=2026-11-27');

    await screen.findByRole('table', { name: 'Сделки за день' });
    expect(api.trades).toHaveBeenCalledWith('p-fund', '2026-11-27');
    expect(rows('Сделки за день')).toEqual([
      '27.11, 11:42OPIF1-BONDSSU26238RMFS4покупка50 00052,535,0128 000 500,0030.11.2026введена',
    ]);
    expect(screen.getByRole('link', { name: 'SU26238RMFS4' })).toHaveAttribute('href', '/booking/trades/t-1');
  });

  it('picks the first fund and today by default and keeps the choice in the address', async () => {
    const user = userEvent.setup();
    const api = renderBooking();
    await screen.findByRole('table', { name: 'Сделки за день' });
    expect(api.trades.mock.calls[0]?.[0]).toBe('p-fund');

    const form = within(screen.getByRole('form', { name: 'Портфель и дата' }));
    await user.selectOptions(form.getByRole('combobox'), 'p-bonds');
    await waitFor(() => expect(api.trades).toHaveBeenLastCalledWith('p-bonds', expect.any(String)));
    fireEvent.change(form.getByLabelText('Дата'), { target: { value: '2026-11-30' } });
    await waitFor(() => expect(api.trades).toHaveBeenLastCalledWith('p-bonds', '2026-11-30'));
    expect(screen.getByRole('status', { name: 'Адрес' })).toHaveTextContent('portfolio=p-bonds&date=2026-11-30');
  });

  it('shows cancelled trades greyed and an empty day', async () => {
    const api = fakeApi();
    api.trades.mockResolvedValueOnce([trade({ status: 'cancelled' })]);
    renderBooking(api, '/booking?portfolio=p-bonds&date=2026-11-27');
    const table = await screen.findByRole('table', { name: 'Сделки за день' });
    expect(within(table).getAllByRole('row')[1]).toHaveClass('muted');

    api.trades.mockResolvedValue([]);
    await userEvent.setup().click(screen.getByRole('button', { name: 'Новая сделка' }));
    await userEvent.setup().click(screen.getByRole('button', { name: 'Отмена' }));
    expect(screen.getByRole('button', { name: 'Новая сделка' })).toBeInTheDocument();
  });

  it('enters a trade with the accrued interest filled in and shows the warnings', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.enter.mockResolvedValue(
      trade({ side: 'sell', warnings: ['Продажа больше позиции: на 30.11.2026 в портфеле 50000 шт.'] }),
    );
    renderBooking(api, '/booking?portfolio=p-bonds&date=2026-11-30');
    await screen.findByRole('table', { name: 'Сделки за день' });

    await user.click(screen.getByRole('button', { name: 'Новая сделка' }));
    const form = within(screen.getByRole('form', { name: 'Новая сделка' }));
    expect(form.getByLabelText('Портфель')).toHaveValue('p-bonds');
    await user.selectOptions(form.getByLabelText('Бумага'), 'i-ofz');
    await waitFor(() => expect(form.getByLabelText('НКД на бумагу, ₽')).toHaveValue('35.01'));
    expect(form.getByLabelText('Дата расчётов')).toHaveValue('2026-11-30');
    expect(form.getByLabelText('Номинал на дату расчётов, ₽')).toHaveValue('1000');
    await user.selectOptions(form.getByLabelText('Направление'), 'sell');
    await user.type(form.getByLabelText('Количество, шт.'), '60000');
    await user.type(form.getByLabelText('Цена, % от номинала'), '52,6');
    await user.type(form.getByLabelText('Номер сделки у брокера'), 'B-17');
    await user.click(form.getByRole('button', { name: 'Ввести сделку' }));

    await screen.findByRole('status', { name: '' });
    const [body] = api.enter.mock.calls[0] as [Record<string, unknown>];
    expect(body).toMatchObject({
      portfolio_id: 'p-bonds',
      instrument_id: 'i-ofz',
      side: 'sell',
      quantity: 60000,
      price: '52.6',
      face_value: '1000',
      accrued: '35.01',
      fee: '0',
      external_id: 'B-17',
      counterparty: null,
      comment: null,
    });
    expect(screen.getByText(/Сделка введена/)).toHaveTextContent(
      'Сделка введена: SU26238RMFS4, продажа 50 000 шт., сумма 28 000 500,00 ₽, расчёты 30.11.2026.',
    );
    expect(screen.getByText(/Продажа больше позиции/)).toBeInTheDocument();
    expect(api.trades).toHaveBeenCalledTimes(2); // список перечитан
  });

  it('starts from the first physical portfolio when a fund is selected', async () => {
    const user = userEvent.setup();
    renderBooking(fakeApi(), '/booking?portfolio=p-fund&date=2026-11-30');
    await user.click(await screen.findByRole('button', { name: 'Новая сделка' }));

    const form = within(screen.getByRole('form', { name: 'Новая сделка' }));
    expect(form.getByLabelText('Портфель')).toHaveValue('p-treasury');
  });

  it('says when the day has no trades', async () => {
    const api = fakeApi();
    api.trades.mockResolvedValue([]);
    renderBooking(api, '/booking?portfolio=p-bonds&date=2026-11-28');

    expect(await screen.findByText('Сделок за этот день нет.')).toBeInTheDocument();
    // Enter в поле даты не перезагружает страницу.
    expect(fireEvent.submit(screen.getByRole('form', { name: 'Портфель и дата' }))).toBe(false);
  });

  it('explains when there are no portfolios', async () => {
    const api = fakeApi();
    api.portfolios.mockResolvedValue([]);
    api.trades.mockRejectedValue(refused('нет портфеля'));
    renderBooking(api);

    expect(await screen.findByText(/Нет доступных портфелей/)).toBeInTheDocument();
  });
});
