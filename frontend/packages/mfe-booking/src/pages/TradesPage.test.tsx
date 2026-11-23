import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { moscowToday, shiftDay } from '../format';
import { fakeApi, refused, renderBooking, repoTrade, trade } from '../test/fixtures';

function rows(): HTMLElement[] {
  return within(screen.getByRole('table', { name: 'Сделки' })).getAllByRole('row').slice(1);
}

function totals(): string[] {
  return within(screen.getByLabelText('Итоги периода'))
    .getAllByRole('term')
    .map((dt) => `${dt.textContent}: ${dt.nextElementSibling?.textContent}`);
}

describe('TradesPage', () => {
  it('lists the trades of the first fund for today by default', async () => {
    const api = renderBooking();
    const today = moscowToday();

    await screen.findByRole('table', { name: 'Сделки' });
    expect(api.trades).toHaveBeenCalledWith('p-fund', today, today);
    expect(screen.getByRole('button', { name: 'Сегодня' })).toHaveAttribute('aria-pressed', 'true');
    const [row] = rows();
    expect(row).toHaveTextContent(
      '11:42ОФЗ 26238SU26238RMFS4облигация · OPIF1-BONDSпокупка50 000 шт.52,5%28 000 500,0030.11.2026введена',
    );
    // Бумага — как в справочнике: название ссылкой-бейджем, код, вид и портфель.
    expect(within(row!).getByRole('link', { name: 'ОФЗ 26238' })).toHaveClass('tag');
    expect(within(row!).getByText('покупка')).toHaveClass('pill', 'side-buy');
    expect(within(row!).getByText('введена')).toHaveClass('pill', 'status-entered');
    // Количество и цена — в одной ячейке, двумя строками разного стиля.
    expect(within(row!).getByText('50 000 шт.')).toHaveClass('qty');
    expect(within(row!).getByText('52,5%')).toHaveClass('unit-price');
    // Период в один день — без заголовков дней.
    expect(screen.queryByText(/· 1 сделка/)).not.toBeInTheDocument();
    expect(totals()).toEqual(['Сделок: 1', 'Куплено: 28,0 млн ₽', 'Продано: 0 ₽', 'Комиссии: 0 ₽']);
  });

  it('switches the period with quick buttons and keeps it in the address', async () => {
    const user = userEvent.setup();
    const api = renderBooking(fakeApi(), '/booking?portfolio=p-bonds');
    await screen.findByRole('table', { name: 'Сделки' });
    const today = moscowToday();

    await user.click(screen.getByRole('button', { name: 'Вчера' }));
    expect(api.trades).toHaveBeenLastCalledWith('p-bonds', shiftDay(today, -1), shiftDay(today, -1));
    await user.click(screen.getByRole('button', { name: 'Неделя' }));
    expect(api.trades).toHaveBeenLastCalledWith('p-bonds', shiftDay(today, -6), today);
    expect(screen.getByRole('button', { name: 'Неделя' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('button', { name: 'Месяц' }));
    expect(api.trades).toHaveBeenLastCalledWith('p-bonds', shiftDay(today, -29), today);
    expect(screen.getByRole('status', { name: 'Адрес' })).toHaveTextContent(
      `portfolio=p-bonds&from=${shiftDay(today, -29)}&till=${today}`,
    );

    await user.selectOptions(screen.getByRole('combobox', { name: 'Портфель' }), 'p-treasury');
    await waitFor(() => expect(api.trades).toHaveBeenLastCalledWith('p-treasury', shiftDay(today, -29), today));
  });

  it('takes a custom period', async () => {
    const user = userEvent.setup();
    const api = renderBooking(fakeApi(), '/booking?portfolio=p-bonds');
    await screen.findByRole('table', { name: 'Сделки' });
    expect(screen.queryByLabelText('С даты')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Период' }));
    fireEvent.change(screen.getByLabelText('С даты'), { target: { value: '2026-11-02' } });
    await waitFor(() => expect(api.trades).toHaveBeenLastCalledWith('p-bonds', '2026-11-02', moscowToday()));
    fireEvent.change(screen.getByLabelText('По дату'), { target: { value: '2026-11-27' } });
    await waitFor(() => expect(api.trades).toHaveBeenLastCalledWith('p-bonds', '2026-11-02', '2026-11-27'));
    // Стёртая дата ничего не меняет.
    fireEvent.change(screen.getByLabelText('С даты'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('По дату'), { target: { value: '' } });
    expect(api.trades).toHaveBeenLastCalledWith('p-bonds', '2026-11-02', '2026-11-27');
    expect(screen.getByRole('button', { name: 'Период' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('opens an old day address as a custom period', async () => {
    const api = fakeApi();
    api.trades.mockResolvedValue([]);
    renderBooking(api, '/booking?portfolio=p-fund&date=2026-11-27');

    expect(await screen.findByText('Сделок за этот период нет.')).toBeInTheDocument();
    expect(api.trades).toHaveBeenCalledWith('p-fund', '2026-11-27', '2026-11-27');
    expect(screen.getByLabelText('С даты')).toHaveValue('2026-11-27');
  });

  it('says when there are no trades today', async () => {
    const api = fakeApi();
    api.trades.mockResolvedValue([]);
    renderBooking(api);

    expect(await screen.findByText('Сделок за сегодня нет.')).toBeInTheDocument();
    expect(totals()).toEqual(['Сделок: 0', 'Куплено: 0 ₽', 'Продано: 0 ₽', 'Комиссии: 0 ₽']);
  });

  it('groups a longer period by days, newest first, with totals', async () => {
    const api = fakeApi();
    api.trades.mockResolvedValue([
      trade({ id: 't-old', trade_at: '2026-11-26T07:00:00Z', trade_date: '2026-11-26', fee: '3.50' }),
      trade({
        id: 't-sale',
        side: 'sell',
        amount: '1500000.00',
        fee: '1.25',
        trade_at: '2026-11-27T10:00:00Z',
        venue: 'negotiated',
        counterparty: 'Банк Пример',
      }),
      trade({ id: 't-1', trade_at: '2026-11-27T08:42:00Z' }),
      trade({ id: 't-gone', status: 'cancelled', amount: '999.00', trade_at: '2026-11-27T09:00:00Z' }),
      repoTrade({ trade_at: '2026-11-27T11:00:00Z', trade_date: '2026-11-27', fee: '10.00' }),
      trade({
        id: 't-lqdt',
        instrument_kind: 'fund_share',
        secid: 'LQDT',
        short_name: 'ВИМ Ликвидность',
        quantity: 1000,
        price: '1.5',
        amount: '1500.00',
        trade_at: '2026-11-26T08:00:00Z',
        trade_date: '2026-11-26',
      }),
    ]);
    renderBooking(api, '/booking?portfolio=p-bonds&from=2026-11-23&till=2026-11-29');

    await screen.findByRole('table', { name: 'Сделки' });
    const found = rows();
    expect(found.map((r) => r.querySelector('td')?.textContent)).toEqual([
      'пт, 27 ноября · 4 сделки',
      '14:00',
      '13:00',
      '12:00',
      '11:42',
      'чт, 26 ноября · 2 сделки',
      '11:00',
      '10:00',
    ]);
    expect(found[0]).toHaveClass('day-row');
    // РЕПО под облигации: ставка и дата второй части под видом сделки, вторая часть — в расчётах.
    expect(found[1]).toHaveTextContent(
      'прямое РЕПО14,5% · до 03.12.202610 000 шт.52,6%5 610 100,00комиссия 10,0030.11.2026→ 03.12.2026',
    );
    expect(within(found[1]!).getByText('прямое РЕПО')).toHaveClass('pill', 'side-repo');
    expect(found[2]).toHaveTextContent('облигация · OPIF1-BONDS · РПС · Банк Пример');
    expect(within(found[2]!).getByText('продажа')).toHaveClass('side-sell');
    // Отменённая — бледно-красной строкой с зачёркнутыми количеством и суммой (стили — в оболочке).
    expect(found[3]).toHaveClass('cancelled');
    expect(within(found[3]!).getByText('отменена')).toHaveClass('pill', 'status-cancelled');
    expect(found[6]).toHaveTextContent('ВИМ ЛиквидностьLQDTпай · OPIF1-BONDS');
    expect(found[6]).toHaveTextContent('1 000 шт.1,5 ₽');
    // Отменённые — не в итогах; РЕПО — ни купля, ни продажа, но его комиссия — в итогах.
    expect(totals()).toEqual(['Сделок: 5', 'Куплено: 56,0 млн ₽', 'Продано: 1,5 млн ₽', 'Комиссии: 15 ₽']);
  });

  it('shows a repo with clearing certificates without a security code', async () => {
    const api = fakeApi();
    api.trades.mockResolvedValue([repoTrade({ instrument_kind: 'ksu', instrument_id: null, secid: 'KSU', short_name: 'КСУ' })]);
    renderBooking(api);

    const [row] = (await screen.findByRole('table', { name: 'Сделки' })) && rows();
    expect(within(row!).getByText('КСУ')).toHaveClass('tag');
    expect(within(row!).queryByText('KSU')).not.toBeInTheDocument();
    expect(row).toHaveTextContent('РЕПО с КСУ · OPIF1-BONDS');
  });

  it('opens a trade in the side panel by a click on its row or Enter', async () => {
    const user = userEvent.setup();
    const api = renderBooking(fakeApi(), '/booking?portfolio=p-bonds');
    await screen.findByRole('table', { name: 'Сделки' });

    await user.click(rows()[0]!);

    expect(await screen.findByRole('dialog', { name: 'Сделка' })).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Адрес' })).toHaveTextContent('/booking/trades/t-1?portfolio=p-bonds');
    await waitFor(() => expect(api.trade).toHaveBeenCalledWith('t-1'));
    // Список за панелью остаётся как был: не перечитывается и не теряет прокрутку.
    expect(api.trades).toHaveBeenCalledTimes(1);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Сделка' })).not.toBeInTheDocument();

    fireEvent.keyDown(rows()[0]!, { key: ' ' });
    expect(screen.queryByRole('dialog', { name: 'Сделка' })).not.toBeInTheDocument();
    fireEvent.keyDown(rows()[0]!, { key: 'Enter' });
    expect(await screen.findByRole('dialog', { name: 'Сделка' })).toBeInTheDocument();
  });

  it('opens a trade by its name link without a second navigation', async () => {
    const user = userEvent.setup();
    renderBooking(fakeApi(), '/booking?portfolio=p-bonds');
    await screen.findByRole('table', { name: 'Сделки' });

    await user.click(screen.getByRole('link', { name: 'ОФЗ 26238' }));

    expect(await screen.findByRole('dialog', { name: 'Сделка' })).toBeInTheDocument();
  });

  it('enters a trade in the side panel with the accrued interest filled in and shows the warnings', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.enter.mockResolvedValue(
      trade({ side: 'sell', warnings: ['Продажа больше позиции: на 30.11.2026 в портфеле 50000 шт.'] }),
    );
    renderBooking(api, '/booking?portfolio=p-bonds');
    await screen.findByRole('table', { name: 'Сделки' });

    await user.click(screen.getByRole('button', { name: 'Новая сделка' }));
    const panel = within(screen.getByRole('dialog', { name: 'Новая сделка' }));
    const form = within(panel.getByRole('form', { name: 'Новая сделка' }));
    expect(form.getByLabelText('Портфель')).toHaveValue('p-bonds');
    await user.selectOptions(form.getByLabelText('Бумага'), 'i-ofz');
    await waitFor(() => expect(form.getByLabelText('НКД на бумагу, ₽')).toHaveValue('35.01'));
    await user.selectOptions(form.getByLabelText('Направление'), 'sell');
    await user.selectOptions(form.getByLabelText('Место'), 'negotiated');
    await user.type(form.getByLabelText('Количество, шт.'), '60000');
    await user.type(form.getByLabelText('Цена, % от номинала'), '52,6');
    await user.type(form.getByLabelText('Номер сделки у брокера'), 'B-17');
    await user.click(form.getByRole('button', { name: 'Ввести сделку' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Новая сделка' })).not.toBeInTheDocument());
    const [body] = api.enter.mock.calls[0] as [Record<string, unknown>];
    expect(body).toMatchObject({
      portfolio_id: 'p-bonds',
      side: 'sell',
      quantity: 60000,
      venue: 'negotiated',
      external_id: 'B-17',
    });
    expect(screen.getByText(/Сделка введена/)).toHaveTextContent(
      'Сделка введена: SU26238RMFS4, продажа 50 000 шт., сумма 28 000 500,00 ₽, расчёты 30.11.2026.',
    );
    expect(screen.getByRole('link', { name: 'SU26238RMFS4' })).toHaveAttribute(
      'href',
      '/booking/trades/t-1?portfolio=p-bonds',
    );
    expect(screen.getByText(/Продажа больше позиции/)).toBeInTheDocument();
    expect(api.trades).toHaveBeenCalledTimes(2); // список перечитан
  });

  it('notes the second part of an entered repo', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.enter.mockResolvedValue(repoTrade({ quantity: 0 }));
    renderBooking(api, '/booking?portfolio=p-bonds');
    await screen.findByRole('table', { name: 'Сделки' });

    await user.click(screen.getByRole('button', { name: 'Новая сделка' }));
    await user.click(within(screen.getByRole('dialog', { name: 'Новая сделка' })).getByRole('button', { name: 'Отмена' }));
    expect(screen.queryByRole('dialog', { name: 'Новая сделка' })).not.toBeInTheDocument();
    expect(api.enter).not.toHaveBeenCalled();
  });

  it('starts a new trade from the first physical portfolio when a fund is selected', async () => {
    const user = userEvent.setup();
    renderBooking(fakeApi(), '/booking?portfolio=p-fund');
    await user.click(await screen.findByRole('button', { name: 'Новая сделка' }));

    const form = within(screen.getByRole('form', { name: 'Новая сделка' }));
    expect(form.getByLabelText('Портфель')).toHaveValue('p-treasury');
  });

  it('explains when there are no portfolios', async () => {
    const api = fakeApi();
    api.portfolios.mockResolvedValue([]);
    api.trades.mockRejectedValue(refused('нет портфеля'));
    renderBooking(api);

    expect(await screen.findByText(/Нет доступных портфелей/)).toBeInTheDocument();
  });
});
