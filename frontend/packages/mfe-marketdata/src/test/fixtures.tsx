import { ApiError, type Role, type Session } from '@am/platform';
import { render } from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import { vi } from 'vitest';

import type { Analytics, Batch, MarketdataApi, PriceView, Rate, RuleRecord, Series } from '../api';

export function session(...roles: Role[]): Session {
  return {
    username: 'user',
    displayName: 'Пользователь',
    roles: new Set(roles),
    getAccessToken: () => Promise.resolve('t'),
    logout: () => undefined,
  };
}

export const OFZ = {
  id: 'i-ofz',
  secid: 'SU26238RMFS4',
  isin: 'RU000A1038V6',
  kind: 'bond' as const,
  short_name: 'ОФЗ 26238',
};

/** ОФЗ 26238 во вторник 27.10 в 11:05: VWAP скрипта, bid и ask, вчерашний итог ISS. */
export function view(fields: Partial<PriceView> = {}): PriceView {
  return {
    instrument: OFZ,
    at: '2026-10-27T08:05:00Z',
    trading_day: '2026-10-27',
    price: {
      value: '51.823000',
      type: 'vwap',
      source: 'quik',
      time: '2026-10-27T08:00:00Z',
      stale: false,
      reason: 'по правилу: VWAP',
    },
    candidates: [
      { type: 'vwap', source: 'iss', value: '51.782000', time: '2026-10-26T20:59:59Z', today: false },
      { type: 'vwap', source: 'quik', value: '51.823000', time: '2026-10-27T08:00:00Z', today: true },
      { type: 'bid', source: 'quik', value: '51.784000', time: '2026-10-27T08:00:00Z', today: true },
      { type: 'ask', source: 'quik', value: '51.789000', time: '2026-10-27T08:00:00Z', today: true },
    ],
    rule: { price_types: ['vwap'], dema_timeframe: null, dema_half_life: null, spread_threshold_bp: 50 },
    spread_bp: '7.7',
    wide_spread: false,
    ...fields,
  };
}

export const RULE: RuleRecord = {
  price_types: ['vwap', 'dema'],
  dema_timeframe: 10,
  dema_half_life: '60.00',
  spread_threshold_bp: 50,
  valid_from: '2026-10-27T08:05:00Z',
  author: 'trader',
  comment: 'Неликвидная бумага: сглаживаем',
};

export function series(fields: Partial<Series> = {}): Series {
  return {
    instrument: OFZ,
    quotes: [
      { type: 'vwap', source: 'quik', time: '2026-10-27T07:50:00Z', value: '51.800000' },
      { type: 'vwap', source: 'quik', time: '2026-10-27T08:00:00Z', value: '51.823000' },
      { type: 'bid', source: 'iss', time: '2026-10-27T08:00:00Z', value: '51.750000' },
    ],
    bars: {
      '10': [
        {
          start: '2026-10-27T07:00:00Z',
          end: '2026-10-27T07:10:00Z',
          open: '51.7',
          high: '51.9',
          low: '51.6',
          close: '51.8',
        },
      ],
      '60': [],
    },
    dema: [{ time: '2026-10-27T07:10:00Z', value: '51.8' }],
    manual: [
      { value: '51.500000', entered_at: '2026-10-27T07:30:00Z', author: 'trader', comment: 'брокер' },
    ],
    rules: [RULE],
    ...fields,
  };
}

export const BATCH: Batch = {
  id: 'b-1',
  client: 'service-account-am-price-feed',
  source: 'quik',
  kind: 'quotes',
  received_at: '2026-10-27T08:05:00Z',
  accepted: 3,
  rejected: [
    {
      index: 1,
      code: 'XS0000000000',
      error: 'unknown_instrument',
      message: 'Бумаги XS0000000000 нет в справочнике.',
    },
  ],
};

/** ОФЗ 26238: эталон design/04 — цена закрытия 28.10, расчёты 29.10. */
export function fixedAnalytics(fields: Partial<Analytics> = {}): Analytics {
  return {
    instrument: OFZ,
    at: '2026-10-28T12:00:00Z',
    trading_day: '2026-10-28',
    settlement_date: '2026-10-29',
    type: 'fixed',
    price: {
      basis: 'valuation',
      clean: '52.493000',
      face_value: '1000.000000',
      accrued: '28.790000',
      dirty: '553.720000',
      valuation: { ...view().price!, value: '52.493000' },
    },
    yields: [
      {
        horizon: { kind: 'maturity', date: '2041-05-15', price: '100' },
        yield_percent: '15.935227',
        macaulay_years: '6.824692',
        macaulay_days: '2491.01',
        modified_duration: '5.886642',
        dv01: '0.325955',
        convexity: '60.252300',
        problem: null,
      },
    ],
    spread: null,
    ...fields,
  };
}

export const RUONIA: Rate = {
  rate: 'ruonia',
  label: 'RUONIA',
  date: '2026-10-28',
  value: '13.9000',
  source: 'cbr',
};

/** Газпром нефть 003P-08R: КС + 130 бп раз в месяц, цена 100,07 (эталон design/04). */
export function floaterAnalytics(): Analytics {
  return fixedAnalytics({
    type: 'floater',
    price: { ...fixedAnalytics().price, basis: 'price', clean: '100.070000', valuation: null },
    yields: [],
    spread: {
      horizon: { kind: 'maturity', date: '2026-12-12', price: '100' },
      rate: 'key_rate',
      margin_bp: '130.00',
      capitalized: false,
      funding: RUONIA,
      funding_percent: '14.909369',
      index: { rate: 'key_rate', label: 'Ключевая ставка', date: '2026-10-26', value: '14.0000', source: 'iss' },
      spread_bp: '81.51',
      yield_percent: '15.724468',
      breakdown: {
        basis_bp: '11.49',
        capitalization_bp: '-8.88',
        margin_bp: '148.59',
        other_bp: '-2.20',
        price_bp: '-67.49',
      },
      spread_duration: '0.103286',
      spread_dv01: '0.010405',
    },
  });
}

/** Подменный API: отвечает фиксированными данными, вызовы запоминает. */
export function fakeApi() {
  const answer = <T,>(value: T) => vi.fn((..._args: unknown[]) => Promise.resolve(value));
  return {
    overview: answer<PriceView[]>([view()]),
    price: answer(view()),
    series: answer(series()),
    rules: answer<RuleRecord[]>([RULE]),
    setRule: answer(RULE),
    manualPrice: answer(series().manual[0]!),
    batches: answer<Batch[]>([BATCH]),
    analytics: answer(fixedAnalytics()),
    rates: answer<Rate[]>([RUONIA]),
  } satisfies Record<keyof MarketdataApi, unknown>;
}

export type FakeApi = ReturnType<typeof fakeApi>;

export function asApi(fake: FakeApi): MarketdataApi {
  return fake as unknown as MarketdataApi;
}

export function ruleViolation(message: string): ApiError {
  return new ApiError(422, { detail: { code: 'rule', message } });
}

/** Страница цен под /marketdata, как в shell; карточка бумаги — заглушка. */
export function renderPage(page: ReactNode, path = '/marketdata') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/marketdata" element={page} />
        <Route path="/marketdata/instruments/:id" element={<OpenedCard />} />
      </Routes>
    </MemoryRouter>,
  );
}

function OpenedCard() {
  return <p>Открыта карточка {useParams().id}</p>;
}
