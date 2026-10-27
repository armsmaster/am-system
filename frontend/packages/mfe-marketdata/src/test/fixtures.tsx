import { ApiError, type Role, type Session } from '@am/platform';
import { render } from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import { vi } from 'vitest';

import type { Batch, MarketdataApi, PriceView, RuleRecord, Series } from '../api';

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
