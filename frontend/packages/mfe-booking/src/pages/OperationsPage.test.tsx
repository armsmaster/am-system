import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Operation } from '../api';
import { fakeApi, nav, operation, PORTFOLIOS, refused, renderBooking } from '../test/fixtures';

const PAGE = '/booking/operations?portfolio=p-fund';

const expense = (fields: Partial<Operation> = {}) =>
  operation({
    id: 'o-exp',
    kind: 'expense_accrual',
    expense_kind: 'auditor',
    units: null,
    amount: '120000.00',
    planned_date: null,
    op_date: '2026-11-25',
    ...fields,
  });

const fee = operation({
  id: 'o-fee',
  kind: 'fee_payment',
  fee_kind: 'management',
  period_end: '2026-10-31',
  units: null,
  amount: '20550.00',
  planned_date: null,
  op_date: '2026-11-20',
});

const transferOp = operation({
  id: 'o-sec',
  kind: 'security_transfer',
  portfolio_id: 'p-bonds',
  portfolio_code: 'OPIF1-BONDS',
  receiver_id: 'p-treasury',
  receiver_code: 'OPIF1-TREASURY',
  units: null,
  amount: null,
  planned_date: null,
  instrument_id: 'i-ofz',
  secid: 'SU26238RMFS4',
  short_name: 'ОФЗ 26238',
  quantity: 10000,
  op_date: '2026-11-24',
});

function rows(): HTMLElement[] {
  return within(screen.getByRole('table', { name: 'Операции' })).getAllByRole('row').slice(1);
}

async function openNew(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Новая операция' }));
  return within(screen.getByRole('form', { name: 'Новая операция' }));
}

