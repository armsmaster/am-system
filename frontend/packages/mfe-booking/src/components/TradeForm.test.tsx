import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { fakeApi, PORTFOLIOS, refused, renderBooking, trade } from '../test/fixtures';

async function openForm(api = fakeApi()) {
  const user = userEvent.setup();
  renderBooking(api, '/booking?portfolio=p-bonds&date=2026-11-30');
  await user.click(await screen.findByRole('button', { name: 'Новая сделка' }));
  return { user, api, form: within(screen.getByRole('form', { name: 'Новая сделка' })) };
}

describe('TradeForm', () => {
  it('asks for the accrued interest when the market data has none', async () => {
    const api = fakeApi();
    api.defaults.mockResolvedValue({
      instrument_id: 'i-ofz',
      kind: 'bond',
      settle_date: '2026-12-03',
      face_value: null,
      accrued: null,
    });
    const { user, form } = await openForm(api);

    await user.selectOptions(form.getByLabelText('Бумага'), 'i-ofz');

    expect(await form.findByText(/НКД на дату расчётов неизвестен/)).toBeInTheDocument();
    expect(form.getByLabelText('НКД на бумагу, ₽')).toHaveValue('');
  });

  it('shows why the hint failed and refills on a new trade time', async () => {
    const api = fakeApi();
    api.defaults.mockRejectedValueOnce(refused('Бумаги нет в справочнике.'));
    const { user, form } = await openForm(api);

    await user.selectOptions(form.getByLabelText('Бумага'), 'i-ofz');
    expect(await form.findByText('Бумаги нет в справочнике.')).toBeInTheDocument();

    await user.clear(form.getByLabelText('Время сделки, МСК'));
    await user.type(form.getByLabelText('Время сделки, МСК'), '2026-11-27T11:42');
    await waitFor(() => expect(api.defaults).toHaveBeenLastCalledWith('i-ofz', '2026-11-27'));
    await waitFor(() => expect(form.queryByText('Бумаги нет в справочнике.')).not.toBeInTheDocument());
    // Бумагу не выбрали — подсказку не спрашиваем.
    await user.selectOptions(form.getByLabelText('Бумага'), '');
    expect(api.defaults).toHaveBeenCalledTimes(2);
  });

  it('hides bond fields for a fund share and sends no accrued interest', async () => {
    const { user, api, form } = await openForm();

    await user.selectOptions(form.getByLabelText('Бумага'), 'i-lqdt');
    expect(form.queryByLabelText('НКД на бумагу, ₽')).not.toBeInTheDocument();
    await user.type(form.getByLabelText('Количество, шт.'), '1000');
    await user.type(form.getByLabelText('Цена, ₽ за пай'), '1,5');
    await user.selectOptions(form.getByLabelText('Место'), 'otc');
    await user.type(form.getByLabelText('Контрагент'), 'Банк');
    await user.clear(form.getByLabelText('Комиссия, ₽'));
    await user.type(form.getByLabelText('Комментарий'), 'Пополнение ликвидности');
    await user.click(form.getByRole('button', { name: 'Ввести сделку' }));

    await waitFor(() => expect(api.enter).toHaveBeenCalled());
    const [body] = api.enter.mock.calls[0] as [Record<string, unknown>];
    expect(body).toMatchObject({
      instrument_id: 'i-lqdt',
      face_value: null,
      accrued: null,
      fee: '0',
      venue: 'otc',
      counterparty: 'Банк',
      comment: 'Пополнение ликвидности',
    });
  });

  it('shows the reason the service refused the trade', async () => {
    const api = fakeApi();
    api.enter.mockRejectedValue(refused('Дата расчётов раньше даты заключения.'));
    const { user, form } = await openForm(api);

    await user.selectOptions(form.getByLabelText('Бумага'), 'i-ofz');
    await user.type(form.getByLabelText('Количество, шт.'), '1');
    await user.type(form.getByLabelText('Цена, % от номинала'), '52');
    await user.click(form.getByRole('button', { name: 'Ввести сделку' }));

    expect(await form.findByRole('alert')).toHaveTextContent('Дата расчётов раньше даты заключения.');
  });
});

describe('TradeForm overrides', () => {
  it('sends what the user typed over the hint', async () => {
    const { user, api, form } = await openForm();

    await user.selectOptions(form.getByLabelText('Портфель'), 'p-treasury');
    await user.selectOptions(form.getByLabelText('Бумага'), 'i-ofz');
    await waitFor(() => expect(form.getByLabelText('НКД на бумагу, ₽')).toHaveValue('35.01'));
    fireEvent.change(form.getByLabelText('Дата расчётов'), { target: { value: '2026-12-01' } });
    await user.clear(form.getByLabelText('Номинал на дату расчётов, ₽'));
    await user.type(form.getByLabelText('Номинал на дату расчётов, ₽'), '750');
    await user.clear(form.getByLabelText('НКД на бумагу, ₽'));
    await user.type(form.getByLabelText('НКД на бумагу, ₽'), '35,21');
    await user.type(form.getByLabelText('Количество, шт.'), '10');
    await user.type(form.getByLabelText('Цена, % от номинала'), '99');
    await user.click(form.getByRole('button', { name: 'Ввести сделку' }));

    await waitFor(() => expect(api.enter).toHaveBeenCalled());
    const [body] = api.enter.mock.calls[0] as [Record<string, unknown>];
    expect(body).toMatchObject({
      portfolio_id: 'p-treasury',
      settle_date: '2026-12-01',
      face_value: '750',
      accrued: '35.21',
    });
  });
});

describe('TradeForm edges', () => {
  it('amends a fund share trade without bond fields', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.trade.mockResolvedValue(
      trade({ instrument_id: 'i-lqdt', instrument_kind: 'fund_share', face_value: null, accrued: null }),
    );
    renderBooking(api, '/booking/trades/t-1');

    await user.click(await screen.findByRole('button', { name: 'Исправить' }));
    const form = within(screen.getByRole('form', { name: 'Исправление сделки' }));
    expect(form.queryByLabelText('НКД на бумагу, ₽')).not.toBeInTheDocument();
  });

  it('has no portfolio to offer when only compound portfolios are visible', async () => {
    const api = fakeApi();
    api.portfolios.mockResolvedValue(PORTFOLIOS.filter((p) => p.kind === 'compound'));
    const { form } = await openForm(api);

    expect(within(form.getByLabelText('Портфель')).queryAllByRole('option')).toEqual([]);
  });
});
