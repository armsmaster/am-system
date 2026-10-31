import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import {
  asApi,
  fakeApi,
  fixedAnalytics,
  floaterAnalytics,
  ruleViolation,
  RUONIA,
  type FakeApi,
} from '../test/fixtures';
import { AnalyticsBlock } from './AnalyticsBlock';

function renderBlock(api: FakeApi = fakeApi()) {
  render(<AnalyticsBlock api={asApi(api)} id="i-ofz" />);
  return api;
}

function rows(name: string): string[] {
  const table = screen.getByRole('table', { name });
  return within(table)
    .getAllByRole('row')
    .map((r) => r.textContent ?? '');
}

describe('AnalyticsBlock', () => {
  it('shows the yield and risk of a fixed coupon bond', async () => {
    renderBlock();

    expect(await screen.findByRole('heading', { name: 'Доходность и риск' })).toBeInTheDocument();
    expect(
      screen.getByText(
        /Чистая цена 52,4930% \(цена для оценки: VWAP, quik\)\. Дата расчётов — 29\.10\.2026: номинал 1 000 ₽, НКД 28,79 ₽, грязная цена 553,72 ₽\./,
      ),
    ).toBeInTheDocument();
    expect(rows('Доходность и метрики').slice(1)).toEqual([
      'к погашению 15.05.204115,946,822 4915,890,326060,25',
    ]);
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
          },
        ],
      }),
    );
    renderBlock(api);

    expect(await screen.findByText('Купон 11.04.2028 не объявлен.')).toBeInTheDocument();
    expect(rows('Доходность и метрики')[1]).toMatch(/^к оферте 14.10.2027/);
  });

  it('breaks down the spread of a floater', async () => {
    const api = fakeApi();
    api.analytics.mockResolvedValue(floaterAnalytics());
    renderBlock(api);

    expect(await screen.findByRole('heading', { name: 'Спред над фондированием' })).toBeInTheDocument();
    expect(screen.getByText('81,5 бп', { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByText(/КС \+ 130 бп, без капитализации; к погашению 12\.12\.2026/)).toBeInTheDocument();
    expect(screen.getByText(/Доходность при плоском прогнозе ставки — 15,72%/)).toBeInTheDocument();
    expect(
      screen.getByText(
        /Фондирование: RUONIA 13,90% на 28\.10\.2026, cbr → 14,91% эффективных годовых\. Индекс: Ключевая ставка 14,00% на 26\.10\.2026, MOEX ISS\./,
      ),
    ).toBeInTheDocument();
    expect(rows('Разложение спреда')).toEqual([
      'Базис ставки11,5 бп',
      'Капитализация−8,9 бп',
      'Маржа148,6 бп',
      'Прочее−2,2 бп',
      'Поправка на цену−67,5 бп',
      'Спред81,5 бп',
    ]);
    expect(screen.getByText(/Спред-дюрация 0,10 года, DV01 по спреду 0,0104 ₽/)).toBeInTheDocument();
    expect(screen.getByText(/что если: введённая цена/)).toBeInTheDocument();
  });

  it('does not repeat RUONIA as the index of a RUONIA floater', async () => {
    const api = fakeApi();
    const floater = floaterAnalytics();
    api.analytics.mockResolvedValue({
      ...floater,
      spread: { ...floater.spread!, rate: 'ruonia', capitalized: true, index: RUONIA },
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
      spread: {
        ...floater.spread!,
        rate: null,
        margin_bp: null,
        capitalized: null,
        index: null,
        funding: { rate: 'ruonia', label: 'RUONIA', date: null, value: null, source: null },
      },
    });
    renderBlock(api);

    expect(await screen.findByText(/линкер: номинал растёт с RUONIA/)).toBeInTheDocument();
    expect(screen.getByText(/Фондирование: RUONIA — нет/)).toBeInTheDocument();
    expect(rows('Разложение спреда')[2]).toBe('Купон148,6 бп');
  });

  it('recomputes for an entered yield and goes back to the valuation price', async () => {
    const user = userEvent.setup();
    const api = renderBlock();
    await screen.findByRole('table', { name: 'Доходность и метрики' });

    const form = within(screen.getByRole('form', { name: 'Что если' }));
    expect(form.getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Чистая цена, %',
      'Доходность, %',
    ]);
    await user.selectOptions(form.getByRole('combobox'), 'yield');
    await user.type(form.getByRole('textbox'), '16,5');
    await user.click(form.getByRole('button', { name: 'Посчитать' }));
    await waitFor(() => expect(api.analytics).toHaveBeenLastCalledWith('i-ofz', { yield: '16.5' }));

    await user.click(await form.findByRole('button', { name: 'Вернуть цену для оценки' }));
    await waitFor(() => expect(api.analytics).toHaveBeenLastCalledWith('i-ofz', {}));
  });

  it('refuses something that is not a number', async () => {
    const user = userEvent.setup();
    const api = renderBlock();
    await screen.findByRole('table', { name: 'Доходность и метрики' });

    const form = within(screen.getByRole('form', { name: 'Что если' }));
    await user.type(form.getByRole('textbox'), 'сто');
    await user.click(form.getByRole('button', { name: 'Посчитать' }));

    expect(form.getByRole('alert')).toHaveTextContent('Введите число');
    expect(api.analytics).toHaveBeenCalledTimes(1);
  });

  it('says what is missing and still offers a what-if price', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.analytics.mockRejectedValue(ruleViolation('Нет ставки ruonia на 28.10.2026.'));
    renderBlock(api);

    expect(await screen.findByRole('alert')).toHaveTextContent('Нет ставки ruonia');
    expect(screen.getByRole('heading', { name: 'Расчёт по облигации' })).toBeInTheDocument();
    const form = within(screen.getByRole('form', { name: 'Что если' }));
    expect(form.getAllByRole('option')).toHaveLength(3);
    await user.selectOptions(form.getByRole('combobox'), 'spread');
    await user.type(form.getByRole('textbox'), '-25');
    await user.click(form.getByRole('button', { name: 'Посчитать' }));
    await waitFor(() => expect(api.analytics).toHaveBeenLastCalledWith('i-ofz', { spread: '-25' }));
  });
});
