import { ApiError, type Role, type Session } from '@am/platform';
import { render } from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import { vi } from 'vitest';

import type {
  CalendarDay,
  Instrument,
  InstrumentSummary,
  Lookup,
  Params,
  RefdataApi,
  Version,
} from '../api';

export function session(...roles: Role[]): Session {
  return {
    username: 'user',
    displayName: 'Пользователь',
    roles: new Set(roles),
    getAccessToken: () => Promise.resolve('t'),
    logout: () => undefined,
  };
}

/** ОФЗ 26238: фиксированный купон, погашение одним платежом. */
export function params(fields: Partial<Params> = {}): Params {
  return {
    name: 'ОФЗ-ПД 26238 15/05/2041',
    short_name: 'ОФЗ 26238',
    board: 'TQOB',
    currency: 'RUB',
    issuer: 'Министерство финансов Российской Федерации',
    face_value: '1000',
    initial_face_value: '1000',
    issue_date: '2021-06-16',
    maturity_date: '2041-05-15',
    coupon_kind: 'fixed',
    coupon_frequency: 2,
    nominal_indexation: 'none',
    coupons: [
      {
        date: '2026-11-25',
        start_date: '2026-05-27',
        record_date: '2026-11-24',
        rate: '7.1',
        amount: '35.4',
      },
      { date: '2027-05-26', start_date: '2026-11-25', record_date: null, rate: null, amount: null },
    ],
    amortizations: [{ date: '2041-05-15', percent: '100', amount: '1000', is_maturity: true }],
    offers: [],
    floater: null,
    ...fields,
  };
}

/** Флоатер на ключевую ставку, параметры которого сопровождение ещё не ввело. */
export function floaterParams(fields: Partial<Params> = {}): Params {
  return params({
    name: 'ОФЗ-ПК 29025 13/08/2036',
    short_name: 'ОФЗ 29025',
    maturity_date: '2036-08-13',
    coupon_kind: 'floater',
    coupon_frequency: 4,
    ...fields,
  });
}

export function version(number: number, fields: Partial<Version> = {}): Version {
  return {
    number,
    valid_from: null,
    source: 'iss',
    author: 'trader',
    comment: 'Загружена из MOEX ISS',
    created_at: '2026-10-20T07:15:00Z',
    pinned: [],
    params: params(),
    ...fields,
  };
}

export const SUMMARY: InstrumentSummary = {
  id: 'i-1',
  secid: 'SU26238RMFS4',
  isin: 'RU000A1038V6',
  kind: 'bond',
  name: 'ОФЗ-ПД 26238 15/05/2041',
  short_name: 'ОФЗ 26238',
  added_by: 'trader',
  last_synced_at: '2026-10-26T03:00:00Z',
  last_sync_error: null,
};

export function instrument(
  versions: readonly Version[] = [version(1)],
  fields: Partial<Instrument> = {},
): Instrument {
  const current = versions[versions.length - 1]!;
  return {
    ...SUMMARY,
    short_name: current.params.short_name,
    current,
    versions: versions.map(({ params: _params, ...header }) => header),
    alerts: [],
    ...fields,
  };
}

export const LOOKUP: Lookup = {
  secid: 'SU26238RMFS4',
  isin: 'RU000A1038V6',
  kind: 'bond',
  params: params(),
  instrument_id: null,
};

export const DAYS: readonly CalendarDay[] = [
  { day: '2026-11-04', is_trading: false, comment: 'День народного единства' },
  { day: '2026-12-27', is_trading: true, comment: null },
];

/** Подменный API: отвечает фиксированными данными, вызовы запоминает. */
export function fakeApi() {
  const answer = <T,>(value: T) => vi.fn((..._args: unknown[]) => Promise.resolve(value));
  return {
    lookup: answer(LOOKUP),
    load: answer(instrument()),
    search: answer<InstrumentSummary[]>([SUMMARY]),
    instrument: answer(instrument()),
    version: answer(version(1)),
    edit: answer(instrument()),
    sync: answer(instrument()),
    calendar: answer<CalendarDay[]>([...DAYS]),
    setDay: answer(null),
    clearDay: answer(null),
    addTradingDays: answer({ date: '2026-10-27' }),
  } satisfies Record<keyof RefdataApi, unknown>;
}

export type FakeApi = ReturnType<typeof fakeApi>;

export function asApi(fake: FakeApi): RefdataApi {
  return fake as unknown as RefdataApi;
}

export function ruleViolation(message: string): ApiError {
  return new ApiError(422, { detail: { code: 'rule', message } });
}

/** Страница справочника под /refdata, как в shell; карточка бумаги — заглушка. */
export function renderPage(page: ReactNode, path = '/refdata') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/refdata" element={page} />
        <Route path="/refdata/instruments/:id" element={<OpenedCard />} />
      </Routes>
    </MemoryRouter>,
  );
}

function OpenedCard() {
  return <p>Открыта карточка {useParams().id}</p>;
}
