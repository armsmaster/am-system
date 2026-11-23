import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { monthStart, moscowToday, shiftDay } from '../format';
import { fakeApi, ladder, renderBooking } from '../test/fixtures';

function tiles(): string[] {
  return within(screen.getByLabelText('Итоги плана'))
    .getAllByRole('term')
    .map((dt) => `${dt.textContent}: ${dt.nextElementSibling?.textContent}`);
}

describe('PlanPage', () => {
  it('shows money and securities on every settlement date from today', async () => {
    const api = renderBooking(fakeApi(), '/booking/plan');

    const rows = within(await screen.findByRole('table', { name: 'План по расчётам' })).getAllByRole('row');
    expect(api.ladder).toHaveBeenCalledWith('a-broker', undefined);
    // Бумаги — не колонками, а метками в строке дня: сколько пришло или ушло и сколько стало.
    expect(rows[0]).toHaveTextContent(/^ДатаДвиженияДеньги на конец дня, ₽$/);
    expect(rows[1]).toHaveTextContent(
      '30.11 пнсегодняOPIF1-BONDS: расчёты по сделке SU26238RMFS4 −28 000 500,00 ₽SU26238RMFS4 +50 000 → 50 000 шт.' +
        '31 999 500,00',
    );
    expect(rows[1]).toHaveClass('next');
    expect(rows[2]).toHaveTextContent('01.12 втплан');
    expect(rows[2]).toHaveTextContent(
      'расчёты по сделке SU26238RMFS4 +11 224 200,00 ₽SU26238RMFS4 −20 000 → 30 000 шт.43 223 700,00',
    );
    expect(within(rows[2]!).getByText(/расчёты по сделке/)).toHaveClass('mv', 'mv-in');
    expect(within(rows[1]!).getByText(/расчёты по сделке/)).toHaveClass('mv', 'mv-out');
    expect(within(rows[1]!).getByText(/→ 50 000 шт\./)).toHaveClass('mv', 'mv-security');
    expect(rows[3]).toHaveTextContent('OPIF1-BONDS: купон SU26238RMFS4 +1 062 000,00 ₽44 285 700,00');
    expect(rows[4]).toHaveTextContent('03.12 чтплан—44 285 700,00');
    // Бумаги в плане: сейчас, движения по датам, минимум с датой.
    const securities = within(screen.getByRole('table', { name: 'Бумаги в плане' })).getAllByRole('row');
    expect(securities).toHaveLength(2);
    expect(securities[1]).toHaveTextContent('ОФЗ 26238SU26238RMFS450 00030.11 +50 00001.12 −20 00030 000на 01.12');
    expect(within(securities[1]!).getByText('01.12 −20 000')).toHaveClass('mv', 'mv-out');
    expect(tiles()).toEqual([
      'Деньги сегодня: 32,0 млн ₽',
      'Минимум до 03.12: 32,0 млн ₽на 30.11',
      'Дефицитов: 0',
    ]);
    expect(screen.getByText(/Портфели счёта: OPIF1-BONDS. Прогноз от расчёта на 29.11.2026/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows past days as fact', async () => {
    const api = fakeApi();
    api.ladder.mockResolvedValue(ladder({ today: '2026-12-02' }));
    renderBooking(api, '/booking/plan');

    const rows = within(await screen.findByRole('table', { name: 'План по расчётам' })).getAllByRole('row');
    expect(rows[1]).toHaveClass('past');
    expect(rows[1]).toHaveTextContent('30.11 пнфакт');
    expect(rows[3]).toHaveTextContent('02.12 срсегодня');
    // Деньги сегодня — на конец сегодняшнего дня; минимум — с сегодня.
    expect(tiles()).toEqual([
      'Деньги сегодня: 44,3 млн ₽',
      'Минимум до 03.12: 44,3 млн ₽на 02.12',
      'Дефицитов: 0',
    ]);
  });

  it('picks where the plan starts and keeps it in the address', async () => {
    const user = userEvent.setup();
    const api = renderBooking(fakeApi(), '/booking/plan');
    await screen.findByRole('table', { name: 'План по расчётам' });
    const today = moscowToday();

    await user.click(screen.getByRole('button', { name: 'Неделя назад' }));
    expect(api.ladder).toHaveBeenLastCalledWith('a-broker', shiftDay(today, -7));
    expect(screen.getByRole('button', { name: 'Неделя назад' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('button', { name: 'С начала месяца' }));
    expect(api.ladder).toHaveBeenLastCalledWith('a-broker', monthStart(today));

    await user.click(screen.getByRole('button', { name: 'С даты' }));
    const input = screen.getByLabelText('С даты');
    expect(input).toHaveAttribute('min', shiftDay(today, -93));
    fireEvent.change(input, { target: { value: '2026-11-02' } });
    expect(api.ladder).toHaveBeenLastCalledWith('a-broker', '2026-11-02');
    // Стёртая дата ничего не меняет.
    fireEvent.change(input, { target: { value: '' } });
    expect(api.ladder).toHaveBeenLastCalledWith('a-broker', '2026-11-02');
    expect(screen.getByRole('status', { name: 'Адрес' })).toHaveTextContent('account=a-broker&from=2026-11-02');

    await user.click(screen.getByRole('button', { name: 'С сегодня' }));
    await waitFor(() => expect(api.ladder).toHaveBeenLastCalledWith('a-broker', undefined));
    expect(screen.getByRole('status', { name: 'Адрес' })).toHaveTextContent(/plan\?account=a-broker$/);
  });

  it('offers today in the date field when the plan starts today', async () => {
    const user = userEvent.setup();
    renderBooking(fakeApi(), '/booking/plan');
    await screen.findByRole('table', { name: 'План по расчётам' });

    await user.click(screen.getByRole('button', { name: 'С даты' }));

    expect(screen.getByLabelText('С даты')).toHaveValue(moscowToday());
  });

  it('opens a date that is not a quick start as a chosen date', async () => {
    renderBooking(fakeApi(), '/booking/plan?account=a-broker&from=2026-11-02');

    await screen.findByRole('table', { name: 'План по расчётам' });
    expect(screen.getByRole('button', { name: 'С даты' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByLabelText('С даты')).toHaveValue('2026-11-02');
  });

  it('shows deficits in red', async () => {
    const api = fakeApi();
    const base = ladder();
    const [first] = base.days;
    api.ladder.mockResolvedValue(
      ladder({
        waiting_since: '2026-11-30',
        days: [
          {
            ...first!,
            date: '2026-12-05',
            trading: false,
            cash: '-1500.00',
            securities: [{ instrument_id: 'i-ofz', quantity: -10000 }],
            movements: [{ ...first!.movements[0]!, kind: 'payment_unknown', instrument_id: 'i-gone', quantity: 0, cash: '0' }],
          },
          // Тот же дефицит назавтра — без нового сообщения; деньги ушли глубже — с новым.
          { ...first!, date: '2026-12-06', cash: '-1500.00', securities: [{ instrument_id: 'i-ofz', quantity: -10000 }] },
          { ...first!, date: '2026-12-07', cash: '-2000.00', securities: [{ instrument_id: 'i-ofz', quantity: -10000 }] },
          {
            ...first!,
            date: '2026-12-08',
            securities: [],
            movements: [{ ...first!.movements[0]!, kind: 'unit_settlement', instrument_id: null, quantity: 0 }],
          },
        ],
      }),
    );
    renderBooking(api, '/booking/ladder');

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Не хватает 1 500,00 ₽ к расчётам 05.12.2026. Нужно РЕПО или перенос.');
    expect(alert).toHaveTextContent('Не хватает 10 000 SU26238RMFS4 к расчётам 05.12.2026.');
    expect(alert).toHaveTextContent('Не хватает 2 000,00 ₽ к расчётам 07.12.2026.');
    expect(within(alert).getAllByText(/Не хватает/)).toHaveLength(3);
    expect(screen.getByText(/Фонд пересчитывается с 30.11.2026/)).toBeInTheDocument();
    expect(screen.getByText(/Сумма купона не объявлена — в плане её нет/)).toBeInTheDocument();
    const rows = within(screen.getByRole('table', { name: 'План по расчётам' })).getAllByRole('row');
    expect(rows[1]).toHaveTextContent('05.12 сбвыходнойOPIF1-BONDS: выплата: сумма не объявлена i-gone');
    expect(within(rows[1]!).getByText(/выплата/)).toHaveClass('mv', 'mv-none');
    expect(rows[1]).toHaveClass('deficit');
    expect(within(rows[1]!).getAllByRole('cell')[2]).toHaveClass('danger');
    // Пришло, но всё равно не хватает — красная метка бумаги.
    expect(within(rows[2]!).getByText('SU26238RMFS4 +50 000 → не хватает 10 000 шт.')).toHaveClass('mv-short');
    expect(rows[4]).toHaveTextContent('OPIF1-BONDS: расчёт по паям −28 000 500,00 ₽');
    expect(rows[4]).not.toHaveClass('deficit');
    const securities = within(screen.getByRole('table', { name: 'Бумаги в плане' })).getAllByRole('row');
    expect(securities[1]).toHaveTextContent('ОФЗ 26238SU26238RMFS4—06.12 +50 00007.12 +50 000−10 00005.12 · дефицит');
    expect(within(securities[1]!).getAllByRole('cell')[3]).toHaveClass('danger');
    expect(tiles()).toEqual(['Деньги сегодня: —', 'Минимум до 08.12: −2 000 ₽на 07.12', 'Дефицитов: 3']);
    expect(screen.getByText('−2 000 ₽')).toHaveClass('danger');
  });

  it('switches the account', async () => {
    const user = userEvent.setup();
    const api = renderBooking(fakeApi(), '/booking/plan');

    await screen.findByRole('table', { name: 'План по расчётам' });
    await user.selectOptions(screen.getByRole('combobox', { name: 'Счёт' }), 'a-bank');
    expect(api.ladder).toHaveBeenLastCalledWith('a-bank', undefined);
  });

  it('explains when there are no accounts', async () => {
    const api = fakeApi();
    api.accounts.mockResolvedValue([]);
    renderBooking(api, '/booking/plan');

    expect(await screen.findByText(/Нет доступных счетов/)).toBeInTheDocument();
  });

  it('shows an empty plan', async () => {
    const api = fakeApi();
    api.ladder.mockResolvedValue(ladder({ days: [] }));
    renderBooking(api, '/booking/plan');

    await screen.findByRole('table', { name: 'План по расчётам' });
    expect(tiles()).toEqual(['Деньги сегодня: —', 'Минимум до —: —', 'Дефицитов: 0']);
    // Бумага без движений — свёрнута.
    expect(screen.getByRole('button', { name: 'Ещё 1 бумага без движений в плане — показать' })).toBeInTheDocument();
  });

  it('has no securities part when the account holds none', async () => {
    const api = fakeApi();
    api.ladder.mockResolvedValue(ladder({ instruments: [], days: [] }));
    renderBooking(api, '/booking/plan');

    await screen.findByRole('table', { name: 'План по расчётам' });
    expect(screen.queryByRole('table', { name: 'Бумаги в плане' })).not.toBeInTheDocument();
  });

  it('folds securities without movements and shows them on request', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    const base = ladder();
    const quiet = ['i-a', 'i-b', 'i-c', 'i-d', 'i-e'].map((id) => ({ instrument_id: id, secid: id.toUpperCase(), short_name: id }));
    api.ladder.mockResolvedValue(
      ladder({
        instruments: [...base.instruments, ...quiet],
        days: base.days.map((d) => ({
          ...d,
          securities: [...d.securities, ...quiet.map((q) => ({ instrument_id: q.instrument_id, quantity: 100 }))],
        })),
      }),
    );
    renderBooking(api, '/booking/plan');

    const table = await screen.findByRole('table', { name: 'Бумаги в плане' });
    expect(within(table).getAllByRole('row')).toHaveLength(3);
    await user.click(screen.getByRole('button', { name: 'Ещё 5 бумаг без движений в плане — показать' }));
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(8);
    expect(rows[2]).toHaveTextContent('i-aI-A100—100на 30.11');
    await user.click(screen.getByRole('button', { name: 'Свернуть бумаги без движений' }));
    expect(within(table).getAllByRole('row')).toHaveLength(3);
  });

  it('shows a transfer of securities only as a security mark', async () => {
    const api = fakeApi();
    const base = ladder();
    const [first] = base.days;
    const transfer = { ...first!.movements[0]!, kind: 'transfer_in' as const, quantity: 100, cash: '0.00' };
    api.ladder.mockResolvedValue(ladder({ days: [{ ...first!, movements: [transfer], securities: [{ instrument_id: 'i-ofz', quantity: 100 }] }] }));
    renderBooking(api, '/booking/plan');

    const rows = within(await screen.findByRole('table', { name: 'План по расчётам' })).getAllByRole('row');
    expect(rows[1]).toHaveTextContent('30.11 пнсегодняSU26238RMFS4 +100 → 100 шт.31 999 500,00');
    expect(within(rows[1]!).queryByText(/перевод/)).not.toBeInTheDocument();
  });

  it('shows a floater coupon forecast with its rate', async () => {
    const api = fakeApi();
    const base = ladder();
    const [first] = base.days;
    const coupon = { ...first!.movements[0]!, kind: 'coupon_forecast' as const, instrument_id: 'i-flt', quantity: 0 };
    api.ladder.mockResolvedValue(
      ladder({
        instruments: [...base.instruments, { instrument_id: 'i-flt', secid: 'RU000A107HG1', short_name: 'Газпнф3P8R' }],
        forecasts: [
          {
            instrument_id: 'i-flt',
            date: '2026-12-14',
            coupon: '12.58',
            index: 'key_rate',
            index_value: '14.0000',
            margin_bp: '130',
            capitalized: false,
          },
          {
            instrument_id: 'i-flt',
            date: '2026-12-15',
            coupon: '8.49',
            index: 'ruonia',
            index_value: '13.9000',
            margin_bp: '-20',
            capitalized: true,
          },
        ],
        days: [
          { ...first!, date: '2026-12-14', movements: [{ ...coupon, cash: '62900.00' }] },
          { ...first!, date: '2026-12-15', movements: [{ ...coupon, cash: '42450.00' }] },
        ],
      }),
    );
    renderBooking(api, '/booking/plan');

    expect(
      await screen.findByText(
        'OPIF1-BONDS: купон, прогноз RU000A107HG1 +62 900,00 ₽ (КС 14% + 1,3%, 12,58 ₽ на бумагу)',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'OPIF1-BONDS: купон, прогноз RU000A107HG1 +42 450,00 ₽ (RUONIA 13,9% − 0,2%, с капитализацией, 8,49 ₽ на бумагу)',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Сумма купона не объявлена/)).not.toBeInTheDocument();
  });
});
