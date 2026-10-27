import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import { asApi, fakeApi, RULE, ruleViolation, series, view, type FakeApi } from '../test/fixtures';
import { PricePage } from './PricePage';

vi.mock('../components/PriceChart', () => ({
  PriceChart: ({ timeframe }: { timeframe: number | null }) => (
    <div role="img" aria-label="График цен">
      таймфрейм {String(timeframe)}
    </div>
  ),
}));

function renderCard(api: FakeApi = fakeApi(), canEdit = true) {
  render(
    <MemoryRouter initialEntries={['/marketdata/instruments/i-ofz']}>
      <Routes>
        <Route
          path="/marketdata/instruments/:id"
          element={<PricePage api={asApi(api)} id="i-ofz" basePath="/marketdata" canEdit={canEdit} />}
        />
        <Route path="/marketdata" element={<p>Обзор цен</p>} />
      </Routes>
    </MemoryRouter>,
  );
  return api;
}

describe('PricePage', () => {
  it('explains the valuation price and lists every price', async () => {
    renderCard();

    const valuation = within(await screen.findByRole('group', { name: 'Цена для оценки' }));
    expect(valuation.getByText('51,823')).toBeInTheDocument();
    expect(valuation.getByText('VWAP · quik, 27.10, 11:00')).toBeInTheDocument();
    expect(valuation.getByText('по правилу: VWAP')).toBeInTheDocument();
    expect(valuation.getByText(/Торговый день — 27.10.2026. Разброс цен за день — 7,7 бп./)).toBeInTheDocument();
    const rows = within(screen.getByRole('table', { name: 'Все цены' })).getAllByRole('row').slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      'VWAPMOEX ISS51,78226.10, 23:59',
      'VWAPquik51,82327.10, 11:00',
      'bidquik51,78427.10, 11:00',
      'askquik51,78927.10, 11:00',
    ]);
    expect(rows[0]).toHaveClass('muted');
    expect(screen.getByRole('img', { name: 'График цен' })).toHaveTextContent('таймфрейм null');
  });

  it('warns when there is no price and when the spread is wide', async () => {
    const api = fakeApi();
    api.price.mockResolvedValue(view({ price: null, candidates: [], spread_bp: null }));
    renderCard(api);

    expect(await screen.findByText(/Цены нет/)).toBeInTheDocument();
    expect(screen.getByText('Цен по бумаге ещё нет.')).toBeInTheDocument();
    expect(screen.getByText(/Разброс цен за день — нет./)).toBeInTheDocument();
  });

  it('marks a stale price and a spread above the threshold', async () => {
    const api = fakeApi();
    api.price.mockResolvedValue(
      view({ wide_spread: true, spread_bp: '193.1', price: { ...view().price!, stale: true } }),
    );
    renderCard(api);

    expect(await screen.findByText('устарела')).toBeInTheDocument();
    expect(screen.getByText(/193,1 бп, больше порога/)).toBeInTheDocument();
  });

  it('shows the rule to those who cannot change it', async () => {
    const api = fakeApi();
    api.price.mockResolvedValue(view({ rule: RULE }));
    renderCard(api, false);

    const rule = within(await screen.findByRole('region', { name: 'Правило оценки' }));
    expect(rule.getByText('VWAP → DEMA')).toBeInTheDocument();
    expect(rule.getByText('DEMA по барам 10 мин, период полураспада 60 мин.')).toBeInTheDocument();
    expect(screen.queryByRole('form', { name: 'Ручная цена' })).not.toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'График цен' })).toHaveTextContent('таймфрейм 10');
  });

  it('shows the rule history, newest first', async () => {
    const api = fakeApi();
    api.series.mockResolvedValue(
      series({ rules: [{ ...RULE, price_types: ['vwap'], dema_timeframe: null, dema_half_life: null, valid_from: '2026-10-26T08:00:00Z', comment: 'по умолчанию' }, RULE] }),
    );
    renderCard(api);

    const history = within(await screen.findByRole('table', { name: 'История правила' }));
    const rows = history.getAllByRole('row').slice(1).map((r) => r.textContent);
    expect(rows).toEqual([
      '27.10, 11:05VWAP → DEMADEMA 10 мин, H 60 минtraderНеликвидная бумага: сглаживаем',
      '26.10, 11:00VWAPtraderпо умолчанию',
    ]);
  });

  it('says the default rule was never changed', async () => {
    const api = fakeApi();
    api.series.mockResolvedValue(series({ rules: [] }));
    renderCard(api);

    expect(await screen.findByText(/Правило по умолчанию — VWAP/)).toBeInTheDocument();
  });

  it('enters a manual price until the end of the day', async () => {
    const api = renderCard();
    const form = within(await screen.findByRole('form', { name: 'Ручная цена' }));

    await userEvent.type(form.getByLabelText('Цена'), ' 51,5 ');
    await userEvent.type(form.getByLabelText('Комментарий — откуда цена'), 'котировка брокера');
    await userEvent.click(form.getByRole('button', { name: 'Ввести цену' }));

    expect(api.manualPrice).toHaveBeenCalledWith('i-ofz', { value: '51.5', comment: 'котировка брокера' });
    expect(form.getByLabelText('Цена')).toHaveValue('');
    expect(api.price).toHaveBeenCalledTimes(2);
  });

  it('keeps the manual price when the service refuses it', async () => {
    const api = fakeApi();
    api.manualPrice.mockRejectedValue(ruleViolation('Цена должна быть больше нуля.'));
    renderCard(api);
    const form = within(await screen.findByRole('form', { name: 'Ручная цена' }));

    await userEvent.type(form.getByLabelText('Цена'), '0');
    await userEvent.type(form.getByLabelText('Комментарий — откуда цена'), 'проверка');
    await userEvent.click(form.getByRole('button', { name: 'Ввести цену' }));

    expect(await form.findByRole('alert')).toHaveTextContent('больше нуля');
    expect(form.getByLabelText('Цена')).toHaveValue('0');
  });

  it('leads back to the overview', async () => {
    renderCard();

    await userEvent.click(screen.getByRole('link', { name: '← Все бумаги' }));

    expect(screen.getByText('Обзор цен')).toBeInTheDocument();
  });
});