describe('OperationsPage (decision 175)', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-12-03T09:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('lists operations by day with totals; trading only reads', async () => {
    const api = fakeApi();
    api.operations.mockResolvedValue([
      operation(),
      operation({ id: 'o-2', kind: 'unit_redemption', units: '5000.00000', amount: '5009500.00', settled_date: '2026-11-27' }),
      operation({ id: 'o-3', status: 'cancelled', comment: 'Заявка отозвана' }),
      expense(),
      fee,
      transferOp,
      operation({ id: 'o-old', op_date: '2026-09-01', planned_date: '2026-09-02', settled_date: '2026-09-02' }),
    ]);
    renderBooking(api, PAGE);

    await screen.findByRole('table', { name: 'Операции' });
    expect(api.operations).toHaveBeenCalledWith('p-fund');
    expect(within(screen.getByLabelText('Итоги периода')).getAllByRole('definition').map((d) => d.textContent)).toEqual([
      '100,0 млн ₽',
      '5,0 млн ₽',
      '20 550 ₽',
      '1100,0 млн ₽',
    ]);
    const table = rows().map((r) => r.textContent);
    expect(table[0]).toMatch(/чт, 26 ноября/);
    expect(table[1]).toBe('выдача паёвOPIF1-TREASURY100 000100 000 000,00расчёт просрочен: план 27.11.2026'.replace('выдача паёвOPIF1', 'выдача паёв1 000,00 ₽ за пайOPIF1'));
    expect(table[2]).toContain('погашение паёв');
    expect(table[2]).toContain('расчёт 27.11.2026');
    expect(table[3]).toContain('отменена');
    expect(table[3]).toContain('Заявка отозвана');
    expect(table.join('|')).toContain('начисление расходааудитор');
    expect(table.join('|')).toContain('вознаграждение УК по 31.10.2026');
    expect(table.join('|')).toContain('перевод бумагSU26238RMFS4OPIF1-BONDS → OPIF1-TREASURY10 000—');
    // Операция сентября — вне месяца.
    expect(table.join('|')).not.toContain('01.09');
    expect(screen.queryByRole('button', { name: 'Новая операция' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Подтвердить расчёт…' })).not.toBeInTheDocument();
  });

  it('filters by kind and shows every pending settlement', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.operations.mockResolvedValue([
      operation(),
      operation({ id: 'o-sep', op_date: '2026-09-01', planned_date: '2026-09-02' }),
      expense(),
      operation({ id: 'o-repo', kind: 'repo_contribution', units: null, planned_date: null, direction: 'in', amount: '1000.00' }),
    ]);
    renderBooking(api, PAGE);
    await screen.findByRole('table', { name: 'Операции' });
    const chips = within(screen.getByRole('group', { name: 'Вид операции' }));
    expect(chips.getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Все 3',
      'Паи 1',
      'Расходы 1',
      'Вознаграждение 0',
      'Переводы 0',
      'Взносы РЕПО 1',
      'Ждут расчёта 2',
    ]);

    await user.click(chips.getByRole('button', { name: /Расходы/ }));
    expect(rows().map((r) => r.textContent).join('|')).toContain('начисление расхода');
    expect(rows()).toHaveLength(2);
    await user.click(chips.getByRole('button', { name: /Ждут расчёта/ }));
    // Сентябрьская выдача — вне периода, но расчёт по ней ждёт: она в списке.
    expect(rows().filter((r) => !r.classList.contains('day-row'))).toHaveLength(2);
    expect(screen.getByLabelText('Итоги периода')).toHaveTextContent('Ждут расчёта2');
    await user.click(chips.getByRole('button', { name: /Взносы РЕПО/ }));
    expect(rows().map((r) => r.textContent).join('|')).toContain('НКЦ — фонду');
    await user.click(chips.getByRole('button', { name: /Вознаграждение/ }));
    expect(screen.getByText('Таких операций за этот период нет.')).toBeInTheDocument();
  });

  it('changes the period and the portfolio', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.operations.mockResolvedValue([operation({ op_date: '2026-09-15', settled_date: '2026-09-16' })]);
    renderBooking(api, PAGE);

    expect(await screen.findByText('Операций за этот период нет.')).toBeInTheDocument();
    const periods = within(screen.getByRole('group', { name: 'Период' }));
    await user.click(periods.getByRole('button', { name: 'Квартал' }));
    expect(rows()).toHaveLength(2);
    await user.click(periods.getByRole('button', { name: 'Год' }));
    expect(rows()).toHaveLength(2);
    await user.click(periods.getByRole('button', { name: 'Период' }));
    fireEvent.change(screen.getByLabelText('С даты'), { target: { value: '2026-09-16' } });
    expect(screen.getByText('Операций за этот период нет.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('По дату'), { target: { value: '2026-09-20' } });
    fireEvent.change(screen.getByLabelText('С даты'), { target: { value: '' } });
    expect(screen.getByLabelText('С даты')).toHaveValue('2026-09-16');

    await user.selectOptions(screen.getByRole('combobox', { name: 'Портфель' }), 'p-bonds');
    expect(api.operations).toHaveBeenLastCalledWith('p-bonds');
  });

  it('issues units with hints and remembers what was saved', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.enterOperation.mockResolvedValue(operation({ warnings: ['Проверьте сумму'] }));
    renderBooking(api, PAGE, true);
    const form = await openNew(user);
    const field = (name: string) => form.getByLabelText(name);

    expect(field('Фонд')).toHaveValue('p-fund');
    await user.selectOptions(field('Фонд'), 'p-fund');
    expect(form.getByRole('radio', { name: /Паи/ })).toBeChecked();
    expect(form.getByRole('radio', { name: 'Выдача' })).toBeChecked();
    fireEvent.change(field('Дата операции'), { target: { value: '2026-11-30' } });
    fireEvent.change(field('Плановая дата расчёта'), { target: { value: '2026-12-01' } });
    await waitFor(() => expect(api.nav).toHaveBeenLastCalledWith('p-fund', '2026-11-20', '2026-11-30'));
    const hints = within(form.getByLabelText('Подсказки'));
    expect(await hints.findByText(/Наша оценка на 30.11.2026/)).toHaveTextContent('1 000,45 ₽ за пай');
    await user.type(field('Паи, шт.'), '20 000');
    await user.type(field('Сумма, ₽'), '20009001');
    // Расхождение меньше сотой процента — без знака.
    expect(hints.getByText(/По операции/)).toHaveTextContent('1 000,45 ₽ за пай (0%)');
    expect(hints.getByText(/Паёв станет/)).toHaveTextContent('120 000');
    await user.clear(field('Сумма, ₽'));
    await user.type(field('Сумма, ₽'), '200090000');
    expect(hints.getByText(/По операции/)).toHaveClass('hint-warn');
    expect(hints.getByText(/По операции/)).toHaveTextContent('(+900%)');
    await user.click(form.getByRole('radio', { name: 'Погашение' }));
    await user.clear(field('Сумма, ₽'));
    await user.type(field('Сумма, ₽'), '10000000');
    expect(hints.getByText(/По операции/)).toHaveTextContent('(−50,02%)');
    expect(hints.getByText(/Паёв станет/)).toHaveTextContent('80 000');
    await user.click(form.getByRole('radio', { name: 'Выдача' }));
    await user.type(field('Комментарий'), 'Заявка 17');
    await user.click(form.getByRole('button', { name: 'Провести выдачу' }));

    expect(api.enterOperation).toHaveBeenCalledWith({
      kind: 'unit_issue',
      fund_id: 'p-fund',
      op_date: '2026-11-30',
      units: '20000',
      amount: '10000000',
      planned_date: '2026-12-01',
      comment: 'Заявка 17',
    });
    expect(await screen.findByText('Проведено: выдача паёв от 26.11.2026.')).toBeInTheDocument();
    expect(screen.getByText('Проверьте сумму')).toBeInTheDocument();
    expect(api.operations).toHaveBeenCalledTimes(2);
  });

  it('says when there is nothing to compare the unit price with', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.nav.mockResolvedValueOnce(nav({ days: [] })).mockRejectedValueOnce(refused('нет'));
    renderBooking(api, PAGE, true);
    const form = await openNew(user);

    expect(await form.findByText(/Оценки фонда на эту дату нет/)).toBeInTheDocument();
    fireEvent.change(form.getByLabelText('Дата операции'), { target: { value: '2026-11-29' } });
    expect(form.getByText('Сверяем с оценкой фонда…')).toBeInTheDocument();
    expect(await form.findByText(/Оценки фонда на эту дату нет/)).toBeInTheDocument();
  });

  it.each([
    [
      'Расходы',
      'Выплата',
      async (form: ReturnType<typeof within>, user: ReturnType<typeof userEvent.setup>) => {
        await user.selectOptions(form.getByLabelText('Вид расхода'), 'auditor');
        await user.type(form.getByLabelText('Сумма, ₽'), '150000');
        expect(form.getByText(/Начислено и не выплачено \(аудитор\): 120 000,00 ₽/)).toBeInTheDocument();
        expect(form.getByText(/Выплата больше начисленного на 30 000,00 ₽/)).toHaveClass('hint-warn');
      },
      'Провести выплату',
      { kind: 'expense_payment', fund_id: 'p-fund', portfolio_id: null, expense_kind: 'auditor', amount: '150000' },
    ],
    [
      'Расходы',
      'Начисление',
      async (form: ReturnType<typeof within>, user: ReturnType<typeof userEvent.setup>) => {
        await user.selectOptions(form.getByLabelText('С чьего счёта платят'), 'p-bonds');
        await user.selectOptions(form.getByLabelText('Вид расхода'), 'exchange_broker');
        await user.type(form.getByLabelText('Сумма, ₽'), '1500');
        expect(form.queryByLabelText('Подсказки')).not.toBeInTheDocument();
      },
      'Начислить расход',
      { kind: 'expense_accrual', fund_id: 'p-fund', portfolio_id: 'p-bonds', expense_kind: 'exchange_broker', amount: '1500' },
    ],
    [
      'Вознаграждение',
      null,
      async (form: ReturnType<typeof within>, user: ReturnType<typeof userEvent.setup>) => {
        await user.selectOptions(form.getByLabelText('Вид комиссии'), 'specdep');
        expect(await form.findByText(/\(спецдепозитарий\) по 30.11.2026: 0,00 ₽/)).toBeInTheDocument();
        await user.selectOptions(form.getByLabelText('Вид комиссии'), 'management');
        fireEvent.change(form.getByLabelText('Оплачено по'), { target: { value: '2026-10-31' } });
        expect(form.getByText(/по 31.10.2026: 0,00 ₽ на 30.11.2026/)).toBeInTheDocument();
        fireEvent.change(form.getByLabelText('Оплачено по'), { target: { value: '2026-11-30' } });
        expect(form.getByText(/Начислено и не выплачено \(вознаграждение УК\) по 30.11.2026: 20 552,06 ₽ на 30.11.2026/)).toBeInTheDocument();
        await user.type(form.getByLabelText('Сумма, ₽'), '20552,06');
        expect(form.getByText('Сумма совпадает с начисленным.')).toBeInTheDocument();
        await user.clear(form.getByLabelText('Сумма, ₽'));
        await user.type(form.getByLabelText('Сумма, ₽'), '21000');
        expect(form.getByText(/Разница 447,94 ₽ станет корректировкой/)).toHaveClass('hint-warn');
      },
      'Провести выплату',
      { kind: 'fee_payment', fund_id: 'p-fund', fee_kind: 'management', period_end: '2026-11-30', amount: '21000' },
    ],
    [
      'Переводы',
      'Деньги',
      async (form: ReturnType<typeof within>, user: ReturnType<typeof userEvent.setup>) => {
        await user.selectOptions(form.getByLabelText('Откуда'), 'p-treasury');
        await user.selectOptions(form.getByLabelText('Куда'), 'p-bonds');
        await user.type(form.getByLabelText('Сумма, ₽'), '60000000');
      },
      'Провести перевод',
      { kind: 'cash_transfer', portfolio_id: 'p-treasury', receiver_id: 'p-bonds', amount: '60000000' },
    ],
    [
      'Переводы',
      'Бумаги',
      async (form: ReturnType<typeof within>, user: ReturnType<typeof userEvent.setup>) => {
        await user.selectOptions(form.getByLabelText('Откуда'), 'p-bonds');
        await user.selectOptions(form.getByLabelText('Куда'), 'p-treasury');
        await user.selectOptions(form.getByLabelText('Бумага'), 'i-ofz');
        await user.type(form.getByLabelText('Количество, шт.'), '10000');
      },
      'Провести перевод',
      { kind: 'security_transfer', portfolio_id: 'p-bonds', receiver_id: 'p-treasury', instrument_id: 'i-ofz', quantity: 10000 },
    ],
  ] as const)('enters %s: %s', async (group, kind, fill, submit, expected) => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.operations.mockResolvedValue([operation(), expense()]);
    renderBooking(api, PAGE, true);
    const form = await openNew(user);

    await user.click(form.getByRole('radio', { name: new RegExp(group) }));
    if (kind) {
      await user.click(form.getByRole('radio', { name: kind }));
    }
    await fill(form, user);
    await user.click(form.getByRole('button', { name: submit }));

    expect(api.enterOperation).toHaveBeenCalledWith(expect.objectContaining({ ...expected, comment: null }));
  });

  it('shows a refusal and closes the form on cancel', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.enterOperation.mockRejectedValue(refused('Переводят только внутри фонда.'));
    renderBooking(api, PAGE, true);
    const form = await openNew(user);

    await user.type(form.getByLabelText('Паи, шт.'), '1');
    await user.type(form.getByLabelText('Сумма, ₽'), '1');
    await user.click(form.getByRole('button', { name: 'Провести выдачу' }));
    expect(await form.findByRole('alert')).toHaveTextContent('Переводят только внутри фонда.');
    await user.click(form.getByRole('button', { name: 'Отмена' }));
    expect(screen.queryByRole('form', { name: 'Новая операция' })).not.toBeInTheDocument();
  });

  it.each([
    ['without a fund', PORTFOLIOS.filter((p) => p.id === 'p-treasury').map((p) => ({ ...p, fund_id: null }))],
    ['without physical portfolios', PORTFOLIOS.filter((p) => p.is_fund)],
  ])('opens the form %s', async (_name, portfolios) => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.portfolios.mockResolvedValue(portfolios);
    renderBooking(api, '/booking/operations', true);
    const form = await openNew(user);

    await user.click(form.getByRole('radio', { name: /Переводы/ }));
    expect(form.getByLabelText('Куда')).toHaveValue('');
  });

  it('opens an operation, amends it with a reason, settles and cancels it', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.operation.mockResolvedValue(
      operation({
        number: 2,
        comment: 'Сумма по реестру',
        versions: [
          { number: 1, status: 'active', author: 'ops', created_at: '2026-11-26T09:00:00Z', comment: null, changed: [] },
          { number: 2, status: 'active', author: 'ops', created_at: '2026-11-26T10:00:00Z', comment: 'Сумма по реестру', changed: ['amount'] },
        ],
      }),
    );
    renderBooking(api, PAGE, true);

    await user.click((await screen.findAllByText('выдача паёв'))[0]!);
    const card = within(screen.getByRole('dialog', { name: 'Операция' }));
    expect(await card.findByText('версия 2')).toBeInTheDocument();
    expect(api.operation).toHaveBeenCalledWith('o-1');
    expect(card.getByText('1 000,00 ₽')).toBeInTheDocument();
    const versions = within(card.getByRole('table', { name: 'Версии операции' })).getAllByRole('row');
    expect(versions[1]).toHaveTextContent('сумма');
    expect(versions[1]).toHaveTextContent('Сумма по реестру');
    expect(versions[2]).toHaveTextContent('проведена: выдача паёв');

    await user.click(card.getByRole('button', { name: 'Исправить' }));
    const amend = within(card.getByRole('form', { name: 'Исправление операции' }));
    expect(amend.getByLabelText('Фонд')).toBeDisabled();
    expect(amend.getByLabelText('Паи, шт.')).toHaveValue('100000');
    expect(amend.getByLabelText('Сумма, ₽')).toHaveValue('100000000');
    expect(amend.queryByRole('radio', { name: /Паи/ })).not.toBeInTheDocument();
    await user.clear(amend.getByLabelText('Сумма, ₽'));
    await user.type(amend.getByLabelText('Сумма, ₽'), '100500000');
    // Исправляемая выдача уже в паях фонда: паёв станет столько же, а не вдвое больше.
    expect(await amend.findByText(/Паёв станет/)).toHaveTextContent('Паёв станет 100 000.');
    await user.type(amend.getByLabelText('Почему исправляете'), 'Сумма по реестру');
    await user.click(amend.getByRole('button', { name: 'Сохранить исправление' }));
    expect(api.amendOperation).toHaveBeenCalledWith('o-1', expect.objectContaining({ amount: '100500000', comment: 'Сумма по реестру' }));
    await waitFor(() => expect(api.operation).toHaveBeenCalledTimes(2));

    await user.click(card.getByRole('button', { name: 'Подтвердить расчёт' }));
    const settle = within(card.getByRole('form', { name: 'Подтверждение расчёта' }));
    expect(settle.getByLabelText('Дата расчёта')).toHaveValue('2026-11-27');
    await user.click(settle.getByRole('button', { name: 'Назад' }));
    await user.click(card.getByRole('button', { name: 'Подтвердить расчёт' }));
    const again = within(card.getByRole('form', { name: 'Подтверждение расчёта' }));
    fireEvent.change(again.getByLabelText('Дата расчёта'), { target: { value: '2026-11-30' } });
    await user.click(again.getByRole('button', { name: 'Подтвердить расчёт' }));
    expect(api.settleOperation).toHaveBeenCalledWith('o-1', '2026-11-30');

    api.cancelOperation.mockRejectedValueOnce(refused('День закрыт.'));
    await user.click(await card.findByRole('button', { name: 'Отменить операцию' }));
    const cancel = within(card.getByRole('form', { name: 'Отмена операции' }));
    await user.type(cancel.getByLabelText('Причина отмены'), ' Ошибка ввода ');
    await user.click(cancel.getByRole('button', { name: 'Отменить операцию' }));
    expect(api.cancelOperation).toHaveBeenCalledWith('o-1', 'Ошибка ввода');
    expect(await cancel.findByRole('alert')).toHaveTextContent('День закрыт.');
    await user.click(cancel.getByRole('button', { name: 'Назад' }));
    await user.click(card.getByRole('button', { name: 'Исправить' }));
    await user.click(within(card.getByRole('form', { name: 'Исправление операции' })).getByRole('button', { name: 'Отмена' }));
    await user.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(screen.queryByRole('dialog', { name: 'Операция' })).not.toBeInTheDocument();
  });

  it.each([
    ['expense', expense({ portfolio_id: 'p-bonds', portfolio_code: 'OPIF1-BONDS' }), { 'Вид расхода': 'auditor', 'С чьего счёта платят': 'p-bonds', 'Сумма, ₽': '120000' }],
    ['fee', fee, { 'Вид комиссии': 'management', 'Оплачено по': '2026-10-31', 'Сумма, ₽': '20550' }],
    ['transfer', transferOp, { Откуда: 'p-bonds', Куда: 'p-treasury', Бумага: 'i-ofz', 'Количество, шт.': '10000' }],
  ] as const)('fills the amendment of %s', async (_name, found, fields) => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.operations.mockResolvedValue([found]);
    api.operation.mockResolvedValue(found);
    renderBooking(api, PAGE, true);

    await user.click((await screen.findAllByRole('row'))[2]!);
    const card = within(await screen.findByRole('dialog', { name: 'Операция' }));
    await user.click(await card.findByRole('button', { name: 'Исправить' }));
    const amend = within(card.getByRole('form', { name: 'Исправление операции' }));
    for (const [label, value] of Object.entries(fields)) {
      expect(amend.getByLabelText(label)).toHaveValue(value);
    }
    expect(card.queryByRole('button', { name: 'Подтвердить расчёт' })).not.toBeInTheDocument();
  });

  it('opens rows from the keyboard; contributions and cancelled operations are not amended', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    const repo = operation({ id: 'o-repo', kind: 'repo_contribution', units: null, planned_date: null, direction: 'out', amount: '500.00' });
    api.operations.mockResolvedValue([repo]);
    api.operation.mockResolvedValueOnce(repo).mockResolvedValueOnce(operation({ status: 'cancelled' }));
    renderBooking(api, PAGE, true);

    const row = (await screen.findAllByRole('row'))[2]!;
    row.focus();
    await user.keyboard('a');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await user.keyboard('{Enter}');
    const card = within(await screen.findByRole('dialog', { name: 'Операция' }));
    expect(await card.findByText('фонд — НКЦ', { exact: false })).toBeInTheDocument();
    expect(card.queryByRole('button', { name: 'Исправить' })).not.toBeInTheDocument();
    expect(card.getByRole('button', { name: 'Отменить операцию' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Закрыть' }));
    await user.click(row);
    expect(await within(screen.getByRole('dialog', { name: 'Операция' })).findByText('отменена')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Отменить операцию' })).not.toBeInTheDocument();
  });

  it('shows a settled operation, an incoming contribution and a fee without a calculation', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    const settled = operation({
      settled_date: '2026-11-27',
      number: 3,
      versions: [
        { number: 1, status: 'active', author: 'ops', created_at: '2026-11-26T09:00:00Z', comment: null, changed: [] },
        { number: 2, status: 'active', author: 'ops', created_at: '2026-11-26T09:30:00Z', comment: null, changed: [] },
        { number: 3, status: 'active', author: 'ops', created_at: '2026-11-27T09:00:00Z', comment: null, changed: ['settled_date', 'note'] },
      ],
    });
    const incoming = operation({ id: 'o-in', kind: 'repo_contribution', units: null, planned_date: null, direction: 'in' });
    api.operations.mockResolvedValue([settled, incoming]);
    api.operation.mockResolvedValueOnce(settled).mockResolvedValueOnce(incoming);
    renderBooking(api, PAGE, true);

    await user.click((await screen.findAllByText('выдача паёв'))[0]!);
    const card = within(screen.getByRole('dialog', { name: 'Операция' }));
    expect(await card.findByText('подтверждён 27.11.2026')).toBeInTheDocument();
    const versions = within(card.getByRole('table', { name: 'Версии операции' })).getAllByRole('row');
    expect(versions[1]).toHaveTextContent('расчёт, note');
    expect(versions[2]).toHaveTextContent('—');
    expect(card.queryByRole('button', { name: 'Подтвердить расчёт' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Закрыть' }));
    await user.click(screen.getAllByText('компенсационный взнос по РЕПО')[0]!);
    expect(await within(screen.getByRole('dialog', { name: 'Операция' })).findByText('НКЦ — фонду')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Закрыть' }));

    api.nav.mockRejectedValue(refused('нет'));
    const form = await openNew(user);
    await user.click(form.getByRole('radio', { name: /Вознаграждение/ }));
    expect(await form.findByText(/по 30.11.2026: 0,00 ₽\.$/)).toBeInTheDocument();
  });

  it('settles on today when there is no planned date', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.operation.mockResolvedValue(operation({ planned_date: null }));
    renderBooking(api, PAGE, true);

    await user.click((await screen.findAllByText('выдача паёв'))[0]!);
    await user.click(await screen.findByRole('button', { name: 'Подтвердить расчёт' }));
    expect(screen.getByLabelText('Дата расчёта')).toHaveValue('2026-12-03');
  });

  it('marks several settlements at once', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.operations.mockResolvedValue([
      operation(),
      operation({ id: 'o-2', units: '5000.00000', amount: '5000000.00', planned_date: '2026-12-04' }),
      operation({ id: 'o-3', units: '10.00000', amount: '10000.00' }),
    ]);
    api.settleOperation.mockResolvedValueOnce(operation({ settled_date: '2026-12-03' })).mockRejectedValueOnce(refused('Дата расчёта раньше даты операции.'));
    renderBooking(api, PAGE, true);

    await user.click(await screen.findByRole('button', { name: 'Подтвердить расчёт…' }));
    const panel = within(screen.getByRole('form', { name: 'Подтверждение расчёта' }));
    expect(panel.getAllByRole('checkbox').every((box) => (box as HTMLInputElement).checked)).toBe(true);
    await user.click(panel.getByLabelText(/10 паёв/));
    await user.click(panel.getByLabelText(/10 паёв/));
    await user.click(panel.getByLabelText(/10 паёв/));
    expect(panel.getByRole('button', { name: 'Подтвердить расчёт: 2' })).toBeEnabled();
    fireEvent.change(panel.getByLabelText('Дата расчёта'), { target: { value: '2026-12-02' } });
    await user.click(panel.getByRole('button', { name: 'Подтвердить расчёт: 2' }));

    expect(api.settleOperation.mock.calls).toEqual([
      ['o-1', '2026-12-02'],
      ['o-2', '2026-12-02'],
    ]);
    expect(await panel.findByText(/— подтверждён/)).toHaveClass('bulk-ok');
    expect(panel.getByText(/— Дата расчёта раньше/)).toHaveClass('bulk-error');
    expect(api.operations).toHaveBeenCalledTimes(2);
  });

  it('explains when there are no portfolios', async () => {
    const api = fakeApi();
    api.portfolios.mockResolvedValue([]);
    renderBooking(api, '/booking/operations');

    expect(await screen.findByText(/Нет доступных портфелей/)).toBeInTheDocument();
  });
});
