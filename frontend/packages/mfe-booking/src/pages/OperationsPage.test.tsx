import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { fakeApi, operation, PORTFOLIOS, refused, renderBooking } from '../test/fixtures';

const PAGE = '/booking/operations?portfolio=p-fund';

describe('OperationsPage', () => {
  it('lists the operations of every kind', async () => {
    const api = fakeApi();
    api.operations.mockResolvedValue([
      operation(),
      operation({ id: 'o-2', settled_date: '2026-11-27', status: 'cancelled' }),
      operation({
        id: 'o-3',
        kind: 'expense_accrual',
        expense_kind: 'specdep',
        units: null,
        amount: '50000.00',
        planned_date: null,
      }),
      operation({
        id: 'o-5',
        kind: 'fee_payment',
        units: null,
        amount: '20550.00',
        planned_date: null,
        fee_kind: 'management',
        period_end: '2026-11-30',
      }),
      operation({
        id: 'o-4',
        kind: 'security_transfer',
        portfolio_code: 'OPIF1-BONDS',
        receiver_code: 'OPIF1-TREASURY',
        units: null,
        amount: null,
        planned_date: null,
        secid: 'SU26238RMFS4',
        quantity: 10000,
      }),
    ]);
    renderBooking(api, PAGE);

    const rows = within(await screen.findByRole('table', { name: 'Операции' })).getAllByRole('row');
    expect(api.operations).toHaveBeenCalledWith('p-fund');
    expect(rows[1]).toHaveTextContent('26.11.2026выдача паёвOPIF1-TREASURY100 000100 000 000,00план 27.11.2026действует');
    expect(rows[2]).toHaveTextContent('отмечен 27.11.2026отменена');
    expect(rows[3]).toHaveTextContent('начисление расхода, специализированный депозитарий');
    expect(rows[3]).toHaveTextContent('—50 000,00—');
    expect(rows[4]).toHaveTextContent('выплата вознаграждения, вознаграждение УК по 30.11.2026');
    expect(rows[5]).toHaveTextContent('перевод бумаг, SU26238RMFS4OPIF1-BONDS → OPIF1-TREASURY10 000—');
    // Трейдинг видит операции, но не проводит их.
    expect(screen.queryByRole('button', { name: 'Новая операция' })).not.toBeInTheDocument();
    expect(screen.queryByText('Изменить')).not.toBeInTheDocument();
  });

  it('switches the portfolio and explains an empty list', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.operations.mockResolvedValue([]);
    renderBooking(api, PAGE);

    expect(await screen.findByText('Операций нет.')).toBeInTheDocument();
    await user.selectOptions(screen.getByRole('combobox', { name: 'Портфель' }), 'p-bonds');
    expect(api.operations).toHaveBeenLastCalledWith('p-bonds');
    expect(fireEvent.submit(screen.getByRole('form', { name: 'Портфель' }))).toBe(false);
  });

  it.each([
    ['without a fund', PORTFOLIOS.filter((p) => p.id === 'p-treasury')],
    ['without physical portfolios', PORTFOLIOS.filter((p) => p.is_fund)],
  ])('opens the form %s', async (_name, portfolios) => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.portfolios.mockResolvedValue(portfolios);
    renderBooking(api, '/booking/operations', true);

    await user.click(await screen.findByRole('button', { name: 'Новая операция' }));
    const form = screen.getByRole('form', { name: 'Новая операция' });
    await user.selectOptions(within(form).getByLabelText('Операция'), 'перевод денег');
    expect(within(form).getByLabelText('Куда')).toHaveValue('');
  });

  it('issues units into the treasury portfolio of the fund', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.enterOperation.mockResolvedValue(operation({ warnings: ['Проверьте сумму'] }));
    renderBooking(api, PAGE, true);

    await user.click(await screen.findByRole('button', { name: 'Новая операция' }));
    const form = screen.getByRole('form', { name: 'Новая операция' });
    const field = (name: string) => within(form).getByLabelText(name);
    await user.selectOptions(field('Фонд'), 'p-fund');
    await user.clear(field('Дата'));
    await user.type(field('Дата'), '2026-11-26');
    await user.type(field('Паи, шт.'), '100 000,5');
    await user.type(field('Сумма, ₽'), '100000000');
    await user.clear(field('Плановая дата расчёта'));
    await user.type(field('Плановая дата расчёта'), '2026-11-27');
    await user.type(field('Комментарий'), 'Заявка 17');
    await user.click(within(form).getByRole('button', { name: 'Провести операцию' }));

    expect(api.enterOperation).toHaveBeenCalledWith({
      kind: 'unit_issue',
      fund_id: 'p-fund',
      op_date: '2026-11-26',
      units: '100000.5',
      amount: '100000000',
      planned_date: '2026-11-27',
      comment: 'Заявка 17',
    });
    expect(await screen.findByText('Проведено: выдача паёв от 26.11.2026.')).toBeInTheDocument();
    expect(screen.getByText('Проверьте сумму')).toBeInTheDocument();
    expect(api.operations).toHaveBeenCalledTimes(2);
  });

  it.each([
    [
      'выплата расхода',
      async (form: HTMLElement, user: ReturnType<typeof userEvent.setup>) => {
        await user.selectOptions(within(form).getByLabelText('Вид расхода'), 'auditor');
        await user.type(within(form).getByLabelText('Сумма, ₽'), '30000');
      },
      { kind: 'expense_payment', fund_id: 'p-fund', expense_kind: 'auditor', amount: '30000' },
    ],
    [
      'выплата вознаграждения',
      async (form: HTMLElement, user: ReturnType<typeof userEvent.setup>) => {
        await user.selectOptions(within(form).getByLabelText('Вид комиссии'), 'specdep');
        // Не прошлый месяц: подсказку меняют на квартал, а не оставляют как есть.
        fireEvent.change(within(form).getByLabelText('Оплачено по'), { target: { value: '2026-09-30' } });
        await user.type(within(form).getByLabelText('Сумма, ₽'), '20550');
      },
      { kind: 'fee_payment', fund_id: 'p-fund', fee_kind: 'specdep', period_end: '2026-09-30', amount: '20550' },
    ],
    [
      'перевод денег',
      async (form: HTMLElement, user: ReturnType<typeof userEvent.setup>) => {
        await user.selectOptions(within(form).getByLabelText('Откуда'), 'p-treasury');
        await user.selectOptions(within(form).getByLabelText('Куда'), 'p-bonds');
        await user.type(within(form).getByLabelText('Сумма, ₽'), '60000000');
      },
      { kind: 'cash_transfer', portfolio_id: 'p-treasury', receiver_id: 'p-bonds', amount: '60000000' },
    ],
    [
      'перевод бумаг',
      async (form: HTMLElement, user: ReturnType<typeof userEvent.setup>) => {
        await user.selectOptions(within(form).getByLabelText('Откуда'), 'p-bonds');
        await user.selectOptions(within(form).getByLabelText('Куда'), 'p-treasury');
        await user.selectOptions(within(form).getByLabelText('Бумага'), 'i-ofz');
        await user.type(within(form).getByLabelText('Количество, шт.'), '10000');
      },
      {
        kind: 'security_transfer',
        portfolio_id: 'p-bonds',
        receiver_id: 'p-treasury',
        instrument_id: 'i-ofz',
        quantity: 10000,
      },
    ],
  ] as const)('enters «%s»', async (label, fill, expected) => {
    const user = userEvent.setup();
    const api = renderBooking(fakeApi(), PAGE, true);

    await user.click(await screen.findByRole('button', { name: 'Новая операция' }));
    const form = screen.getByRole('form', { name: 'Новая операция' });
    await user.selectOptions(within(form).getByLabelText('Операция'), label);
    await fill(form, user);
    await user.click(within(form).getByRole('button', { name: 'Провести операцию' }));

    expect(api.enterOperation).toHaveBeenCalledWith(expect.objectContaining({ ...expected, comment: null }));
  });

  it('shows a refusal and closes the form on cancel', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.enterOperation.mockRejectedValue(refused('Переводят только внутри фонда.'));
    renderBooking(api, PAGE, true);

    await user.click(await screen.findByRole('button', { name: 'Новая операция' }));
    const form = screen.getByRole('form', { name: 'Новая операция' });
    await user.type(within(form).getByLabelText('Паи, шт.'), '1');
    await user.type(within(form).getByLabelText('Сумма, ₽'), '1');
    await user.click(within(form).getByRole('button', { name: 'Провести операцию' }));
    expect(await within(form).findByRole('alert')).toHaveTextContent('Переводят только внутри фонда.');

    await user.click(within(form).getByRole('button', { name: 'Отмена' }));
    expect(screen.queryByRole('form', { name: 'Новая операция' })).not.toBeInTheDocument();
  });

  it('marks the settlement and cancels an operation', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.operations.mockResolvedValue([
      operation(),
      operation({ id: 'o-2', kind: 'cash_transfer', units: null, planned_date: null }),
    ]);
    renderBooking(api, PAGE, true);

    const rows = within(await screen.findByRole('table', { name: 'Операции' })).getAllByRole('row');
    await user.click(within(rows[1]!).getByText('Изменить'));
    const settle = within(rows[1]!).getByRole('form', { name: 'Отметка расчёта' });
    expect(within(settle).getByLabelText('Дата расчёта')).toHaveValue('2026-11-27');
    fireEvent.change(within(settle).getByLabelText('Дата расчёта'), { target: { value: '2026-11-30' } });
    await user.click(within(settle).getByRole('button', { name: 'Отметить расчёт' }));
    expect(api.settleOperation).toHaveBeenCalledWith('o-1', '2026-11-30');

    // Перевод: расчёт по паям не отмечают — только отмена с причиной.
    await user.click(within(rows[2]!).getByText('Изменить'));
    expect(within(rows[2]!).queryByRole('form', { name: 'Отметка расчёта' })).not.toBeInTheDocument();
    api.cancelOperation.mockRejectedValueOnce(refused('Операция отменена — её уже не изменить.'));
    const cancel = within(rows[2]!).getByRole('form', { name: 'Отмена операции' });
    await user.type(within(cancel).getByLabelText('Причина отмены'), ' Ошибка ввода ');
    await user.click(within(cancel).getByRole('button', { name: 'Отменить операцию' }));
    expect(api.cancelOperation).toHaveBeenCalledWith('o-2', 'Ошибка ввода');
    expect(await within(rows[2]!).findByRole('alert')).toHaveTextContent('её уже не изменить');
  });

  it('settles on today when there is no planned date', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.operations.mockResolvedValue([operation({ planned_date: null })]);
    renderBooking(api, PAGE, true);

    const rows = within(await screen.findByRole('table', { name: 'Операции' })).getAllByRole('row');
    await user.click(within(rows[1]!).getByText('Изменить'));
    expect(within(rows[1]!).getByLabelText('Дата расчёта')).not.toHaveValue('');
  });

  it('explains when there are no portfolios', async () => {
    const api = fakeApi();
    api.portfolios.mockResolvedValue([]);
    renderBooking(api, '/booking/operations');

    expect(await screen.findByText(/Нет доступных портфелей/)).toBeInTheDocument();
  });
});
