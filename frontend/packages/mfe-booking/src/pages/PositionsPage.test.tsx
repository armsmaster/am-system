import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Line, Positions } from '../api';
import { ATTRIBUTES, dayResult, fakeApi, positions, renderBooking } from '../test/fixtures';

const [OFZ] = positions().lines as [Line];
const BLANK = { result: '0.00', realized: '0.00' };

/** Флоатер банка с рейтингом выпуска, линкер без эмитента и бумага без расчёта и справочника. */
const FLOATER: Line = {
  ...OFZ,
  instrument_id: 'i-flt',
  secid: 'RU000A10FLT1',
  short_name: 'Банк Флоат',
  bond_type: 'floater',
  yield_percent: null,
  yield_continuous: null,
  spread_bp: '81.51',
  spread_continuous_bp: '70.68',
  horizon_kind: 'offer',
  horizon_date: '2027-10-14',
  metrics_basis: 'spread',
};
const LINKER: Line = { ...OFZ, instrument_id: 'i-lnk', secid: 'RU000A10LNK1', short_name: 'Линкер', bond_type: null };
const BARE: Line = {
  ...OFZ,
  instrument_id: 'i-bare',
  secid: 'RU000A10BAR1',
  short_name: 'Без расчёта',
  bond_type: null,
  yield_percent: null,
  yield_continuous: null,
  dv01: null,
  dv01_continuous: null,
  duration: null,
  duration_continuous: null,
  average_price: null,
  price: null,
  lots: [],
};
const WIDE_ATTRIBUTES = {
  ...ATTRIBUTES,
  instruments: [
    ...ATTRIBUTES.instruments,
    {
      id: 'i-flt',
      issuer_id: 'e-bank',
      coupon_kind: 'floater' as const,
      nominal_indexation: 'none' as const,
      classes: {},
      ratings: {
        status: 'rated' as const,
        ratings: [{ agency: 'expert', agency_name: 'Эксперт РА', rating: 'ruA+', grade: 'A+', since: '2025-01-01' }],
      },
    },
    {
      id: 'i-lnk',
      issuer_id: null,
      coupon_kind: 'fixed' as const,
      nominal_indexation: 'ruonia' as const,
      classes: {},
      ratings: { status: 'unrated' as const, ratings: [] },
    },
  ],
  issuers: [
    ...ATTRIBUTES.issuers,
    { id: 'e-bank', name: 'Банк', classes: { 'c-sector': 'v-fin' }, ratings: { status: 'unknown' as const, ratings: [] } },
  ],
};

function rowsOf(name: string) {
  return within(screen.getByRole('table', { name })).getAllByRole('row');
}

