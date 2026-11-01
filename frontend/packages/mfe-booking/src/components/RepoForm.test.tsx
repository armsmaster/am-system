import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { fakeApi, renderBooking, repoTrade } from '../test/fixtures';

async function openRepo(api = fakeApi()) {
  const user = userEvent.setup();
  renderBooking(api, '/booking?portfolio=p-bonds&date=2026-11-30');
  await user.click(await screen.findByRole('button', { name: 'Новая сделка' }));
  const form = within(screen.getByRole('form', { name: 'Новая сделка' }));
  await user.selectOptions(form.getByLabelText('Вид сделки'), 'repo');
  return { user, api, form };
}

describe('TradeForm for REPO (design/07)', () => {
  it('enters a reverse REPO with КСУ: only money, the second leg checked against the rate', async () => {
    const { user, api, form } = await openRepo();

    await user.selectOptions(form.getByLabelText('Обеспечение'), 'ksu');
    expect(form.queryByLabelText('Количество, шт.')).not.toBeInTheDocument();
    expect(api.defaults).not.toHaveBeenCalled();
    fireEvent.change(form.getByLabelText('Время сделки, МСК'), { target: { value: '2026-11-27T10:00' } });
    fireEvent.change(form.getByLabelText('Первая часть: дата расчётов'), { target: { value: '2026-11-27' } });
    await user.type(form.getByLabelText('Сумма первой части, ₽'), '30 000 000');
    await user.type(form.getByLabelText('Ставка РЕПО, % годовых'), '14,2');
    fireEvent.change(form.getByLabelText('Вторая часть: дата расчётов'), { target: { value: '2026-11-30' } });
    expect(form.getByText(/По ставке и сроку вторая часть — 30 035 013,70 ₽/)).toBeInTheDocument();
    await user.type(form.getByLabelText('Вторая часть: сумма по отчёту брокера, ₽'), '30035013,70');
    expect(form.queryByLabelText('Место')).not.toBeInTheDocument();
    await user.click(form.getByRole('button', { name: 'Ввести сделку' }));

    await waitFor(() => expect(api.enter).toHaveBeenCalled());
    const [body] = api.enter.mock.calls[0] as [Record<string, unknown>];
    expect(body).toMatchObject({
      kind: 'repo',
      side: 'buy',
      instrument_id: null,
      quantity: 0,
      price: '0',
      amount: '30000000',
      repo_rate: '14.2',
      second_date: '2026-11-30',
      second_amount: '30035013.70',
      venue: 'exchange',
      counterparty: null,
      face_value: null,
      accrued: null,
    });
  });

  it('enters a direct REPO with a bond: the first leg on the trade day, NKD from the service', async () => {
    const { user, api, form } = await openRepo();

    expect(within(form.getByLabelText('Обеспечение')).queryByRole('option', { name: /LQDT/ })).toBeNull();
    fireEvent.change(form.getByLabelText('Время сделки, МСК'), { target: { value: '2026-11-30T11:05' } });
    await user.selectOptions(form.getByLabelText('Обеспечение'), 'i-ofz');
    await waitFor(() => expect(form.getByLabelText('Первая часть: дата расчётов')).toHaveValue('2026-11-30'));
    expect(form.getByLabelText('НКД на бумагу, ₽')).toHaveValue('');
    await user.selectOptions(form.getByLabelText('Вид РЕПО'), 'sell');
    await user.type(form.getByLabelText('Количество, шт.'), '10000');
    await user.type(form.getByLabelText('Цена первой части, % от номинала'), '52,6');
    await user.type(form.getByLabelText('Ставка РЕПО, % годовых'), '14.5');
    fireEvent.change(form.getByLabelText('Вторая часть: дата расчётов'), { target: { value: '2026-12-03' } });
    await user.type(form.getByLabelText('Вторая часть: сумма по отчёту брокера, ₽'), '5616786.01');
    expect(form.getByText(/Сумму первой части сервис посчитает по цене и НКД/)).toBeInTheDocument();
    await user.click(form.getByRole('button', { name: 'Ввести сделку' }));

    await waitFor(() => expect(api.enter).toHaveBeenCalled());
    const [body] = api.enter.mock.calls[0] as [Record<string, unknown>];
    expect(body).toMatchObject({
      kind: 'repo',
      side: 'sell',
      instrument_id: 'i-ofz',
      quantity: 10000,
      price: '52.6',
      settle_date: '2026-11-30',
      accrued: null,
      amount: null,
    });
  });

  it('shows no check while the КСУ amounts are incomplete', async () => {
    const { user, form } = await openRepo();

    await user.selectOptions(form.getByLabelText('Обеспечение'), 'ksu');
    fireEvent.change(form.getByLabelText('Первая часть: дата расчётов'), { target: { value: '2026-11-27' } });
    fireEvent.change(form.getByLabelText('Вторая часть: дата расчётов'), { target: { value: '2026-11-30' } });
    await user.type(form.getByLabelText('Сумма первой части, ₽'), '1000');
    expect(form.queryByText(/По ставке и сроку/)).not.toBeInTheDocument();
    await user.type(form.getByLabelText('Ставка РЕПО, % годовых'), 'abc');
    expect(form.queryByText(/По ставке и сроку/)).not.toBeInTheDocument();
  });

  it('amends a КСУ REPO with its fields filled in', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.trade.mockResolvedValue(
      repoTrade({
        instrument_id: null,
        instrument_kind: 'ksu',
        secid: 'КСУ',
        short_name: 'КСУ',
        side: 'buy',
        quantity: 0,
        price: '0',
        face_value: null,
        accrued: null,
        amount: '30000000.00',
        repo_rate: null,
        second_date: null,
        second_amount: null,
      }),
    );
    renderBooking(api, '/booking/trades/t-repo');

    await user.click(await screen.findByRole('button', { name: 'Исправить' }));
    const form = within(screen.getByRole('form', { name: 'Исправление сделки' }));
    expect(form.getByLabelText('Вид сделки')).toBeDisabled();
    expect(form.getByLabelText('Сумма первой части, ₽')).toHaveValue('30000000.00');
    expect(form.getByLabelText('Ставка РЕПО, % годовых')).toHaveValue('');
  });
});
