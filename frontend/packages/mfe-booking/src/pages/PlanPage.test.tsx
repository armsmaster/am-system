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
  it('shows money in and out, the balance and securities on every settlement date from today', async () => {
    const user = userEvent.setup();
    const api = renderBooking(fakeApi(), '/booking/plan');

    const rows = within(await screen.findByRole('table', { name: 'План по расчётам' })).getAllByRole('row');
    expect(api.ladder).toHaveBeenCalledWith('a-broker', undefined);
    expect(rows[0]).toHaveTextContent(/^ДатаПоступило, ₽Списано, ₽Деньги на конец дня, ₽Бумаги$/);
    expect(rows[1]).toHaveTextContent(
      '30.11 пнсегодня—−28 000 500,00сделкиОФЗ 26238 · 1 покупка, +50 000 шт.28 000 500,00' +
        '31 999 500,00ОФЗ 26238 +50 000 → 50 000 шт.',
    );
    expect(rows[1]).toHaveClass('next');
    expect(rows[2]).toHaveTextContent(
      '01.12 втплан+11 224 200,00сделкиОФЗ 26238 · 1 продажа, −20 000 шт.11 224 200,00—43 223 700,00' +
        'ОФЗ 26238 −20 000 → 30 000 шт.',
    );
    expect(within(rows[2]!).getByText('+11 224 200,00')).toHaveClass('plan-total', 'up');
    expect(rows[3]).toHaveTextContent('02.12 срплан+1 062 000,00купонОФЗ 26238');
    // День без движений — свёрнутой строкой.
    expect(rows[4]).toHaveTextContent('03.12 чт · без движений44 285 700,00');
    expect(rows[4]).toHaveClass('plan-quiet');
    // Бумаги в плане: сейчас, движения по датам, минимум с датой.
    const securities = within(screen.getByRole('table', { name: 'Бумаги в плане' })).getAllByRole('row');
    expect(securities).toHaveLength(2);
    expect(securities[1]).toHaveTextContent('ОФЗ 26238SU26238RMFS450 00030.11 +50 00001.12 −20 00030 000на 01.12');
    expect(within(securities[1]!).getByText('01.12 −20 000')).toHaveClass('mv', 'mv-out');
    expect(tiles()).toEqual([
      'Деньги сегодня: 32,0 млн ₽',
      'Можно разместить до 03.12: 32,0 млн ₽минимум остатка — 30.11',
      'Дефицит денег: нет',
      'Дефицит бумаг: нет',
    ]);
    expect(screen.getByText(/Портфели счёта: OPIF1-BONDS. Прогноз от расчёта на 29.11.2026/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    // Столбики остатка по дням: щелчок открывает движения дня.
    const chart = within(screen.getByRole('group', { name: 'Деньги на конец дня' }));
    const columns = chart.getAllByRole('button');
    expect(columns.map((c) => c.getAttribute('aria-label'))[0]).toBe(
      '30.11 пн: на конец дня 31 999 500,00 ₽, поступило 0,00, списано 28 000 500,00',
    );
    expect(columns[0]).toHaveClass('today');
    expect(columns.map((c) => c.querySelector('.plan-col-label')?.textContent)).toEqual(['30', '01', '02', '03']);
    await user.click(columns[0]!);
    const panel = within(screen.getByRole('dialog', { name: 'Движения 30.11 · сегодня' }));
    expect(panel.getByLabelText('Деньги дня')).toHaveTextContent(
      'на начало дня, ₽60 000 000,00поступило, ₽0,00списано, ₽−28 000 500,00на конец дня, ₽31 999 500,00',
    );
    expect(panel.getByRole('region', { name: 'Сделки' })).toHaveTextContent(
      'ОФЗ 26238 · 1 покупка, +50 000 шт.−28 000 500,00OPIF1-BONDS · расчёты по сделке · сделка−28 000 500,00',
    );
    expect(panel.getByRole('link', { name: 'сделка' })).toHaveAttribute('href', '/booking/trades/t-1');
    expect(panel.getByRole('region', { name: 'Бумаги дня' })).toHaveTextContent('ОФЗ 26238+50 000 → 50 000 шт.');
    await user.click(screen.getByRole('button', { name: 'Закрыть' }));

    // Строка тоже открывает день — щелчком и клавишей Enter.
    await user.click(rows[3]!);
    expect(screen.getByRole('dialog', { name: 'Движения 02.12 · план' })).toHaveTextContent('Купоны и погашения');
    await user.click(screen.getByRole('button', { name: 'Закрыть' }));
    rows[2]!.focus();
    await user.keyboard('{Enter}');
    expect(screen.getByRole('dialog', { name: 'Движения 01.12 · план' })).toBeInTheDocument();
    await user.keyboard('a');
    expect(screen.getByRole('dialog', { name: 'Движения 01.12 · план' })).toBeInTheDocument();
  });

  it('shows past days as fact', async () => {
    const api = fakeApi();
    api.ladder.mockResolvedValue(ladder({ today: '2026-12-02' }));
    renderBooking(api, '/booking/plan');

    const rows = within(await screen.findByRole('table', { name: 'План по расчётам' })).getAllByRole('row');
    expect(rows[1]).toHaveClass('past');
    expect(rows[1]).toHaveTextContent('30.11 пнфакт');
    expect(rows[3]).toHaveTextContent('02.12 срсегодня');
    expect(screen.getAllByRole('button', { name: /^30.11 пн/ })[0]).toHaveClass('past');
    // Деньги сегодня — на конец сегодняшнего дня; минимум — с сегодня.
    expect(tiles()).toEqual([
      'Деньги сегодня: 44,3 млн ₽',
      'Можно разместить до 03.12: 44,3 млн ₽минимум остатка — 02.12',
      'Дефицит денег: нет',
      'Дефицит бумаг: нет',
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
          {
            ...first!,
            date: '2026-12-07',
            cash: '-2000.00',
            securities: [
              { instrument_id: 'i-ofz', quantity: -10000 },
              { instrument_id: 'i-gone', quantity: -5 },
            ],
          },
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
    expect(alert).toHaveTextContent('Не хватает 5 i-gone к расчётам 07.12.2026.');
    expect(within(alert).getAllByText(/Не хватает/)).toHaveLength(4);
    expect(screen.getByText(/Фонд пересчитывается с 30.11.2026/)).toBeInTheDocument();
    expect(screen.getByText(/Сумма купона не объявлена — в плане её нет/)).toBeInTheDocument();
    const rows = within(screen.getByRole('table', { name: 'План по расчётам' })).getAllByRole('row');
    // Выплата без суммы — не в ячейках денег, а в панели дня; бумаги не хватает и без движения.
    expect(rows[1]).toHaveTextContent('05.12 сбвыходной——−1 500,00не хватаетОФЗ 26238 → не хватает 10 000 шт.');
    expect(rows[1]).toHaveClass('deficit');
    expect(within(rows[1]!).getAllByRole('cell')[3]).toHaveClass('danger');
    // Пришло, но всё равно не хватает — красная метка бумаги.
    expect(within(rows[2]!).getByText('ОФЗ 26238 +50 000 → не хватает 10 000 шт.')).toHaveClass('mv-short');
    expect(rows[4]).toHaveTextContent('08.12 втплан—−28 000 500,00паирасчёт по паям28 000 500,00');
    expect(rows[4]).not.toHaveClass('deficit');
    // Дефицит — красным столбиком вниз от нуля.
    expect(screen.getAllByRole('button', { name: /^05.12 сб/ })[0]!.querySelector('.plan-bar')).toHaveClass('plan-bar-short');
    const user = userEvent.setup();
    await user.click(rows[1]!);
    const panel = within(screen.getByRole('dialog', { name: 'Движения 05.12 · выходной' }));
    expect(panel.getByRole('region', { name: 'Без денег' })).toHaveTextContent(
      'OPIF1-BONDS · выплата: сумма не объявлена · i-gone',
    );
    expect(panel.getByRole('region', { name: 'Бумаги дня' })).toHaveTextContent('ОФЗ 26238не хватает 10 000 шт.');
    const securities = within(screen.getByRole('table', { name: 'Бумаги в плане' })).getAllByRole('row');
    expect(securities[1]).toHaveTextContent('ОФЗ 26238SU26238RMFS4—06.12 +50 00007.12 +50 000−10 00005.12 · дефицит');
    expect(within(securities[1]!).getAllByRole('cell')[3]).toHaveClass('danger');
    expect(tiles()).toEqual([
      'Деньги сегодня: —',
      'Можно разместить до 08.12: 0 ₽минимум остатка — 07.12',
      'Дефицит денег: −2 000 ₽05.12–07.12 · 3 дня',
      'Дефицит бумаг: 2 бумагиОФЗ 26238 с 05.12 и другие',
    ]);
  });

  it('folds the smallest items of a busy day and labels a long chart by Mondays', async () => {
    const api = fakeApi();
    const base = ladder();
    const [first] = base.days;
    const coupon = (instrument: string, cash: string) => ({ ...first!.movements[0]!, kind: 'coupon' as const, instrument_id: instrument, trade_id: null, quantity: 0, cash });
    const busy = { ...first!, movements: ['a', 'b', 'c', 'd', 'e', 'f', 'g'].map((id, n) => coupon(`i-${id}`, `${(n + 1) * 100}.00`)) };
    const days = Array.from({ length: 35 }, (_, n) => ({
      ...first!,
      date: shiftDay('2026-11-02', n),
      movements: [],
      cash: `${n * 1000}.00`,
    }));
    api.ladder.mockResolvedValue(ladder({ today: '2026-11-10', days: [...days.slice(0, 8), { ...busy, date: '2026-11-10' }, ...days.slice(9)] }));
    renderBooking(api, '/booking/plan');

    const rows = within(await screen.findByRole('table', { name: 'План по расчётам' })).getAllByRole('row');
    const today = rows.find((r) => r.classList.contains('next'))!;
    const items = within(today).getAllByRole('listitem');
    expect(items).toHaveLength(6);
    expect(items[0]).toHaveTextContent('купонi-g700,00');
    expect(items[5]).toHaveTextContent('ещё 2 движения300,00');
    // 35 дней — подписаны только понедельники и сегодня.
    const labels = within(screen.getByRole('group', { name: 'Деньги на конец дня' }))
      .getAllByRole('button')
      .map((c) => c.querySelector('.plan-col-label')?.textContent)
      .filter(Boolean);
    expect(labels).toEqual(['02', '09', '10', '16', '23', '30']);
  });

  it('opens a REPO day with the commission and names a one-day shortfall', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    const base = ladder();
    const [first] = base.days;
    const leg = { ...first!.movements[0]!, instrument_id: null, quantity: 0, repo_term: 1 };
    api.ladder.mockResolvedValue(
      ladder({
        days: [
          {
            ...first!,
            cash: '-5.00',
            securities: [{ instrument_id: 'i-ofz', quantity: -1 }],
            movements: [
              { ...leg, kind: 'repo_first', trade_id: 'r-1', cash: '-8200000.00' },
              { ...leg, kind: 'fee', trade_id: 'r-1', cash: '-56.16' },
            ],
          },
        ],
      }),
    );
    renderBooking(api, '/booking/plan');

    const rows = within(await screen.findByRole('table', { name: 'План по расчётам' })).getAllByRole('row');
    expect(rows[1]).toHaveTextContent('РЕПОовернайт · первая частькомиссия 56,168 200 056,16');
    expect(tiles().slice(2)).toEqual(['Дефицит денег: −5 ₽30.11', 'Дефицит бумаг: 1 бумагаОФЗ 26238 с 30.11']);
    await user.click(rows[1]!);
    expect(screen.getByRole('region', { name: 'РЕПО' })).toHaveTextContent(
      'овернайт · первая часть · комиссия 56,16−8 200 056,16' +
        'OPIF1-BONDS · РЕПО: первая часть · сделка−8 200 000,00OPIF1-BONDS · комиссия · сделка−56,16',
    );
  });

  it('folds quiet days of a lasting shortfall into one red row', async () => {
    const api = fakeApi();
    const base = ladder();
    const [first] = base.days;
    const short = { cash: '-5.00', securities: [{ instrument_id: 'i-ofz', quantity: -1 }] };
    api.ladder.mockResolvedValue(
      ladder({
        days: [
          { ...first!, ...short },
          { ...first!, ...short, date: '2026-12-01', movements: [] },
          { ...first!, ...short, date: '2026-12-02', movements: [] },
        ],
      }),
    );
    renderBooking(api, '/booking/plan');

    const rows = within(await screen.findByRole('table', { name: 'План по расчётам' })).getAllByRole('row');
    expect(rows).toHaveLength(3);
    expect(rows[2]).toHaveTextContent('01.12–02.12 · без движений−5,00ОФЗ 26238 → не хватает 1 шт.');
    expect(rows[2]).toHaveClass('plan-quiet', 'deficit');
    expect(within(rows[2]!).getAllByRole('cell')[1]).toHaveClass('danger');
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
    expect(tiles()).toEqual(['Деньги сегодня: —', 'Можно разместить до —: —', 'Дефицит денег: нет', 'Дефицит бумаг: нет']);
    expect(screen.queryByRole('group', { name: 'Деньги на конец дня' })).not.toBeInTheDocument();
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
    const away = { ...transfer, kind: 'transfer_out' as const, quantity: -40, portfolio_code: 'OPIF1-TREASURY' };
    api.ladder.mockResolvedValue(
      ladder({ days: [{ ...first!, movements: [transfer, away], securities: [{ instrument_id: 'i-ofz', quantity: 60 }] }] }),
    );
    renderBooking(api, '/booking/plan');

    const rows = within(await screen.findByRole('table', { name: 'План по расчётам' })).getAllByRole('row');
    expect(rows[1]).toHaveTextContent('30.11 пнсегодня——31 999 500,00ОФЗ 26238 +60 → 60 шт.');
    expect(within(rows[1]!).queryByText(/перевод/)).not.toBeInTheDocument();
    await userEvent.setup().click(rows[1]!);
    expect(screen.getByRole('region', { name: 'Без денег' })).toHaveTextContent(
      'OPIF1-BONDS · перевод: пришло · ОФЗ 26238+100 шт.OPIF1-TREASURY · перевод: ушло · ОФЗ 26238−40 шт.',
    );
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

    const rows = within(await screen.findByRole('table', { name: 'План по расчётам' })).getAllByRole('row');
    expect(rows[1]).toHaveTextContent('купон, прогнозГазпнф3P8RКС 14% + 1,3%, 12,58 ₽ на бумагу62 900,00');
    expect(rows[2]).toHaveTextContent(
      'купон, прогнозГазпнф3P8RRUONIA 13,9% − 0,2%, с капитализацией, 8,49 ₽ на бумагу42 450,00',
    );
    expect(screen.queryByText(/Сумма купона не объявлена/)).not.toBeInTheDocument();
  });
});
