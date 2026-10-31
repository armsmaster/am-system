import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { fakeApi, refused, renderBooking, trade } from '../test/fixtures';

function facts(): Record<string, string> {
  const list = document.querySelector<HTMLElement>('dl.facts')!;
  const terms = within(list).getAllByRole('term');
  return Object.fromEntries(terms.map((t) => [t.textContent, t.nextElementSibling?.textContent ?? '']));
}

describe('TradePage', () => {
  it('shows the trade and its versions', async () => {
    const api = fakeApi();
    api.trade.mockResolvedValue(
      trade({
        number: 2,
        external_id: 'B-17',
        counterparty: 'Брокер',
        venue: 'otc',
        versions: [
          ...trade().versions,
          {
            number: 2,
            status: 'entered',
            author: 'ops',
            created_at: '2026-11-27T09:00:00Z',
            comment: 'По отчёту брокера',
            changed: ['quantity', 'accrued', 'mystery'],
          },
        ],
      }),
    );
    renderBooking(api, '/booking/trades/t-1');

    expect(await screen.findByRole('heading', { name: 'покупка SU26238RMFS4 — ОФЗ 26238' })).toBeInTheDocument();
    expect(api.trade).toHaveBeenCalledWith('t-1');
    expect(facts()).toMatchObject({
      Портфель: 'OPIF1-BONDS',
      Количество: '50 000 шт.',
      Цена: '52,5% от номинала',
      'Номинал на дату расчётов': '1 000 ₽',
      'НКД на бумагу': '35,01 ₽',
      Сумма: '28 000 500,00 ₽',
      Расчёты: '30.11.2026',
      Место: 'внебиржевая, Брокер',
      'Номер сделки у брокера': 'B-17',
    });
    const versions = within(screen.getByRole('table', { name: 'Версии сделки' })).getAllByRole('row');
    expect(versions[1]).toHaveTextContent('количество, НКД, mystery');
    expect(versions[1]).toHaveTextContent('opsПо отчёту брокера');
    expect(versions[2]).toHaveTextContent('—');
  });

  it('shows a fund share without bond fields', async () => {
    const api = fakeApi();
    api.trade.mockResolvedValue(
      trade({ instrument_kind: 'fund_share', price: '1.5', face_value: null, accrued: null }),
    );
    renderBooking(api, '/booking/trades/t-1');

    await screen.findByRole('heading', { name: /SU26238RMFS4/ });
    expect(facts()['Цена']).toBe('1,5 ₽ за пай');
    expect(facts()).not.toHaveProperty('НКД на бумагу');
  });

  it('lets operations confirm a trade', async () => {
    const user = userEvent.setup();
    const api = renderBooking(fakeApi(), '/booking/trades/t-1', true);

    await user.click(await screen.findByRole('button', { name: 'Подтвердить' }));

    await waitFor(() => expect(api.confirm).toHaveBeenCalledWith('t-1', ''));
    expect(api.trade).toHaveBeenCalledTimes(2);
  });

  it('shows why confirmation failed', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.confirm.mockRejectedValue(refused('Сделка уже подтверждена.'));
    renderBooking(api, '/booking/trades/t-1', true);

    await user.click(await screen.findByRole('button', { name: 'Подтвердить' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Сделка уже подтверждена.');
  });

  it('amends a trade as a new version with a reason', async () => {
    const user = userEvent.setup();
    const api = renderBooking(fakeApi(), '/booking/trades/t-1');

    await user.click(await screen.findByRole('button', { name: 'Исправить' }));
    const form = within(screen.getByRole('form', { name: 'Исправление сделки' }));
    expect(form.getByLabelText('Количество, шт.')).toHaveValue('50000');
    await user.clear(form.getByLabelText('Количество, шт.'));
    await user.type(form.getByLabelText('Количество, шт.'), '25000');
    await user.type(form.getByLabelText('Что исправлено и почему'), 'По отчёту брокера');
    await user.click(form.getByRole('button', { name: 'Сохранить версию' }));

    await waitFor(() => expect(api.amend).toHaveBeenCalled());
    const [id, body] = api.amend.mock.calls[0] as [string, Record<string, unknown>];
    expect(id).toBe('t-1');
    expect(body).toMatchObject({ quantity: 25000, comment: 'По отчёту брокера', accrued: '35.01' });
    await waitFor(() => expect(screen.queryByRole('form', { name: 'Исправление сделки' })).not.toBeInTheDocument());
  });

  it('cancels a trade with a reason and can step back', async () => {
    const user = userEvent.setup();
    const api = renderBooking(fakeApi(), '/booking/trades/t-1');

    await user.click(await screen.findByRole('button', { name: 'Отменить сделку' }));
    await user.click(screen.getByRole('button', { name: 'Назад' }));
    await user.click(screen.getByRole('button', { name: 'Отменить сделку' }));
    const form = within(screen.getByRole('form', { name: 'Отмена сделки' }));
    await user.type(form.getByLabelText('Почему отменяется'), 'Сделки не было');
    await user.click(form.getByRole('button', { name: 'Отменить сделку' }));

    await waitFor(() => expect(api.cancel).toHaveBeenCalledWith('t-1', 'Сделки не было'));
  });

  it('closes the amendment without saving', async () => {
    const user = userEvent.setup();
    renderBooking(fakeApi(), '/booking/trades/t-1');

    await user.click(await screen.findByRole('button', { name: 'Исправить' }));
    await user.click(screen.getByRole('button', { name: 'Отмена' }));

    expect(screen.getByRole('button', { name: 'Исправить' })).toBeInTheDocument();
  });

  it.each([
    ['a confirmed trade for trading', 'confirmed', false],
    ['a cancelled trade for anyone', 'cancelled', true],
  ] as const)('offers no changes to %s', async (_name, status, canConfirm) => {
    const api = fakeApi();
    api.trade.mockResolvedValue(trade({ status }));
    renderBooking(api, '/booking/trades/t-1', canConfirm);

    await screen.findByRole('heading', { name: /SU26238RMFS4/ });
    expect(screen.queryByRole('button', { name: 'Исправить' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Подтвердить' })).not.toBeInTheDocument();
  });
});
