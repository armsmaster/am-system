import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { FEE_RATES, fakeApi, nav, PORTFOLIOS, refused, renderBooking } from '../test/fixtures';

const PAGE = '/booking/nav?fund=p-fund&from=2026-11-29&till=2026-11-30';

describe('NavPage', () => {
  it('shows NAV, units and unit price by day and the composition', async () => {
    const fake = fakeApi();
    fake.feeRates.mockResolvedValue([
      FEE_RATES[0]!,
      { ...FEE_RATES[0]!, kind: 'specdep', rate: '0.100000', comment: null },
    ]);
    const api = renderBooking(fake, PAGE);

    const rows = within(await screen.findByRole('table', { name: 'СЧА по дням' })).getAllByRole('row');
    expect(api.nav).toHaveBeenCalledWith('p-fund', '2026-11-29', '2026-11-30');
    expect(rows[2]).toHaveTextContent('30.11.2026100 045 447,94100 0001 000,454 111,6320 552,06');
    const composition = screen.getByLabelText('Состав СЧА на 30.11.2026');
    expect(composition).toHaveTextContent('деньги на счетах71 999 500,00 ₽бумаги по чистой цене15 786 000,00 ₽');
    expect(composition).toHaveTextContent('− комиссии от СЧА к выплате20 552,06 ₽СЧА на 30.11.2026100 045 447,94 ₽');
    expect(composition).not.toHaveTextContent('требования');
    const rates = within(screen.getByRole('table', { name: 'Ставки комиссий' })).getAllByRole('row');
    expect(rates[1]).toHaveTextContent('вознаграждение УК1,526.11.2026ПДУ п. 92ops');
    expect(rates[2]).toHaveTextContent('спецдепозитарий0,126.11.2026—ops');
    // Трейдинг ставки не вводит.
    expect(screen.queryByRole('form', { name: 'Новая ставка' })).not.toBeInTheDocument();
  });

  it('sets a new rate', async () => {
    const user = userEvent.setup();
    const api = renderBooking(fakeApi(), PAGE, true);

    const form = await screen.findByRole('form', { name: 'Новая ставка' });
    await user.selectOptions(within(form).getByLabelText('Комиссия'), 'specdep');
    await user.type(within(form).getByLabelText('Ставка, % годовых'), '0,1');
    fireEvent.change(within(form).getByLabelText('Действует с'), { target: { value: '2026-12-01' } });
    await user.type(within(form).getByLabelText('Основание'), ' Договор 7 ');
    await user.click(within(form).getByRole('button', { name: 'Сохранить ставку' }));

    expect(api.setFeeRate).toHaveBeenCalledWith('p-fund', {
      kind: 'specdep',
      rate: '0.1',
      valid_from: '2026-12-01',
      comment: 'Договор 7',
    });
    expect(api.feeRates).toHaveBeenCalledTimes(2);
  });

  it('shows a refused rate', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.setFeeRate.mockRejectedValue(refused('У фонда OPIF1 не назначен казначейский портфель.'));
    api.feeRates.mockResolvedValue([]);
    renderBooking(api, PAGE, true);

    expect(await screen.findByText(/Ставок нет/)).toBeInTheDocument();
    const form = screen.getByRole('form', { name: 'Новая ставка' });
    await user.type(within(form).getByLabelText('Ставка, % годовых'), '1.5');
    await user.click(within(form).getByRole('button', { name: 'Сохранить ставку' }));
    expect(await within(form).findByRole('alert')).toHaveTextContent('казначейский портфель');
    expect(api.setFeeRate).toHaveBeenCalledWith('p-fund', expect.objectContaining({ kind: 'management', comment: null }));
  });

  it('changes the fund and the period', async () => {
    const api = renderBooking(fakeApi(), PAGE);

    await screen.findByRole('table', { name: 'СЧА по дням' });
    fireEvent.change(screen.getByRole('combobox', { name: 'Фонд' }), { target: { value: 'p-fund' } });
    fireEvent.change(screen.getByLabelText('С'), { target: { value: '2026-11-01' } });
    fireEvent.change(screen.getByLabelText('По'), { target: { value: '2026-11-15' } });
    fireEvent.change(screen.getByLabelText('По'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('С'), { target: { value: '' } });
    expect(api.nav).toHaveBeenLastCalledWith('p-fund', '2026-11-01', '2026-11-15');
    expect(fireEvent.submit(screen.getByRole('form', { name: 'Фонд и период' }))).toBe(false);
  });

  it('marks an incomplete valuation, a pending recalculation and an empty period', async () => {
    const api = fakeApi();
    const [first] = nav().days;
    api.nav.mockResolvedValue(
      nav({
        waiting_since: '2026-11-30',
        days: [{ ...first!, flags: ['no_price'], unit_price: null, claims: '1000.00', fees_today: {} }],
      }),
    );
    renderBooking(api, PAGE);

    expect(await screen.findByText(/Фонд пересчитывается с 30.11.2026/)).toBeInTheDocument();
    expect(screen.getByText(/Оценка неполная/)).toHaveTextContent('цены не было');
    const [, row] = within(screen.getByRole('table', { name: 'СЧА по дням' })).getAllByRole('row');
    expect(row).toHaveTextContent('100 000—0,00');
    expect(screen.getByLabelText('Состав СЧА на 29.11.2026')).toHaveTextContent('требования к пайщикам1 000,00 ₽');

    api.nav.mockResolvedValue(nav({ days: [] }));
    fireEvent.change(screen.getByLabelText('С'), { target: { value: '2026-10-01' } });
    expect(await screen.findByText('Расчёта за период нет.')).toBeInTheDocument();
  });

  it('defaults to the first fund and this month; explains when there are no funds', async () => {
    const api = renderBooking(fakeApi(), '/booking/nav');
    await screen.findByRole('table', { name: 'СЧА по дням' });
    const [fund, first] = api.nav.mock.calls[0]!;
    expect([fund, String(first).slice(8)]).toEqual(['p-fund', '01']);

    const none = fakeApi();
    none.portfolios.mockResolvedValue(PORTFOLIOS.filter((p) => !p.is_fund));
    renderBooking(none, '/booking/nav');
    expect(await screen.findByText(/Нет доступных фондов/)).toBeInTheDocument();
  });
});
