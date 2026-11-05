import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { LimitChange } from '../api';
import { fakeApi, PORTFOLIOS, refused, renderBooking, trade, whatIf } from '../test/fixtures';
import { changeText, tradeForCheck } from './LimitsCheck';

async function openForm(api = fakeApi(), seesLimits = true) {
  const user = userEvent.setup();
  renderBooking(api, '/booking?portfolio=p-bonds&date=2026-11-30', false, seesLimits);
  await user.click(await screen.findByRole('button', { name: 'Новая сделка' }));
  return { user, api, form: within(screen.getByRole('form', { name: 'Новая сделка' })) };
}

function panel() {
  return within(screen.getByRole('region', { name: 'Лимиты после сделки' }));
}

describe('LimitsCheck in the trade form', () => {
  it('shows how the trade changes the fund limits before it is entered', async () => {
    const { user, api, form } = await openForm();
    expect(panel().getByText(/Заполните бумагу, количество и цену/)).toBeInTheDocument();

    await user.selectOptions(form.getByLabelText('Бумага'), 'i-ofz');
    await waitFor(() => expect(form.getByLabelText('НКД на бумагу, ₽')).toHaveValue('35.01'));
    await user.type(form.getByLabelText('Количество, шт.'), '15000');
    await user.type(form.getByLabelText('Цена, % от номинала'), '99');

    expect(
      await panel().findByText(
        'Л3, на эмитента, Эмитент А: 9,64% → 10,18% при лимите 10%. После сделки лимит будет нарушен.',
      ),
    ).toHaveClass('status-breach');
    // Сумма — по формуле сервиса сделок: 15 000 × (99% × 1 000 + 35,01).
    expect(api.limitsCheck).toHaveBeenLastCalledWith({
      fund_id: 'p-fund',
      kind: 'outright',
      side: 'buy',
      amount: '15375150.00',
      instrument_id: 'i-ofz',
      secid: 'SU26238RMFS4',
      quantity: 15000,
    });
    expect(panel().getByText(/Проверка — только информация/)).toBeInTheDocument();
    // Проверка сделку не запрещает: кнопка ввода доступна.
    expect(form.getByRole('button', { name: 'Ввести сделку' })).toBeEnabled();
  });

  it('says why there is nothing to show', async () => {
    const api = fakeApi();
    api.limitsCheck
      .mockResolvedValueOnce(whatIf({ limit_set: null, calculated: false, changes: [] }))
      .mockResolvedValueOnce(whatIf({ calculated: false, changes: [] }))
      .mockResolvedValueOnce(whatIf({ changes: [] }))
      .mockRejectedValueOnce(refused('Сервис лимитов недоступен.'));
    const { user, form } = await openForm(api);

    await user.selectOptions(form.getByLabelText('Бумага'), 'i-lqdt');
    await user.type(form.getByLabelText('Количество, шт.'), '1');
    await user.type(form.getByLabelText('Цена, ₽ за пай'), '2');
    expect(await panel().findByText('Набора лимитов у фонда нет — проверять нечем.')).toBeInTheDocument();
    await user.type(form.getByLabelText('Количество, шт.'), '0');
    expect(await panel().findByText(/Расчёта фонда за сегодня ещё нет/)).toBeInTheDocument();
    await user.type(form.getByLabelText('Количество, шт.'), '0');
    expect(await panel().findByText('Лимиты фонда сделка не меняет.')).toBeInTheDocument();
    await user.type(form.getByLabelText('Количество, шт.'), '0');
    expect(await panel().findByText(/Проверка лимитов недоступна: Сервис лимитов недоступен\./)).toBeInTheDocument();
    expect(api.limitsCheck).toHaveBeenLastCalledWith(expect.objectContaining({ amount: '2000.00', quantity: 1000 }));
    // Очистили количество — проверять нечего.
    await user.clear(form.getByLabelText('Количество, шт.'));
    expect(await panel().findByText(/Заполните бумагу/)).toBeInTheDocument();
  });

  it('checks a REPO with КСУ by its first leg', async () => {
    const { user, api, form } = await openForm();
    await user.selectOptions(form.getByLabelText('Вид сделки'), 'repo');
    await user.selectOptions(form.getByLabelText('Обеспечение'), 'ksu');
    await user.selectOptions(form.getByLabelText('Вид РЕПО'), 'sell');
    await user.type(form.getByLabelText('Сумма первой части, ₽'), '300 000 000');

    await waitFor(() =>
      expect(api.limitsCheck).toHaveBeenLastCalledWith({
        fund_id: 'p-fund',
        kind: 'repo',
        side: 'sell',
        amount: '300000000.00',
        instrument_id: null,
        secid: '',
        quantity: 0,
      }),
    );
  });

  it('waits for a portfolio of a fund', async () => {
    const api = fakeApi();
    api.portfolios.mockResolvedValue([
      ...PORTFOLIOS,
      { id: 'p-own', code: 'OWN', name: 'Собственные', kind: 'physical', is_fund: false, parent_ids: [], fund_id: null },
    ]);
    const { user, form } = await openForm(api);
    await user.selectOptions(form.getByLabelText('Портфель'), 'p-own');
    await user.selectOptions(form.getByLabelText('Бумага'), 'i-lqdt');
    await user.type(form.getByLabelText('Количество, шт.'), '1');
    await user.type(form.getByLabelText('Цена, ₽ за пай'), '2');

    expect(panel().getByText(/Заполните бумагу/)).toBeInTheDocument();
    expect(api.limitsCheck).not.toHaveBeenCalled();
  });

  it('is shown only to trading and risk management, and only for a new trade', async () => {
    const { api } = await openForm(fakeApi(), false);
    expect(screen.queryByRole('region', { name: 'Лимиты после сделки' })).not.toBeInTheDocument();
    expect(api.limitsCheck).not.toHaveBeenCalled();
  });
});

