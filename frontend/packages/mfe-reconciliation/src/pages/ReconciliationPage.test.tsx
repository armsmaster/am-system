import { ApiError } from '@am/platform';
import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import {
  balance,
  bank,
  broker,
  coupon,
  fakeApi,
  fundDay,
  PORTFOLIOS,
  purchase,
  renderReconciliation,
} from '../test/fixtures';

async function openDay(api = fakeApi(), isOperations = true, isAdmin = false) {
  renderReconciliation(api, '/reconciliation', isOperations, isAdmin);
  fireEvent.change(await screen.findByLabelText('День'), { target: { value: '2026-12-02' } });
  await screen.findByText(/OPIF1, 02.12.2026/);
  return api;
}

describe('ReconciliationPage', () => {
  it('shows a coupon the broker credits a day later', async () => {
    const api = await openDay();

    expect(api.day).toHaveBeenLastCalledWith('p-fund', '2026-12-02');
    expect(screen.getByText(/Не все счета сверены/)).toBeInTheDocument();
    const account = screen.getByRole('region', { name: 'OPIF1-BROKER' });
    expect(account).toHaveTextContent('Счёт брокерский, Брокер Х. Портфели: OPIF1-BONDS.');
    expect(account).toHaveTextContent('Статус: есть расхождения.');
    const rows = within(screen.getByRole('table', { name: 'Движения OPIF1-BROKER' })).getAllByRole('row');
    expect(rows[1]).toHaveTextContent('купон SU26238RMFS4885 000,00—−885 000,00нет у брокераПринять');
    const balances = within(screen.getByRole('table', { name: 'Остаток OPIF1-BROKER' })).getAllByRole('row');
    expect(balances[1]).toHaveTextContent('на начало дня46 026 949,9546 026 949,950,00сходится');
    expect(balances[2]).toHaveTextContent('на конец дня46 911 949,9546 026 949,95−885 000,00расходится');
    // Сделок в этот день нет — таблицы сделок тоже.
    expect(screen.queryByRole('table', { name: 'Сделки OPIF1-BROKER' })).not.toBeInTheDocument();
  });

  it('accepts a discrepancy with a comment', async () => {
    const api = await openDay();
    const account = screen.getByRole('region', { name: 'OPIF1-BROKER' });

    await userEvent.click(within(account).getAllByRole('button', { name: 'Принять' })[0]!);
    const form = screen.getByRole('form', { name: 'Принять расхождение' });
    const submit = within(form).getByRole('button', { name: 'Принять расхождение' });
    expect(submit).toBeDisabled();
    await userEvent.type(within(form).getByLabelText(/Почему/), 'Купон ОФЗ 26238 брокер зачислит 03.12 ');
    await userEvent.click(submit);

    expect(api.accept).toHaveBeenCalledWith({
      account_id: 'a-broker',
      date: '2026-12-02',
      line: 'movement:coupon::i-ofz',
      comment: 'Купон ОФЗ 26238 брокер зачислит 03.12',
    });
    expect(screen.queryByRole('form', { name: 'Принять расхождение' })).not.toBeInTheDocument();
    expect(api.day).toHaveBeenCalledTimes(3);
  });

  it('keeps the form when the service refuses and can be cancelled', async () => {
    const api = fakeApi();
    api.accept.mockRejectedValue(
      new ApiError(422, { detail: { code: 'nothing_to_accept', message: 'Строка сходится или уже принята.' } }),
    );
    await openDay(api);

    await userEvent.click(screen.getAllByRole('button', { name: 'Принять' })[0]!);
    await userEvent.type(screen.getByLabelText(/Почему/), 'Проверили');
    await userEvent.click(screen.getByRole('button', { name: 'Принять расхождение' }));

    expect(await screen.findByText('Строка сходится или уже принята.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Отмена' }));
    expect(screen.queryByRole('form', { name: 'Принять расхождение' })).not.toBeInTheDocument();
  });

  it('enters «no movements» for a bank day', async () => {
    const api = await openDay();
    const account = screen.getByRole('region', { name: 'OPIF1-BANK' });

    expect(account).toHaveTextContent('Счёт банковский, Банк Y.');
    expect(account).toHaveTextContent('Статус: нет выписки за день.');
    await userEvent.click(
      within(account).getByRole('button', {
        name: 'Движений нет: остаток 40 000 000,00 ₽, как в выписке за 01.12.2026',
      }),
    );

    expect(api.enter).toHaveBeenCalledWith({
      account_id: 'a-bank',
      date: '2026-12-02',
      opening_cash: '40000000.00',
      closing_cash: '40000000.00',
      movements: [],
    });
  });

  it('shows trades, confirms the matched ones', async () => {
    const api = fakeApi();
    const matched = purchase({ status: 'matched', differences: [] });
    api.day.mockResolvedValue(
      fundDay({
        reconciled: true,
        accounts: [
          broker({
            status: 'reconciled',
            trades: [
              matched,
              purchase({ line: 'trade:2', status: 'matched', differences: [], system: { ...matched.system!, status: 'confirmed', trade_id: 't-2' } }),
              purchase({ line: 'trade:3' }),
            ],
            movements: [],
            closing: balance({ line: 'balance:closing' }),
          }),
        ],
      }),
    );
    await openDay(api);

    expect(screen.getByText(/Все счета фонда сверены — день можно закрыть/)).toBeInTheDocument();
    const rows = within(screen.getByRole('table', { name: 'Сделки OPIF1-BROKER' })).getAllByRole('row');
    expect(rows[1]).toHaveTextContent(
      '1000000001: покупка SU26238RMFS450 000 шт., 28 000 500,00 ₽, комиссия 0,00 ₽50 000 шт., 28 000 500,00 ₽, комиссия 2 800,05 ₽сходится',
    );
    expect(rows[3]).toHaveTextContent('комиссия: 0,00 в системе, 2 800,05 в выписке');
    expect(screen.getByText('Движений нет ни в системе, ни в выписке.')).toBeInTheDocument();
    // Подтверждённую сделку повторно не подтверждают, несошедшуюся — тоже.
    await userEvent.click(screen.getByRole('button', { name: 'Подтвердить сошедшиеся сделки: 1' }));
    expect(api.confirm).toHaveBeenCalledExactlyOnceWith('t-buy', 'Сошлась с выпиской за 02.12.2026');
  });

  it('names trades from either side and shows what was accepted or paired', async () => {
    const api = fakeApi();
    const only = purchase();
    api.day.mockResolvedValue(
      fundDay({
        accounts: [
          broker({
            trades: [
              purchase({ status: 'not_in_system', system: null, differences: [] }),
              purchase({
                line: 'trade:id:t-9',
                status: 'not_in_statement',
                statement: null,
                differences: [],
                system: { ...only.system!, external_id: null, kind: 'repo', side: 'sell', secid: 'LQDT' },
              }),
              purchase({ line: 'trade:4', status: 'accepted', differences: [], acceptance: { comment: 'Брокер округлил', accepted_by: 'ops', accepted_at: '2026-12-03T09:00:00Z' } }),
            ],
            movements: [
              coupon({ status: 'paired', paired: '2026-12-01', statement: '885000.00', system: null, discrepancy: '885000.00' }),
              coupon({ line: 'm-2', kind: 'settlement', secid: null, trade: 'id:t-9', status: 'matched', comments: ['Пайщик'] }),
              coupon({ line: 'm-3', kind: 'fee', secid: null, trade: '1000000001', status: 'matched' }),
              coupon({ line: 'm-4', trade: '2000000001', status: 'matched' }),
            ],
            closing: balance({ line: 'balance:closing', status: 'explained', expected: '-885000.00', discrepancy: '-885000.00' }),
            waiting: [
              { date: '2026-12-02', line: 'movement:coupon::i-ofz', kind: 'coupon', secid: 'SU26238RMFS4', trade: null, discrepancy: '-885000.00', comment: 'Брокер зачислит 03.12' },
              { date: '2026-12-02', line: 'movement:other::', kind: 'other', secid: null, trade: null, discrepancy: '-1.00', comment: 'Разберёмся' },
            ],
          }),
        ],
      }),
    );
    await openDay(api);

    const trades = within(screen.getByRole('table', { name: 'Сделки OPIF1-BROKER' })).getAllByRole('row');
    expect(trades[1]).toHaveTextContent('1000000001: покупка SU26238RMFS4—50 000 шт.');
    expect(trades[1]).toHaveTextContent('нет в системе');
    expect(trades[2]).toHaveTextContent('без номера: РЕПО, продажа LQDT50 000 шт., 28 000 500,00 ₽, комиссия 0,00 ₽—нет у брокера');
    expect(trades[3]).toHaveTextContent('принятоБрокер округлил (ops)');
    const moves = within(screen.getByRole('table', { name: 'Движения OPIF1-BROKER' })).getAllByRole('row');
    expect(moves[1]).toHaveTextContent('сошлось с принятой строкой 01.12.2026');
    expect(moves[2]).toHaveTextContent('расчёты по сделке без номераПайщик');
    expect(moves[3]).toHaveTextContent('комиссия по сделке 1000000001');
    expect(moves[4]).toHaveTextContent('купон SU26238RMFS4 по сделке РЕПО 2000000001');
    const balances = within(screen.getByRole('table', { name: 'Остаток OPIF1-BROKER' })).getAllByRole('row');
    expect(balances[2]).toHaveTextContent('объясняют принятые расхождения: −885 000,00 ₽');
    expect(screen.getByText(/с 02.12.2026: купон SU26238RMFS4 −885 000,00 ₽ — Брокер зачислит 03.12/)).toBeInTheDocument();
    expect(screen.getByText(/с 02.12.2026: прочее −1,00 ₽ — Разберёмся/)).toBeInTheDocument();
  });

  it('tells about a weekend, a recalculation and a bank discrepancy', async () => {
    const api = fakeApi();
    api.day.mockResolvedValue(
      fundDay({
        trading: false,
        reconciled: true,
        accounts: [
          bank({ status: 'recalculating', waiting_since: '2026-11-30', previous: null }),
          bank({
            account_id: 'a-bank-2',
            code: 'OPIF1-BANK2',
            status: 'discrepancies',
            statement: { ...broker().statement! },
            movements: [coupon({ kind: 'unit_settlement', secid: null, status: 'not_in_statement' })],
            opening: balance(),
            closing: balance({ line: 'balance:closing' }),
          }),
        ],
      }),
    );
    await openDay(api);

    expect(screen.getByText(/Неторговый день: выписок нет, сверять нечего/)).toBeInTheDocument();
    expect(screen.getByText('Фонд ждёт пересчёта с 30.11.2026.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Движений нет/ })).not.toBeInTheDocument();
    const moves = within(screen.getByRole('table', { name: 'Движения OPIF1-BANK2' })).getAllByRole('row');
    expect(moves[1]).toHaveTextContent('расчёт по паям885 000,00—−885 000,00нет в банке');
  });

  it('shows the trader no buttons', async () => {
    await openDay(fakeApi(), false);

    expect(screen.queryAllByRole('button')).toEqual([]);
  });

  it('switches funds', async () => {
    const api = fakeApi();
    api.portfolios.mockResolvedValue([...PORTFOLIOS, { id: 'p-fund-2', code: 'OPIF2', name: 'ОПИФ 2', is_fund: true }]);
    await openDay(api);

    await userEvent.selectOptions(screen.getByLabelText('Фонд'), 'p-fund-2');
    expect(api.day).toHaveBeenLastCalledWith('p-fund-2', '2026-12-02');
  });

  it('says when there is no fund to reconcile', async () => {
    const api = fakeApi();
    api.portfolios.mockResolvedValue([PORTFOLIOS[1]!]);
    renderReconciliation(api, '/reconciliation');

    expect(await screen.findByText('Нет фондов, к которым у вас есть доступ.')).toBeInTheDocument();
  });
  it('closes the next day when every account is reconciled', async () => {
    const api = fakeApi();
    api.day.mockResolvedValue(fundDay({ reconciled: true, accounts: [] }));
    await openDay(api);

    expect(screen.getByText('Фонд закрыт по 01.12.2026. Следующий к закрытию — 02.12.2026.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Закрыть 02.12.2026' }));
    expect(api.close).toHaveBeenCalledWith('p-fund', '2026-12-02');
    expect(api.day).toHaveBeenCalledTimes(3);
  });

  it('does not close a day with discrepancies or out of order', async () => {
    const api = await openDay();
    // Счета не сверены — закрыть нельзя.
    expect(screen.getByRole('button', { name: 'Закрыть 02.12.2026' })).toBeDisabled();

    api.day.mockResolvedValue(fundDay({ date: '2026-12-03', reconciled: true, accounts: [] }));
    fireEvent.change(screen.getByLabelText('День'), { target: { value: '2026-12-03' } });
    // Дни закрываются подряд: сначала 02.12.
    expect(await screen.findByText(/OPIF1, 03.12.2026/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Закрыть/ })).not.toBeInTheDocument();
  });

  it('says when no day is closed', async () => {
    const api = fakeApi();
    api.day.mockResolvedValue(fundDay({ closed_through: null, next_day: null, accounts: [] }));
    await openDay(api);

    expect(screen.getByText('Закрытых дней нет.')).toBeInTheDocument();
  });

  it('lets the admin reopen a closed day that no longer matches', async () => {
    const api = fakeApi();
    api.day.mockResolvedValue(fundDay({ closed: true, closed_through: '2026-12-03' }));
    api.reopen.mockRejectedValueOnce(
      new ApiError(422, { detail: { code: 'day_not_closed', message: '02.12.2026 не закрыт.' } }),
    );
    await openDay(api, true, true);

    expect(screen.getByText('02.12.2026 закрыт. Фонд закрыт по 03.12.2026.')).toBeInTheDocument();
    expect(screen.getByText(/Закрытый день не сходится с выписками/)).toBeInTheDocument();
    const form = screen.getByRole('form', { name: 'Открыть день заново' });
    const submit = within(form).getByRole('button', { name: 'Открыть заново с 02.12.2026' });
    expect(submit).toBeDisabled();
    await userEvent.type(within(form).getByLabelText(/Почему/), 'Брокер исправил отчёт ');
    await userEvent.click(submit);
    expect(await screen.findByText('02.12.2026 не закрыт.')).toBeInTheDocument();
    await userEvent.click(submit);
    expect(api.reopen).toHaveBeenLastCalledWith('p-fund', '2026-12-02', 'Брокер исправил отчёт');
  });

  it('shows a closed reconciled day to operations without a reopen form', async () => {
    const api = fakeApi();
    api.day.mockResolvedValue(fundDay({ closed: true, reconciled: true, closed_through: '2026-12-02', accounts: [] }));
    await openDay(api);

    expect(screen.getByText('02.12.2026 закрыт. Фонд закрыт по 02.12.2026.')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByRole('form', { name: 'Открыть день заново' })).not.toBeInTheDocument();
  });
});
