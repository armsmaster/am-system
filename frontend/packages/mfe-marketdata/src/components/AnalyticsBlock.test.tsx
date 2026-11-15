import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { saveFile } from '../download';
import { asApi, fakeApi, fixedAnalytics, floaterAnalytics, ruleViolation, type FakeApi } from '../test/fixtures';
import { AnalyticsBlock } from './AnalyticsBlock';

vi.mock('../download', () => ({ saveFile: vi.fn() }));

const TODAY = '2026-10-28';

function renderBlock(api: FakeApi = fakeApi()) {
  render(<AnalyticsBlock api={asApi(api)} id="i-ofz" today={TODAY} />);
  return api;
}

/** Строки списка «подпись — значение». */
function facts(name: string): string[] {
  return within(screen.getByLabelText(name))
    .getAllByRole('term')
    .map((dt) => `${dt.textContent} ${dt.nextElementSibling?.textContent}`);
}

function values(region: HTMLElement): string[] {
  return within(region)
    .getAllByRole('term')
    .map((dt) => `${dt.textContent} ${dt.nextElementSibling?.textContent}`);
}

describe('AnalyticsBlock', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it('shows the price of the calculation and the yield and risk in continuous compounding', async () => {
    renderBlock();

    expect(await screen.findByRole('heading', { name: 'Доходность и риск' })).toBeInTheDocument();
    expect(screen.getByText('Сейчас · расчёты 29.10.2026')).toBeInTheDocument();
    expect(facts('Цена в расчёте')).toEqual([
      'Чистая цена 52,4930% · VWAP, quik',
      'Номинал 1 000,00 ₽',
      'НКД 28,79 ₽',
      'Грязная цена 553,72 ₽',
    ]);
    // По умолчанию — непрерывная капитализация (решение 156): мод. дюрация равна дюрации Маколея.
    const capitalization = screen.getByRole('group', { name: 'Капитализация' });
    expect(within(capitalization).getByRole('button', { name: 'Непрерывные' })).toHaveAttribute('aria-pressed', 'true');
    const maturity = screen.getByRole('region', { name: 'К погашению 15.05.2041' });
    expect(within(maturity).getByText('14,79%')).toBeInTheDocument();
    expect(values(maturity)).toEqual([
      'Эффективная годовая 15,94%',
      'Дюрация 6,82 г · 2 491 дн.',
      'Мод. дюрация 6,82',
      'DV01 0,3779 ₽',
      'Выпуклость 74,16',
    ]);
  });

  it('switches to effective annual yields and remembers the choice', async () => {
    renderBlock();
    await screen.findByLabelText('Цена в расчёте');

    await userEvent.click(screen.getByRole('button', { name: 'Годовые' }));

    const maturity = screen.getByRole('region', { name: 'К погашению 15.05.2041' });
    expect(within(maturity).getByText('15,94%')).toBeInTheDocument();
    expect(values(maturity)).toEqual([
      'Непрерывная 14,79%',
      'Дюрация 6,82 г · 2 491 дн.',
      'Мод. дюрация 5,89',
      'DV01 0,3260 ₽',
      'Выпуклость 60,25',
    ]);
    expect(localStorage.getItem('am.marketdata.measure')).toBe('annual');
  });

  it('opens with the remembered annual yields', async () => {
    localStorage.setItem('am.marketdata.measure', 'annual');
    renderBlock();

    const maturity = await screen.findByRole('region', { name: 'К погашению 15.05.2041' });
    expect(within(maturity).getByText('15,94%')).toBeInTheDocument();
  });

  it('works without browser storage', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied');
    });
    renderBlock();

    const maturity = await screen.findByRole('region', { name: 'К погашению 15.05.2041' });
    expect(within(maturity).getByText('14,79%')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Годовые' }));
    expect(within(maturity).getByText('15,94%')).toBeInTheDocument();
  });

  it('shows annual yields when the service has no continuous ones', async () => {
    const api = fakeApi();
    const [maturity] = fixedAnalytics().yields;
    api.analytics.mockResolvedValue(fixedAnalytics({ yields: [{ ...maturity!, continuous: null }] }));
    renderBlock(api);

    const region = await screen.findByRole('region', { name: 'К погашению 15.05.2041' });
    expect(within(region).getByText('15,94%')).toBeInTheDocument();
    expect(values(region)[0]).toBe('Дюрация 6,82 г · 2 491 дн.');
  });

  it('explains why a horizon cannot be computed', async () => {
    const api = fakeApi();
    const [maturity] = fixedAnalytics().yields;
    api.analytics.mockResolvedValue(
      fixedAnalytics({
        yields: [
          { ...maturity!, horizon: { kind: 'offer', date: '2027-10-14', price: '100' } },
          {
            ...maturity!,
            yield_percent: null,
            problem: { code: 'coupon_unknown', message: 'Купон 11.04.2028 не объявлен.' },
            continuous: null,
          },
        ],
      }),
    );
    renderBlock(api);

    const maturityBlock = await screen.findByRole('region', { name: 'К погашению 15.05.2041' });
    expect(within(maturityBlock).getByText('Купон 11.04.2028 не объявлен.')).toBeInTheDocument();
    expect(within(maturityBlock).queryByRole('definition')).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'К оферте 14.10.2027' })).toHaveTextContent('14,79%');
  });

  it('breaks down the spread of a floater over the funding estimate', async () => {
    const api = fakeApi();
    api.analytics.mockResolvedValue(floaterAnalytics());
    renderBlock(api);

    expect(await screen.findByRole('heading', { name: 'Спред над фондированием' })).toBeInTheDocument();
    const horizon = screen.getByRole('region', { name: 'К погашению 12.12.2026' });
    expect(within(horizon).getByText('70,7 бп')).toBeInTheDocument();
    expect(
      screen.getByText(/КС \+ 130 бп, без капитализации\. Доходность при плоском прогнозе ставки — 14,60%/),
    ).toBeInTheDocument();
    // Решение 157: RUONIA — ключевая ставка плюс сглаженный базис.
    expect(
      screen.getByText(
        'Фондирование: RUONIA 13,90% = КС 14,00% + базис −10,0 бп → 13,90% непрерывных. ' +
          'Индекс: Ключевая ставка 14,00% на 26.10.2026, MOEX ISS.',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'Базис — DEMA разницы RUONIA и КС с полураспадом полгода, по 27.10.2026. ' +
          'Последняя RUONIA 13,90% на 27.10.2026, cbr.',
      ),
    ).toBeInTheDocument();
    expect(facts('Разложение спреда')).toEqual([
      'Базис ставки 10,0 бп',
      'Капитализация −7,7 бп',
      'Маржа 128,5 бп',
      'Прочее −1,9 бп',
      'Поправка на цену −58,2 бп',
      'Спред-дюрация 0,12 года',
      'DV01 по спреду 0,0120 ₽',
      'Спред в годовых 81,5 бп',
    ]);
    expect(facts('Цена в расчёте')[0]).toBe('Чистая цена 100,0700% · что если: введённая цена');

    await userEvent.click(screen.getByRole('button', { name: 'Годовые' }));
    expect(within(horizon).getByText('81,5 бп')).toBeInTheDocument();
    expect(screen.getByText(/базис −10,0 бп → 14,91% эффективных годовых\./)).toBeInTheDocument();
    expect(facts('Разложение спреда')).toEqual([
      'Базис ставки 11,5 бп',
      'Капитализация −8,9 бп',
      'Маржа 148,6 бп',
      'Прочее −2,2 бп',
      'Поправка на цену −67,5 бп',
      'Спред-дюрация 0,10 года',
      'DV01 по спреду 0,0104 ₽',
      'Спред непрерывный 70,7 бп',
    ]);
  });

  it('does not repeat RUONIA as the index of a RUONIA floater', async () => {
    const api = fakeApi();
    const floater = floaterAnalytics();
    api.analytics.mockResolvedValue({
      ...floater,
      spread: { ...floater.spread!, rate: 'ruonia', capitalized: true, index: null },
    });
    renderBlock(api);

    expect(await screen.findByText(/RUONIA \+ 130 бп, с капитализацией/)).toBeInTheDocument();
    expect(screen.queryByText(/Индекс:/)).not.toBeInTheDocument();
  });

  it('shows the coupon of a linker instead of the margin', async () => {
    const api = fakeApi();
    const floater = floaterAnalytics();
    api.analytics.mockResolvedValue({
      ...floater,
      type: 'linker',
      spread: { ...floater.spread!, rate: null, margin_bp: null, capitalized: null, index: null },
    });
    renderBlock(api);

    expect(await screen.findByText(/линкер: номинал растёт с RUONIA/)).toBeInTheDocument();
    expect(facts('Разложение спреда')[2]).toBe('Купон 128,5 бп');
  });

  it('computes on the end of a chosen day', async () => {
    const api = renderBlock();
    await screen.findByLabelText('Цена в расчёте');
    expect(api.analytics).toHaveBeenLastCalledWith('i-ofz', {}, undefined);
    api.analytics.mockResolvedValue(fixedAnalytics({ trading_day: '2026-09-30', settlement_date: '2026-10-01' }));

    await userEvent.click(screen.getByRole('button', { name: 'Вчера' }));
    expect(api.analytics).toHaveBeenLastCalledWith('i-ofz', {}, '2026-10-27T23:59:59+03:00');
    await userEvent.click(screen.getByRole('button', { name: 'Конец сентября' }));
    expect(api.analytics).toHaveBeenLastCalledWith('i-ofz', {}, '2026-09-30T23:59:59+03:00');
    expect(await screen.findByText('На конец 30.09.2026 · расчёты 01.10.2026')).toBeInTheDocument();
    expect(screen.getByLabelText('Дата расчёта')).toHaveValue('2026-09-30');

    fireEvent.change(screen.getByLabelText('Дата расчёта'), { target: { value: '2026-10-15' } });
    expect(api.analytics).toHaveBeenLastCalledWith('i-ofz', {}, '2026-10-15T23:59:59+03:00');
    // Стёртая дата ничего не меняет.
    fireEvent.change(screen.getByLabelText('Дата расчёта'), { target: { value: '' } });
    expect(api.analytics).toHaveBeenLastCalledWith('i-ofz', {}, '2026-10-15T23:59:59+03:00');

    await userEvent.click(screen.getByRole('button', { name: 'Сегодня' }));
    expect(api.analytics).toHaveBeenLastCalledWith('i-ofz', {}, undefined);
  });

  it('recomputes for an entered yield in the chosen measure and goes back to the valuation price', async () => {
    const user = userEvent.setup();
    const api = renderBlock();
    await screen.findByLabelText('Цена в расчёте');

    const form = within(screen.getByRole('form', { name: 'Что если' }));
    expect(form.getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Чистая цена, %',
      'Доходность, % непрерывная',
    ]);
    await user.selectOptions(form.getByRole('combobox'), 'yield');
    await user.type(form.getByRole('textbox'), '14,8');
    await user.click(form.getByRole('button', { name: 'Посчитать' }));
    await waitFor(() =>
      expect(api.analytics).toHaveBeenLastCalledWith('i-ofz', { yield: '14.8', measure: 'continuous' }, undefined),
    );

    await user.click(await form.findByRole('button', { name: 'Вернуть цену для оценки' }));
    await waitFor(() => expect(api.analytics).toHaveBeenLastCalledWith('i-ofz', {}, undefined));

    await user.click(screen.getByRole('button', { name: 'Годовые' }));
    expect(form.getByRole('option', { name: 'Доходность, % годовых' })).toBeInTheDocument();
    await user.click(form.getByRole('button', { name: 'Посчитать' }));
    await waitFor(() =>
      expect(api.analytics).toHaveBeenLastCalledWith('i-ofz', { yield: '14.8', measure: 'annual' }, undefined),
    );
    // Цене измерение не нужно.
    await user.selectOptions(form.getByRole('combobox'), 'price');
    await user.click(form.getByRole('button', { name: 'Посчитать' }));
    await waitFor(() => expect(api.analytics).toHaveBeenLastCalledWith('i-ofz', { price: '14.8' }, undefined));
  });

  it('refuses something that is not a number', async () => {
    const user = userEvent.setup();
    const api = renderBlock();
    await screen.findByLabelText('Цена в расчёте');

    const form = within(screen.getByRole('form', { name: 'Что если' }));
    await user.type(form.getByRole('textbox'), 'сто');
    await user.click(form.getByRole('button', { name: 'Посчитать' }));

    expect(form.getByRole('alert')).toHaveTextContent('Введите число');
    expect(api.analytics).toHaveBeenCalledTimes(1);
  });

  it('says what is missing and still offers a what-if price', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.analytics.mockRejectedValue(ruleViolation('Нет RUONIA до 28.10.2026.'));
    renderBlock(api);

    expect(await screen.findByRole('alert')).toHaveTextContent('Нет RUONIA');
    expect(screen.getByRole('heading', { name: 'Калькулятор доходности' })).toBeInTheDocument();
    // Скачивать нечего.
    expect(screen.queryByRole('button', { name: 'Скачать расчёт' })).not.toBeInTheDocument();
    const form = within(screen.getByRole('form', { name: 'Что если' }));
    expect(form.getAllByRole('option')).toHaveLength(3);
    await user.selectOptions(form.getByRole('combobox'), 'spread');
    await user.type(form.getByRole('textbox'), '-25');
    await user.click(form.getByRole('button', { name: 'Посчитать' }));
    await waitFor(() =>
      expect(api.analytics).toHaveBeenLastCalledWith('i-ofz', { spread: '-25', measure: 'continuous' }, undefined),
    );
  });

  it('downloads the calculation in Excel for the same price and day', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    let finish: (file: Blob) => void = () => undefined;
    api.analyticsWorkbook.mockImplementation(() => new Promise<Blob>((resolve) => (finish = resolve)));
    renderBlock(api);
    await screen.findByLabelText('Цена в расчёте');
    await user.click(screen.getByRole('button', { name: 'Вчера' }));
    await screen.findByLabelText('Цена в расчёте');

    await user.click(screen.getByRole('button', { name: 'Скачать расчёт' }));

    expect(api.analyticsWorkbook).toHaveBeenCalledWith('i-ofz', {}, '2026-10-27T23:59:59+03:00');
    expect(screen.getByRole('button', { name: 'Готовлю файл…' })).toBeDisabled();
    const file = new Blob(['PK']);
    finish(file);
    expect(await screen.findByRole('button', { name: 'Скачать расчёт' })).toBeEnabled();
    expect(saveFile).toHaveBeenCalledWith(file, 'Расчёт SU26238RMFS4 28.10.2026.xlsx');
  });

  it('says why the calculation could not be downloaded', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.analyticsWorkbook.mockRejectedValue(ruleViolation('Нет цены для оценки на этот момент.'));
    renderBlock(api);
    await screen.findByLabelText('Цена в расчёте');

    await user.click(screen.getByRole('button', { name: 'Скачать расчёт' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Нет цены для оценки на этот момент.');
    expect(screen.getByRole('button', { name: 'Скачать расчёт' })).toBeEnabled();
  });
});