describe('tradeForCheck', () => {
  const fields = {
    kind: 'outright' as const,
    side: 'buy' as const,
    fundId: 'p-fund',
    instrument: { id: 'i-ofz', secid: 'SU26238RMFS4', kind: 'bond' as const },
    quantity: '10',
    price: '100',
    faceValue: '1000',
    accrued: '',
    amount: '',
  };

  it('needs a fund, a paper for a purchase and a positive amount', () => {
    expect(tradeForCheck(fields)?.amount).toBe('10000.00');
    expect(tradeForCheck({ ...fields, fundId: null })).toBeNull();
    expect(tradeForCheck({ ...fields, instrument: null })).toBeNull();
    expect(tradeForCheck({ ...fields, quantity: '' })).toBeNull();
    expect(tradeForCheck({ ...fields, price: 'сто' })).toBeNull();
  });
});

describe('changeText', () => {
  const change = (fields: Partial<LimitChange>): LimitChange => ({ ...whatIf().changes[0]!, ...fields });

  it('tells the verdict for every status', () => {
    const metric = { type: 'metric' as const, label: null, group: null, maximum: '4', before: '2.8', after: '3.1' };
    expect(changeText(change({ ...metric, before_status: 'ok', after_status: 'ok' }))).toBe(
      'Л3: 2,80 → 3,10 при лимите 4. В пределах.',
    );
    expect(changeText(change({ ...metric, before_status: 'ok', after_status: 'warning' }))).toBe(
      'Л3: 2,80 → 3,10 при лимите 4. Близко к лимиту.',
    );
    expect(changeText(change({ before_status: 'breach', after_status: 'breach' }))).toMatch(
      /Лимит уже нарушен — сделка увеличит нарушение\.$/,
    );
    expect(changeText(change({ before: '11', after: '10.5', before_status: 'breach', after_status: 'breach' }))).toMatch(
      /Лимит нарушен и после сделки\.$/,
    );
    expect(changeText(change({ before: '10.5', after: '9', before_status: 'breach', after_status: 'ok' }))).toMatch(
      /Сделка устраняет нарушение\.$/,
    );
    // Значения ещё не было — считаем от нуля.
    expect(changeText(change({ before: null, maximum: '0', before_status: 'breach', after_status: 'breach' }))).toMatch(
      /увеличит нарушение\.$/,
    );
    expect(changeText(change({ after: null, maximum: '0', before_status: 'breach', after_status: 'breach' }))).toMatch(
      /и после сделки\.$/,
    );
    // Минимум: хуже — когда значение падает.
    const floor = { ...metric, maximum: null, minimum: '1', before: '0.9', after: '0.8' };
    expect(changeText(change({ ...floor, before_status: 'breach', after_status: 'breach' }))).toBe(
      'Л3: 0,90 → 0,80 при минимуме 1. Лимит уже нарушен — сделка увеличит нарушение.',
    );
    expect(changeText(change({ ...metric, maximum: null, after: null, after_status: 'no_data', reasons: ['нет дюрации'] }))).toBe(
      'Л3: 2,80 → —. Нет данных для проверки (нет дюрации).',
    );
    expect(changeText(change({ ...metric, maximum: null, before: '-1', after: '1' }))).toBe(
      'Л3: −1,00 → 1,00. После сделки лимит будет нарушен.',
    );
  });

  it('lists the new violations of a rule check', () => {
    const rule = {
      code: '4',
      type: 'eligible' as const,
      label: null,
      group: null,
      before_status: 'breach' as const,
      after_status: 'breach' as const,
      reasons: ['короткая позиция: правило «ОФЗ» её не разрешает'],
    };
    expect(changeText(change(rule))).toBe(
      '4. Лимит уже нарушен — сделка увеличит нарушение (короткая позиция: правило «ОФЗ» её не разрешает).',
    );
  });
});

describe('LimitsCheck for an amendment', () => {
  it('is not shown: the trade is already in the positions', async () => {
    const api = fakeApi();
    api.trade.mockResolvedValue(trade());
    const user = userEvent.setup();
    renderBooking(api, '/booking/trades/t-1', false, true);
    await user.click(await screen.findByRole('button', { name: 'Исправить' }));

    expect(await screen.findByRole('form', { name: 'Исправление сделки' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Лимиты после сделки' })).not.toBeInTheDocument();
  });
});
