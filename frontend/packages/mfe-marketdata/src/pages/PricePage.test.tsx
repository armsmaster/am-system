import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import type { Rule, Series } from '../api';
import { asApi, fakeApi, RULE, ruleViolation, series, view, type FakeApi } from '../test/fixtures';
import { PricePage } from './PricePage';

vi.mock('../components/PriceChart', () => ({
  PriceChart: ({ rule }: { rule: Rule }) => (
    <div role="img" aria-label="График цен">
      правило {rule.price_types.join(', ')}
    </div>
  ),
}));

// Вторник 27.10.2026, 11:05 по Москве.
const NOW = new Date('2026-10-27T08:05:00Z');

function renderCard(api: FakeApi = fakeApi(), canEdit = true) {
  render(
    <MemoryRouter initialEntries={['/marketdata/instruments/i-ofz']}>
      <Routes>
        <Route
          path="/marketdata/instruments/:id"
          element={<PricePage api={asApi(api)} id="i-ofz" basePath="/marketdata" canEdit={canEdit} now={NOW} />}
        />
        <Route path="/marketdata" element={<p>Обзор цен</p>} />
      </Routes>
    </MemoryRouter>,
  );
  return api;
}

async function tiles() {
  const list = within(await screen.findByLabelText('Цена и изменение'));
  const terms = list.getAllByRole('term').map((dt) => dt.textContent);
  const values = list.getAllByRole('definition').map((dd) => dd.textContent);
  return Object.fromEntries(terms.map((term, i) => [term, values[i]]));
}