describe('PositionsPage (decision 164)', () => {
  beforeEach(() => {
    localStorage.clear();
    // Сегодня — 03.12.2026: кнопка следующего дня видна для 02.12 и скрыта для 03.12.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-12-03T09:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('shows bonds with yields, ratings, sectors and results by period', async () => {
    const api = renderBooking(fakeApi(), '/booking/positions?portfolio=p-bonds&date=2026-12-02');

    const table = await screen.findByRole('table', { name: 'Облигации' });
    expect(api.positions).toHaveBeenCalledWith('p-bonds', '2026-12-02');
    expect(api.attributes).toHaveBeenCalledWith('2026-12-02');
    const [head, group, row] = within(table).getAllByRole('row');
    expect(head).toHaveTextContent(
      'БумагаКол-во, шт.ЦенаСтоимость, ₽ДоходностьнепрерывнаяРезультатДеньРезультатНеделяРезультатС нач. мес.' +
        'РезультатС нач. годаРеализованныйС нач. годаРейтингСектор',
    );
    expect(group).toHaveTextContent('С фиксированным купоном · 1 бумага15 810 000+41 700+101 400+35 400+101 400+20 000');
    expect(row).toHaveTextContent(
      'ОФЗ 26238SU26238RMFS4Минфин России30 00052,7%15 810 00014,79%к погаш. 15.05.2041' +
        '+41 700+101 400+35 400+101 400+20 000AAA(RU)АКРА · эмитентаГосударство',
    );
    expect(screen.getByLabelText('Итоги портфеля')).toHaveTextContent(
      'Стоимость60,1 млн ₽Деньги−15,7 млн ₽Результат дня+41 700 ₽С начала года+101 400 ₽по бумагам' +
        'DV0111 337 ₽за 1 бп, непрерывная',
    );
    expect(screen.getByText('1 бумага')).toBeInTheDocument();
    // Результат дня по частям — под плитками, части в сумме дают результат.
    const result = screen.getByLabelText('Стоимость и результат дня');
    expect(result).toHaveTextContent('стоимость портфеля60 103 400,00 ₽результат дня41 700,00 ₽переоценка36 000,00 ₽');
    expect(result).toHaveTextContent('корректировка комиссий при выплате0,00 ₽');
    expect(result).not.toHaveTextContent('приток');
    expect(screen.queryByText('Проданные в периоде')).not.toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'Паи фондов' })).not.toBeInTheDocument();
    expect(rowsOf('Движения дня')[1]).toHaveTextContent('OPIF1-BONDSкупонSU26238RMFS4—1 062 000,00');
  });

  it('switches the yield format and remembers it together with the calculator', async () => {
    const user = userEvent.setup();
    renderBooking(fakeApi(), '/booking/positions?portfolio=p-bonds&date=2026-12-02');
    await screen.findByRole('table', { name: 'Облигации' });

    await user.click(within(screen.getByRole('group', { name: 'Доходность' })).getByRole('button', { name: 'Годовая' }));
    expect(rowsOf('Облигации')[0]).toHaveTextContent('Доходностьгодовая');
    expect(rowsOf('Облигации')[2]).toHaveTextContent('15,94%к погаш. 15.05.2041');
    expect(screen.getByLabelText('Итоги портфеля')).toHaveTextContent('DV019 780 ₽за 1 бп, годовая');
    expect(localStorage.getItem('am.marketdata.measure')).toBe('annual');
  });

  it('groups bonds by type, classifier, issuer or not at all and remembers the choice', async () => {
    localStorage.setItem(
      'am.booking.positions.columns',
      JSON.stringify({ columns: ['value', 'yield', 'duration', 'dv01', 'rating', 'type', 'class:c-rank'], custom: null }),
    );
    const api = fakeApi();
    api.positions.mockResolvedValue(positions({ lines: [OFZ, FLOATER, LINKER, BARE] }));
    api.attributes.mockResolvedValue(WIDE_ATTRIBUTES);
    const user = userEvent.setup();
    renderBooking(api, '/booking/positions?portfolio=p-bonds&date=2026-12-02');
    await screen.findByRole('table', { name: 'Облигации' });

    const groups = () => [...document.querySelectorAll('tr.group-row td:first-child')].map((td) => td.textContent);
    expect(groups()).toEqual([
      'С фиксированным купоном · 1 бумага',
      'Флоатеры · 1 бумага',
      'Линкеры · 1 бумага',
      'Тип не определён · 1 бумага',
    ]);
    const rows = rowsOf('Облигации');
    expect(rows[2]).toHaveTextContent('15 810 00014,79%к погаш. 15.05.20416,8211 337AAA(RU)АКРА · эмитентафикс. купонСтарший');
    expect(rows[4]).toHaveTextContent('Банк ФлоатRU000A10FLT1Банк15 810 000спред 71 бпк оферте 14.10.2027');
    expect(rows[4]).toHaveTextContent('ruA+Эксперт РАфлоатер—');
    expect(rows[6]).toHaveTextContent('Линкер');
    expect(rows[6]).toHaveTextContent('нет рейтингалинкер');
    expect(rows[8]).toHaveTextContent('Без расчётаRU000A10BAR115 810 000———нет данных—');
    // Итог группы без DV01 бумаги — ноль.
    expect(rows[7]).toHaveTextContent('Тип не определён · 1 бумага15 810 0000');

    const grouping = within(screen.getByRole('group', { name: 'Группировка' }));
    await user.click(grouping.getByRole('button', { name: 'Сектор' }));
    expect(groups()).toEqual(['Государство · 1 бумага', 'Финансы · 1 бумага', 'Без значения · 2 бумаги']);
    await user.click(grouping.getByRole('button', { name: 'Эмитент' }));
    expect(groups()).toEqual(['Банк · 1 бумага', 'Минфин России · 1 бумага', 'Эмитент не указан · 2 бумаги']);
    await user.click(grouping.getByRole('button', { name: 'Очередность' }));
    expect(groups()).toEqual(['Старший · 1 бумага', 'Без значения · 3 бумаги']);
    await user.click(grouping.getByRole('button', { name: 'Без групп' }));
    expect(groups()).toEqual([]);
    expect(localStorage.getItem('am.booking.positions.group')).toBe('none');

    // Бумага без справочника и лотов — панель без эмитента, доходности и лотов.
    await user.click(screen.getByRole('button', { name: 'Действия: Без расчёта' }));
    await user.click(screen.getByRole('menuitem', { name: 'Лоты и оценка' }));
    const panel = within(screen.getByRole('dialog', { name: 'Лоты и оценка' }));
    expect(panel.getByLabelText('Оценка RU000A10BAR1')).toHaveTextContent('средняя цена покупки—');
    expect(panel.getByLabelText('Оценка RU000A10BAR1')).toHaveTextContent('цена оценки—');
    expect(panel.getByLabelText('Оценка RU000A10BAR1')).not.toHaveTextContent('доходность');
    expect(panel.getByText('Открытых лотов нет.')).toBeInTheDocument();
    expect(panel.queryByText('Банк')).not.toBeInTheDocument();
  });

  it('falls back to the type when the remembered classifier is gone', async () => {
    localStorage.setItem('am.booking.positions.group', 'class:gone');
    renderBooking(fakeApi(), '/booking/positions?portfolio=p-bonds&date=2026-12-02');
    await screen.findByRole('table', { name: 'Облигации' });

    const grouping = within(screen.getByRole('group', { name: 'Группировка' }));
    expect(grouping.getByRole('button', { name: 'Тип' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('chooses columns: presets, toggles, the period matrix, order and reset', async () => {
    const user = userEvent.setup();
    renderBooking(fakeApi(), '/booking/positions?portfolio=p-bonds&date=2026-12-02');
    await screen.findByRole('table', { name: 'Облигации' });
    const header = () => rowsOf('Облигации')[0];

    await user.click(screen.getByRole('button', { name: 'Колонки' }));
    const panel = within(screen.getByRole('dialog', { name: 'Колонки таблицы' }));
    const presets = within(panel.getByRole('group', { name: 'Готовые наборы' }));
    expect(presets.getByRole('button', { name: 'Мой' })).toHaveAttribute('aria-pressed', 'true');

    await user.click(presets.getByRole('button', { name: 'Компактно' }));
    expect(header()).toHaveTextContent('БумагаСтоимость, ₽ДоходностьнепрерывнаяРезультатДеньРезультатС нач. года');
    expect(presets.getByRole('button', { name: 'Компактно' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(presets.getByRole('button', { name: 'Риск' }));
    expect(header()).toHaveTextContent('ДюрациянепрерывнаяDV01, ₽непрерывнаяРейтингСекторОчередность');
    await user.click(presets.getByRole('button', { name: 'Результат' }));
    expect(header()).toHaveTextContent('Нереализ., ₽');
    await user.click(presets.getByRole('button', { name: 'Мой' }));
    expect(header()).toHaveTextContent('Кол-во, шт.ЦенаСтоимость, ₽');

    // Включённая колонка встаёт на своё место; матрица — результат и реализованный по периодам.
    await user.click(within(panel.getByRole('group', { name: 'Позиция' })).getByRole('button', { name: 'Нереализованный' }));
    await user.click(panel.getByRole('button', { name: 'Реализованный: неделя' }));
    await user.click(panel.getByRole('button', { name: 'Результат: день' }));
    await user.click(within(panel.getByRole('group', { name: 'Риск и справочник' })).getByRole('button', { name: 'Тип облигации' }));
    const order = within(panel.getByRole('list', { name: 'Порядок колонок' }));
    expect(order.getAllByRole('listitem').map((li) => li.textContent?.replace(/[↑↓]/g, ''))).toEqual([
      'Количество',
      'Цена',
      'Стоимость',
      'Нереализованный',
      'Доходность / спред',
      'Результат: неделя',
      'Результат: с нач. мес.',
      'Результат: с нач. года',
      'Реализованный: неделя',
      'Реализованный: с нач. года',
      'Рейтинг',
      'Тип облигации',
      'Сектор',
    ]);
    expect(order.getByRole('button', { name: 'Левее: Количество' })).toBeDisabled();
    expect(order.getByRole('button', { name: 'Правее: Сектор' })).toBeDisabled();
    await user.click(order.getByRole('button', { name: 'Правее: Количество' }));
    await user.click(order.getByRole('button', { name: 'Левее: Сектор' }));
    expect(header()).toHaveTextContent('БумагаЦенаКол-во, шт.Стоимость, ₽Нереализ., ₽');
    expect(header()).toHaveTextContent('РейтингСекторТип');
    expect(rowsOf('Облигации')[2]).toHaveTextContent('+60 000');
    expect(presets.getByRole('button', { name: 'Мой' })).toHaveAttribute('aria-pressed', 'true');
    const stored = JSON.parse(localStorage.getItem('am.booking.positions.columns') ?? '{}') as { custom: string[] };
    expect(stored.custom.slice(0, 2)).toEqual(['price', 'quantity']);

    await user.click(panel.getByRole('button', { name: 'Вернуть по умолчанию' }));
    expect(localStorage.getItem('am.booking.positions.columns')).toBe('{"columns":null,"custom":null}');
    expect(header()).toHaveTextContent('БумагаКол-во, шт.ЦенаСтоимость, ₽Доходность');

    // Все колонки выключены — в таблице одни бумаги.
    await user.click(presets.getByRole('button', { name: 'Компактно' }));
    for (const name of ['Стоимость', 'Доходность / спред']) {
      await user.click(panel.getByRole('button', { name }));
    }
    await user.click(panel.getByRole('button', { name: 'Результат: день' }));
    await user.click(panel.getByRole('button', { name: 'Результат: с нач. года' }));
    expect(panel.getByText(/Колонки не выбраны/)).toBeInTheDocument();
    expect(header()).toHaveTextContent(/^Бумага$/);
    await user.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(screen.queryByRole('dialog', { name: 'Колонки таблицы' })).not.toBeInTheDocument();
  });

  it('shows depot quantity, accrued interest and the annual duration from the remembered choice', async () => {
    localStorage.setItem('am.marketdata.measure', 'annual');
    localStorage.setItem('am.booking.positions.columns', JSON.stringify({ columns: ['settle_quantity', 'accrued', 'duration'], custom: null }));
    const api = fakeApi();
    api.positions.mockResolvedValue(positions({ lines: [{ ...OFZ, settle_quantity: 20000, accrued_value: '35010.00' }] }));
    renderBooking(api, '/booking/positions?portfolio=p-bonds&date=2026-12-02');

    const [head, group, row] = within(await screen.findByRole('table', { name: 'Облигации' })).getAllByRole('row');
    expect(head).toHaveTextContent('БумагаНа счёте депо, шт.НКД, ₽Дюрациягодовая');
    expect(group).toHaveTextContent(/1 бумага35 010$/);
    expect(row).toHaveTextContent(/20 00035 0105,89⋯$/);
  });

  it('changes the font size and remembers it', async () => {
    const user = userEvent.setup();
    renderBooking(fakeApi(), '/booking/positions?portfolio=p-bonds&date=2026-12-02');
    await screen.findByRole('table', { name: 'Облигации' });

    await user.click(within(screen.getByRole('group', { name: 'Размер шрифта' })).getByRole('button', { name: 'A−' }));
    expect(document.querySelector('.positions')).toHaveClass('size-small');
    expect(localStorage.getItem('am.booking.positions.size')).toBe('small');
  });

  it('works when the browser storage is unavailable', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('private mode');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('private mode');
    });
    const user = userEvent.setup();
    renderBooking(fakeApi(), '/booking/positions?portfolio=p-bonds&date=2026-12-02');
    await screen.findByRole('table', { name: 'Облигации' });

    await user.click(within(screen.getByRole('group', { name: 'Размер шрифта' })).getByRole('button', { name: 'A+' }));
    expect(document.querySelector('.positions')).toHaveClass('size-large');
  });

  it('opens lots and the valuation from the row menu', async () => {
    const user = userEvent.setup();
    renderBooking(fakeApi(), '/booking/positions?portfolio=p-bonds&date=2026-12-02');
    await screen.findByRole('table', { name: 'Облигации' });
    const menuButton = screen.getByRole('button', { name: 'Действия: ОФЗ 26238' });

    // Меню закрывается щелчком мимо и Escape; другие клавиши его не трогают.
    await user.click(menuButton);
    expect(menuButton).toHaveAttribute('aria-expanded', 'true');
    fireEvent.keyDown(document, { key: 'a' });
    expect(screen.getByRole('menu')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    await user.click(menuButton);
    fireEvent.click(document.querySelector('.menu-scrim')!);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    await user.click(menuButton);
    await user.click(menuButton);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    // Прокрутка уводит строку от меню — меню закрывается.
    await user.click(menuButton);
    fireEvent.scroll(window);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();

    await user.click(menuButton);
    await user.click(screen.getByRole('menuitem', { name: 'Лоты и оценка' }));
    const panel = within(screen.getByRole('dialog', { name: 'Лоты и оценка' }));
    expect(panel.getByText('Минфин России')).toBeInTheDocument();
    const facts = panel.getByLabelText('Оценка SU26238RMFS4');
    expect(facts).toHaveTextContent(
      'по дате сделки, шт.30 000на счёте депо, шт.30 000средняя цена покупки52,5%стоимость покупки, ₽15 750 000,00' +
        'уплаченный НКД, ₽1 050 300,00цена оценки52,7%',
    );
    expect(facts).toHaveTextContent(
      'результат дня, ₽41 700,00переоценка36 000,00реализованный0,00купонный доход5 700,00комиссии0,00',
    );
    expect(facts).toHaveTextContent('доходность, непрерывная14,79%к погаш. 15.05.2041');
    expect(facts).toHaveTextContent('модифицированная дюрация5,89');
    const results = within(panel.getByRole('table', { name: 'Результат SU26238RMFS4' })).getAllByRole('row');
    expect(results[1]).toHaveTextContent('Результат41 700,00101 400,00101 400,0035 400,00101 400,00101 400,00');
    expect(results[2]).toHaveTextContent('Реализованный0,0020 000,0020 000,000,0020 000,0020 000,00');
    expect(within(panel.getByRole('table', { name: 'Лоты SU26238RMFS4' })).getAllByRole('row')[1]).toHaveTextContent(
      'OPIF1-BONDS27.11, 11:4230 00052,5%15 750 000,00',
    );
    await user.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(screen.queryByRole('dialog', { name: 'Лоты и оценка' })).not.toBeInTheDocument();
  });

  it('moves between trading days and to the ends of the previous month and quarter', async () => {
    const api = fakeApi();
    // Ответ — на запрошенный день; 30.11 — календарь недоступен, соседние дни — будни.
    api.positions.mockImplementation((...args: unknown[]) => {
      const date = args[1] as string;
      const neighbours: Partial<Positions> =
        date === '2026-11-30' ? { previous_day: null, next_day: null } : { previous_day: '2026-12-01', next_day: '2026-12-04' };
      return Promise.resolve(positions({ date, ...neighbours }));
    });
    const user = userEvent.setup();
    renderBooking(api, '/booking/positions?portfolio=p-bonds&date=2026-12-02');
    await screen.findByRole('table', { name: 'Облигации' });

    const previous = screen.getByRole('button', { name: 'Предыдущий торговый день, 01.12.2026' });
    expect(previous).toHaveTextContent('‹ пред.вт 01.12');
    expect(screen.queryByRole('button', { name: /Следующий торговый день/ })).not.toBeInTheDocument();
    await user.click(previous);
    await waitFor(() => expect(api.positions).toHaveBeenLastCalledWith('p-bonds', '2026-12-01'));

    const quick = within(screen.getByRole('group', { name: 'Быстрые даты' }));
    expect(quick.getAllByRole('button').map((b) => b.textContent)).toEqual(['Сегодня', 'Конец ноября', 'Конец III кв.']);
    await user.click(quick.getByRole('button', { name: 'Конец ноября' }));
    await waitFor(() => expect(api.positions).toHaveBeenLastCalledWith('p-bonds', '2026-11-30'));
    expect(quick.getByRole('button', { name: 'Конец ноября' })).toHaveAttribute('aria-pressed', 'true');
    // Календаря нет — соседние будни: пятница и вторник.
    expect(await screen.findByRole('button', { name: 'Предыдущий торговый день, 27.11.2026' })).toHaveTextContent('пт 27.11');
    await user.click(screen.getByRole('button', { name: 'Следующий торговый день, 01.12.2026' }));
    await waitFor(() => expect(api.positions).toHaveBeenLastCalledWith('p-bonds', '2026-12-01'));
    await user.click(quick.getByRole('button', { name: 'Конец III кв.' }));
    await waitFor(() => expect(api.positions).toHaveBeenLastCalledWith('p-bonds', '2026-09-30'));
    await user.click(quick.getByRole('button', { name: 'Сегодня' }));
    await waitFor(() => expect(api.positions).toHaveBeenLastCalledWith('p-bonds', '2026-12-03'));

    // Своя дата: ни одна быстрая не нажата; стёртая дата ничего не меняет.
    fireEvent.change(screen.getByLabelText('Дата'), { target: { value: '2026-11-15' } });
    await waitFor(() => expect(api.positions).toHaveBeenLastCalledWith('p-bonds', '2026-11-15'));
    expect(quick.getAllByRole('button').every((b) => b.getAttribute('aria-pressed') === 'false')).toBe(true);
    fireEvent.change(screen.getByLabelText('Дата'), { target: { value: '' } });
    expect(api.positions).toHaveBeenLastCalledWith('p-bonds', '2026-11-15');
    expect(screen.getByRole('status', { name: 'Адрес' })).toHaveTextContent('portfolio=p-bonds&date=2026-11-15');
  });

  it('keeps the portfolio in the address and names the portfolios of a compound', async () => {
    const api = fakeApi();
    const [state] = positions().portfolios;
    api.positions.mockResolvedValue(positions({ portfolios: [state!, { ...state!, id: 'p-treasury', code: 'OPIF1-TREASURY' }] }));
    const user = userEvent.setup();
    renderBooking(api, '/booking/positions');
    await screen.findByRole('table', { name: 'Облигации' });

    expect(api.positions).toHaveBeenLastCalledWith('p-fund', '2026-12-03');
    expect(rowsOf('Облигации')[2]).toHaveTextContent('Минфин России · OPIF1-BONDS');
    await user.selectOptions(screen.getByRole('combobox', { name: 'Портфель' }), 'p-bonds');
    await waitFor(() => expect(api.positions).toHaveBeenLastCalledWith('p-bonds', '2026-12-03'));
  });

  it('shows fund shares and bonds sold in the period in their own rows', async () => {
    const api = fakeApi();
    const share: Line = { ...BARE, instrument_id: 'i-lqdt', kind: 'fund_share', secid: 'LQDT', short_name: 'ВИМ Ликвидность', price: '1.5' };
    const [state] = positions().portfolios;
    api.positions.mockResolvedValue(
      positions({
        portfolios: [{ ...state!, movements: [] }],
        lines: [OFZ, share],
        closed: {
          week: BLANK,
          days30: { result: '4900.00', realized: '4900.00' },
          month: { result: '4900.00', realized: '4900.00' },
          quarter: { result: '9536.00', realized: '4900.00' },
          year: { result: '9536.00', realized: '4900.00' },
        },
      }),
    );
    renderBooking(api, '/booking/positions?portfolio=p-bonds&date=2026-12-02');
    await screen.findByRole('table', { name: 'Облигации' });

    expect(rowsOf('Облигации')[3]).toHaveTextContent(/^Проданные в периоде0\+4 900\+9 536\+4 900$/);
    expect(rowsOf('Паи фондов')[1]).toHaveTextContent('ВИМ ЛиквидностьLQDT30 0001,5 ₽15 810 000—+41 700');
    expect(rowsOf('Паи фондов')[1]).toHaveTextContent('+20 000——');
    expect(screen.getByLabelText('Итоги портфеля')).toHaveTextContent('С начала года+212 336 ₽');
    expect(screen.getByLabelText('Итоги портфеля')).toHaveTextContent('DV0111 337 ₽');
    expect(screen.queryByRole('table', { name: 'Движения дня' })).not.toBeInTheDocument();

    // Пай — в рублях и в панели лотов.
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Действия: ВИМ Ликвидность' }));
    await user.click(screen.getByRole('menuitem', { name: 'Лоты и оценка' }));
    expect(screen.getByLabelText('Оценка LQDT')).toHaveTextContent('цена оценки1,5 ₽');
  });

  it('keeps the bonds block for bonds sold out in the period', async () => {
    const api = fakeApi();
    api.positions.mockResolvedValue(
      positions({
        lines: [],
        closed: { week: BLANK, days30: BLANK, month: BLANK, quarter: BLANK, year: { result: '0.00', realized: '-1.00' } },
      }),
    );
    renderBooking(api, '/booking/positions?portfolio=p-bonds&date=2026-12-02');

    expect(await screen.findByText('0 бумаг')).toBeInTheDocument();
    expect(rowsOf('Облигации')[1]).toHaveTextContent(/^Проданные в периоде000−1$/);
    expect(screen.getByText('Бумаг нет.')).toBeInTheDocument();
  });

  it('hides the sold-in-period row when no period column is shown', async () => {
    localStorage.setItem('am.booking.positions.columns', JSON.stringify({ columns: ['value'], custom: null }));
    const api = fakeApi();
    api.positions.mockResolvedValue(
      positions({ closed: { week: BLANK, days30: BLANK, month: BLANK, quarter: BLANK, year: { result: '5.00', realized: '5.00' } } }),
    );
    renderBooking(api, '/booking/positions?portfolio=p-bonds&date=2026-12-02');

    await screen.findByRole('table', { name: 'Облигации' });
    expect(screen.queryByText('Проданные в периоде')).not.toBeInTheDocument();
  });

  it('shows positions without reference data', async () => {
    const api = fakeApi();
    api.attributes.mockRejectedValue(new Error('refdata down'));
    api.ratingScale.mockRejectedValue(new Error('refdata down'));
    renderBooking(api, '/booking/positions?portfolio=p-bonds&date=2026-12-02');

    const table = await screen.findByRole('table', { name: 'Облигации' });
    expect(within(table).getAllByRole('row')[0]).not.toHaveTextContent('Сектор');
    expect(within(table).getAllByRole('row')[2]).toHaveTextContent('нет данных');
    expect(within(screen.getByRole('group', { name: 'Группировка' })).getAllByRole('button')).toHaveLength(3);
  });

  it('explains stale and missing calculations', async () => {
    const api = fakeApi();
    const [state] = positions().portfolios;
    api.positions.mockResolvedValue(
      positions({
        lines: [],
        result: null,
        portfolios: [
          { ...state!, code: 'A', waiting_since: '2026-11-30' },
          { ...state!, code: 'B', as_of: null, movements: [] },
          { ...state!, code: 'C', as_of: '2026-12-01', movements: [] },
          {
            ...state!,
            id: 'x',
            code: 'D',
            movements: [
              { kind: 'settlement', instrument_id: 'gone', trade_id: 't', operation_id: null, quantity: -20000, cash: '11224200.00' },
              { kind: 'transfer_in', instrument_id: null, trade_id: null, operation_id: 'o', quantity: 0, cash: '60000000.00' },
            ],
          },
        ],
      }),
    );
    renderBooking(api, '/booking/positions?portfolio=p-fund&date=2026-12-02');

    expect(await screen.findByText(/A: пересчитывается с 30.11.2026/)).toBeInTheDocument();
    expect(screen.getByText(/B: расчёта ещё нет/)).toBeInTheDocument();
    expect(screen.getByText('C: последний расчёт — на 01.12.2026.')).toBeInTheDocument();
    expect(screen.getByText('Бумаг нет.')).toBeInTheDocument();
    expect(screen.getByLabelText('Итоги портфеля')).toHaveTextContent('Стоимость—Деньги−15,7 млн ₽Результат дня—С начала года0 ₽');
    const moves = rowsOf('Движения дня');
    expect(moves[2]).toHaveTextContent('Dрасчёты по сделкеgone−20 00011 224 200,00');
    expect(moves[3]).toHaveTextContent('Dперевод: пришло——60 000 000,00');
  });

  it('marks an incomplete valuation and shows flows and unsettled trades', async () => {
    const api = fakeApi();
    api.positions.mockResolvedValue(
      positions({
        result: dayResult({
          total: '-6300.00',
          flows: '60000000.00',
          receivable: '11224200.00',
          payable: '28000500.00',
          flags: ['no_price', 'stale_price'],
        }),
        lines: [
          {
            ...FLOATER,
            price: null,
            coupon_due: '1062000.00',
            principal_due: '250000.00',
            flags: ['stale_price', 'coupon_unknown', 'principal_unknown', 'due_estimated'],
            duration: '0.41',
          },
        ],
      }),
    );
    const user = userEvent.setup();
    renderBooking(api, '/booking/positions?portfolio=p-bonds&date=2026-12-02');

    const result = await screen.findByLabelText('Стоимость и результат дня');
    expect(result).toHaveTextContent('приток: паи и переводы60 000 000,00 ₽');
    expect(result).toHaveTextContent('к получению по сделкам до расчётов11 224 200,00 ₽');
    expect(result).toHaveTextContent('к уплате по сделкам до расчётов28 000 500,00 ₽');
    expect(screen.getByLabelText('Итоги портфеля')).toHaveTextContent('Результат дня−6 300 ₽');
    expect(screen.getByText(/Оценка неполная/)).toHaveTextContent(
      'Оценка неполная: цены не было — по цене покупки; цена не за этот день.',
    );
    expect(rowsOf('Облигации')[2]).toHaveTextContent('30 000— *');
    await user.click(screen.getByRole('button', { name: 'Действия: Банк Флоат' }));
    await user.click(screen.getByRole('menuitem', { name: 'Лоты и оценка' }));
    const details = screen.getByLabelText('Оценка RU000A10FLT1');
    expect(details).toHaveTextContent('купоны к получению, ₽1 062 000,00');
    expect(details).toHaveTextContent('погашение номинала к получению, ₽250 000,00');
    expect(details).toHaveTextContent('доходность, непрерывнаяспред 71 бпк оферте 14.10.2027');
    expect(details).toHaveTextContent('спред-дюрация0,41');
    expect(details).toHaveTextContent(
      'оценкацена не за этот день; сумма купона к получению не объявлена; сумма погашения к получению не объявлена; ' +
        'купон или погашение линкера посчитаны по условиям выпуска — ISS подтвердит сумму в день выплаты',
    );
  });

  it('notes a linker coupon by the issue terms calmly, apart from an incomplete valuation', async () => {
    const api = fakeApi();
    api.positions.mockResolvedValue(
      positions({ result: dayResult({ flags: ['due_estimated'] }), lines: [{ ...OFZ, flags: ['due_estimated'] }] }),
    );
    const user = userEvent.setup();
    renderBooking(api, '/booking/positions?portfolio=p-bonds&date=2026-11-18');

    expect(
      await screen.findByText(
        'Купон или погашение линкера к получению посчитаны по условиям выпуска — ISS подтвердит сумму в день выплаты.',
      ),
    ).toHaveClass('muted');
    expect(screen.queryByText(/Оценка неполная/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Действия: ОФЗ 26238' }));
    await user.click(screen.getByRole('menuitem', { name: 'Лоты и оценка' }));
    const note = within(screen.getByLabelText('Оценка SU26238RMFS4')).getByText(/по условиям выпуска/);
    expect(note).toHaveClass('muted');
  });

  it('shows units and settlements with unit holders of the treasury portfolio', async () => {
    const api = fakeApi();
    api.positions.mockResolvedValue(
      positions({ units: '100000.50000', claims: '1000.00', obligations: '0', expenses: '20000.00', lines: [] }),
    );
    renderBooking(api, '/booking/positions?portfolio=p-treasury&date=2026-11-26');

    const list = await screen.findByLabelText('Паи и расчёты с пайщиками');
    expect(list).toHaveTextContent('паи фонда100 000,5 шт.требования к пайщикам1 000,00 ₽начисленные расходы20 000,00 ₽');
    expect(list).not.toHaveTextContent('обязательства');
  });

  it('shows the MV structure by issuer, type, classifier and rating and filters the table by a group', async () => {
    const api = fakeApi();
    // MV с НКД: короткая ОФЗ −203 544, флоатер 20 000 000, линкер 5 100 000, бумага без расчёта 15 810 000, пай 1 000 000.
    const share: Line = { ...BARE, instrument_id: 'i-lqdt', kind: 'fund_share', secid: 'LQDT', short_name: 'ВИМ Ликвидность', clean: '1000000.00' };
    api.positions.mockResolvedValue(
      positions({
        lines: [
          { ...OFZ, trade_quantity: -400, clean: '-202544.00', accrued_value: '-1000.00' },
          { ...FLOATER, clean: '20000000.00' },
          { ...LINKER, clean: '5000000.00', accrued_value: '100000.00' },
          BARE,
          share,
        ],
      }),
    );
    api.attributes.mockResolvedValue(WIDE_ATTRIBUTES);
    const user = userEvent.setup();
    renderBooking(api, '/booking/positions?portfolio=p-bonds&date=2026-12-02');
    await screen.findByRole('table', { name: 'Облигации' });

    await user.click(within(screen.getByRole('group', { name: 'Вид' })).getByRole('button', { name: 'Структура' }));
    expect(localStorage.getItem('am.booking.positions.view')).toBe('structure');
    expect(screen.queryByRole('group', { name: 'Группировка' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Структура портфеля')).toHaveTextContent(
      'MV с НКД, ₽ · облигации и паи фондов на 02.12.2026 · всего 41,7 млн',
    );
    const bars = (name: string) =>
      within(screen.getByRole('list', { name })).getAllByRole('button').map((b) => b.getAttribute('aria-label'));
    expect(bars('По эмитентам')).toEqual([
      'Банк: 20 000 000,00 ₽, 48%, 1 бумага',
      'Паи фондов: 1 000 000,00 ₽, 2%, 1 бумага',
      'Минфин России: −203 544,00 ₽, −<1%, 1 бумага',
      'Эмитент не указан: 20 910 000,00 ₽, 50%, 2 бумаги',
    ]);
    expect(bars('По типам бумаг')).toEqual([
      'Флоатеры: 20 000 000,00 ₽, 48%, 1 бумага',
      'Линкеры: 5 100 000,00 ₽, 12%, 1 бумага',
      'Паи фондов: 1 000 000,00 ₽, 2%, 1 бумага',
      'С фиксированным купоном: −203 544,00 ₽, −<1%, 1 бумага',
      'Тип не определён: 15 810 000,00 ₽, 38%, 1 бумага',
    ]);
    expect(bars('По классификатору')).toEqual([
      'Финансы: 20 000 000,00 ₽, 48%, 1 бумага',
      'Паи фондов: 1 000 000,00 ₽, 2%, 1 бумага',
      'Государство: −203 544,00 ₽, −<1%, 1 бумага',
      'Без значения: 20 910 000,00 ₽, 50%, 2 бумаги',
    ]);
    expect(bars('По рейтингам')).toEqual([
      'AAA: −203 544,00 ₽, −<1%, 1 бумага',
      'A+: 20 000 000,00 ₽, 48%, 1 бумага',
      'Нет рейтинга: 5 100 000,00 ₽, 12%, 1 бумага',
      'Нет данных: 15 810 000,00 ₽, 38%, 1 бумага',
      'Паи фондов: 1 000 000,00 ₽, 2%, 1 бумага',
    ]);
    // Короткая позиция — влево от нуля.
    const minfin = screen.getAllByRole('button', { name: /^Минфин России/ })[0]!;
    expect(minfin.querySelector('.bar-fill')).toHaveClass('bar-negative');
    expect(minfin.querySelector('.bar-value')).toHaveTextContent('−204 тыс.');

    await user.selectOptions(screen.getByRole('combobox', { name: 'Классификатор' }), 'c-rank');
    expect(bars('По классификатору')[0]).toBe('Паи фондов: 1 000 000,00 ₽, 2%, 1 бумага');
    expect(localStorage.getItem('am.booking.positions.breakdown')).toBe('c-rank');

    // Щелчок по группе — таблица только с её бумагами.
    await user.click(screen.getByRole('button', { name: 'Банк: 20 000 000,00 ₽, 48%, 1 бумага' }));
    expect(screen.getByText(/Только Эмитент: Банк/)).toHaveTextContent('Только Эмитент: Банк — 1 бумага.');
    expect(rowsOf('Облигации')).toHaveLength(3);
    expect(rowsOf('Облигации')[2]).toHaveTextContent('Банк Флоат');
    expect(screen.queryByRole('table', { name: 'Паи фондов' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Показать все' }));
    expect(screen.queryByText(/Только Эмитент/)).not.toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Паи фондов' })).toBeInTheDocument();
  });

  it('opens the remembered structure and explains what it lacks', async () => {
    localStorage.setItem('am.booking.positions.view', 'structure');
    const api = fakeApi();
    api.attributes.mockRejectedValue(new Error('refdata down'));
    renderBooking(api, '/booking/positions?portfolio=p-bonds&date=2026-12-02');

    expect(await screen.findByText(/Классификаторов нет/)).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'По рейтингам' })).toHaveTextContent('Нет данных');
  });

  it('takes the first classifier when no issuer classifier exists and shows an empty structure', async () => {
    localStorage.setItem('am.booking.positions.view', 'structure');
    const api = fakeApi();
    api.attributes.mockResolvedValue({ ...ATTRIBUTES, classifiers: ATTRIBUTES.classifiers.filter((c) => c.target === 'instrument') });
    renderBooking(api, '/booking/positions?portfolio=p-bonds&date=2026-12-02');

    expect(await screen.findByRole('combobox', { name: 'Классификатор' })).toHaveValue('c-rank');
    expect(screen.getByRole('list', { name: 'По классификатору' })).toHaveTextContent('Старший');
  });

  it('says there are no papers in the structure of an empty portfolio', async () => {
    localStorage.setItem('am.booking.positions.view', 'structure');
    const api = fakeApi();
    api.positions.mockResolvedValue(positions({ lines: [] }));
    renderBooking(api, '/booking/positions?portfolio=p-bonds&date=2026-12-02');

    expect(await screen.findByText('Бумаг нет.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Структура портфеля')).not.toBeInTheDocument();
  });

  it('explains when there are no portfolios', async () => {
    const api = fakeApi();
    api.portfolios.mockResolvedValue([]);
    renderBooking(api, '/booking/positions');

    expect(await screen.findByText(/Нет доступных портфелей/)).toBeInTheDocument();
  });
});
