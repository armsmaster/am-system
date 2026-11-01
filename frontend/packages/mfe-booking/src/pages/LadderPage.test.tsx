import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { fakeApi, ladder, renderBooking } from '../test/fixtures';

describe('LadderPage', () => {
  it('shows money and securities on every settlement date', async () => {
    const api = renderBooking(fakeApi(), '/booking/ladder');

    const rows = within(await screen.findByRole('table', { name: 'Лестница расчётов' })).getAllByRole('row');
    expect(api.ladder).toHaveBeenCalledWith('a-broker');
    expect(rows[0]).toHaveTextContent('ДатаДвиженияДеньги на конец дня, ₽SU26238RMFS4, шт.');
    expect(rows[1]).toHaveTextContent(
      '30.11.2026OPIF1-BONDS: расчёты по сделке SU26238RMFS4 +50 000 шт. −28 000 500,00 ₽31 999 500,0050 000',
    );
    expect(rows[2]).toHaveTextContent('расчёты по сделке SU26238RMFS4 −20 000 шт. +11 224 200,00 ₽43 223 700,0030 000');
    expect(rows[3]).toHaveTextContent('OPIF1-BONDS: купон SU26238RMFS4 +1 062 000,00 ₽44 285 700,00');
    expect(rows[4]).toHaveTextContent('03.12.2026—44 285 700,0030 000');
    expect(screen.getByText(/Портфели счёта: OPIF1-BONDS. Прогноз от расчёта на 29.11.2026/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
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
    expect(screen.getByText(/Сумма купона не объявлена — в лестнице её нет/)).toBeInTheDocument();
    const rows = within(screen.getByRole('table', { name: 'Лестница расчётов' })).getAllByRole('row');
    expect(rows[1]).toHaveTextContent('05.12.2026 (выходной)OPIF1-BONDS: выплата: сумма не объявлена i-gone');
    const cells = within(rows[1]!).getAllByRole('cell');
    expect(cells[2]).toHaveClass('danger');
    expect(cells[3]).toHaveClass('danger');
    expect(rows[4]).toHaveTextContent('OPIF1-BONDS: расчёт по паям −28 000 500,00 ₽');
    expect(within(rows[4]!).getAllByRole('cell')[3]).toHaveTextContent('0');
  });

  it('switches the account', async () => {
    const user = userEvent.setup();
    const api = renderBooking(fakeApi(), '/booking/ladder');

    await screen.findByRole('table', { name: 'Лестница расчётов' });
    await user.selectOptions(screen.getByRole('combobox', { name: 'Счёт' }), 'a-bank');
    expect(api.ladder).toHaveBeenLastCalledWith('a-bank');
    expect(fireEvent.submit(screen.getByRole('form', { name: 'Счёт' }))).toBe(false);
  });

  it('explains when there are no accounts', async () => {
    const api = fakeApi();
    api.accounts.mockResolvedValue([]);
    renderBooking(api, '/booking/ladder');

    expect(await screen.findByText(/Нет доступных счетов/)).toBeInTheDocument();
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
    renderBooking(api, '/booking/ladder');

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