describe('PricePage', () => {
  it('shows the price, its change, face value and accrued interest, the chart and every price', async () => {
    const api = renderCard();

    expect(await tiles()).toEqual({
      'Цена для оценки': '51,823VWAP · quik, 27.10, 11:00',
      'За день': '+0,14%с 51,75 на конец 26.10.2026',
      'За неделю': '+0,43%с 51,6 на конец 20.10.2026',
      'Номинал, 28.10': '1 000,00 ₽',
      'НКД, 28.10': '28,40 ₽',
    });
    expect(screen.getByText('По правилу: VWAP. Торговый день — 27.10.2026; разброс цен за день — 7,7 бп.')).toBeInTheDocument();
    // График — за две недели до сейчас.
    expect(api.series).toHaveBeenCalledWith('i-ofz', '2026-10-13T08:05:00.000Z');
    expect(await screen.findByRole('img', { name: 'График цен' })).toHaveTextContent('правило vwap');
    const rows = within(screen.getByRole('table', { name: 'Все цены' })).getAllByRole('row').slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      'VWAPMOEX ISS51,78226.10, 23:59',
      'VWAPquik51,82327.10, 11:00',
      'bidquik51,78427.10, 11:00',
      'askquik51,78927.10, 11:00',
    ]);
    expect(rows[0]).toHaveClass('past');
    expect(screen.getByRole('region', { name: 'Калькулятор доходности' })).toBeInTheDocument();
    expect(api.analytics).toHaveBeenCalledWith('i-ofz', {}, undefined);
  });

  it('warns when there is no price', async () => {
    const api = fakeApi();
    api.price.mockResolvedValue(
      view({
        price: null,
        candidates: [],
        spread_bp: null,
        day_ago: { day: '2026-10-26', price: null },
        week_ago: { day: '2026-10-20', price: null },
        accrued: null,
      }),
    );
    renderCard(api);

    expect(await tiles()).toMatchObject({
      'Цена для оценки': 'нет цены',
      'За день': '—нет цены на 26.10.2026',
      'За неделю': '—нет цены на 20.10.2026',
      'НКД, 28.10': '—',
    });
    expect(screen.getByText(/Цены нет/)).toBeInTheDocument();
    expect(screen.getByText('Цен по бумаге ещё нет.')).toBeInTheDocument();
  });

  it('marks a stale price and a spread above the threshold', async () => {
    const api = fakeApi();
    api.price.mockResolvedValue(
      view({ price: { ...view().price!, stale: true }, wide_spread: true, spread_bp: '193.1' }),
    );
    renderCard(api);

    expect((await tiles())['Цена для оценки']).toBe('51,823VWAP · quik, 27.10, 11:00устарела');
    expect(screen.getByText(', больше порога 50 бп')).toBeInTheDocument();
  });

  it('shows a fund share without a calculator and without face value', async () => {
    const api = fakeApi();
    api.price.mockResolvedValue(
      view({
        instrument: { id: 'i-fund', secid: 'LQDT', isin: null, kind: 'fund_share', short_name: 'LQDT ETF' },
        spread_bp: null,
        accrued: null,
        face_value: null,
      }),
    );
    renderCard(api);

    expect(Object.keys(await tiles())).toEqual(['Цена для оценки', 'За день', 'За неделю']);
    expect(screen.getByText(/разброс цен за день — нет\./)).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Калькулятор доходности' })).not.toBeInTheDocument();
    expect(api.analytics).not.toHaveBeenCalled();
  });

  it('shows the rule to those who cannot change it, and its history', async () => {
    const api = fakeApi();
    api.price.mockResolvedValue(view({ rule: RULE }));
    renderCard(api, false);

    expect(
      await screen.findByText(
        'Правило: VWAP → DEMA · DEMA по барам 10 мин, полураспад 60 мин · порог разброса 50 бп. Правило и ручную цену меняет трейдинг.',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Ручная цена' })).not.toBeInTheDocument();
    await userEvent.click(await screen.findByText('История правила (1)'));
    const history = within(screen.getByRole('table', { name: 'История правила' }));
    expect(history.getAllByRole('row').slice(1).map((r) => r.textContent)).toEqual([
      '27.10, 11:05VWAP → DEMADEMA 10 мин, H 60 минtrader — Неликвидная бумага: сглаживаем',
    ]);
  });

  it('says the default rule was never changed', async () => {
    const api = fakeApi();
    api.series.mockResolvedValue(series({ rules: [] }));
    renderCard(api);

    expect(await screen.findByText('Правило по умолчанию — VWAP; его ещё не меняли.')).toBeInTheDocument();
  });

  it('enters a manual price in a panel and reloads the card', async () => {
    const user = userEvent.setup();
    const api = renderCard();

    await user.click(await screen.findByRole('button', { name: 'Ручная цена' }));
    const form = within(within(screen.getByRole('dialog', { name: 'Ручная цена' })).getByRole('form'));
    await user.type(form.getByLabelText('Цена'), '51,5');
    await user.type(form.getByLabelText('Комментарий — откуда цена'), 'котировка брокера');
    await user.click(form.getByRole('button', { name: 'Ввести цену' }));

    expect(api.manualPrice).toHaveBeenCalledWith('i-ofz', { value: '51.5', comment: 'котировка брокера' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.price).toHaveBeenCalledTimes(2);
    expect(api.series).toHaveBeenCalledTimes(2);
  });

  it('keeps the manual price when the service refuses it', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.manualPrice.mockRejectedValue(ruleViolation('Цена должна быть больше нуля.'));
    renderCard(api);

    await user.click(await screen.findByRole('button', { name: 'Ручная цена' }));
    const form = within(screen.getByRole('form', { name: 'Ручная цена' }));
    await user.type(form.getByLabelText('Цена'), '0');
    await user.type(form.getByLabelText('Комментарий — откуда цена'), 'ошибка');
    await user.click(form.getByRole('button', { name: 'Ввести цену' }));

    expect(await form.findByRole('alert')).toHaveTextContent('больше нуля');
    expect(form.getByLabelText('Цена')).toHaveValue('0');
    await user.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('opens the rule in a panel, even before the chart data came', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.series.mockReturnValue(new Promise<Series>(() => undefined));
    // Правило с DEMA: форма подсказывает таймфреймы баров, а их ещё нет.
    api.price.mockResolvedValue(view({ rule: RULE }));
    renderCard(api);

    await user.click(await screen.findByRole('button', { name: 'Правило оценки' }));
    const panel = within(screen.getByRole('dialog', { name: 'Правило оценки' }));
    expect(panel.getByRole('form', { name: 'Правило оценки' })).toBeInTheDocument();
    expect(panel.getByText('Скрипт ещё не присылал бары по этой бумаге.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('leads back to the overview', async () => {
    renderCard();

    await userEvent.click(screen.getByRole('link', { name: '← Все бумаги' }));

    expect(screen.getByText('Обзор цен')).toBeInTheDocument();
  });
});
