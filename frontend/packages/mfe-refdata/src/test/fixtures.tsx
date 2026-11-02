import { ApiError, type Role, type Session } from '@am/platform';
import { render } from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import { vi } from 'vitest';

import type {
  CalendarDay,
  Classifications,
  Classifier,
  Instrument,
  InstrumentSummary,
  Issuer,
  IssuerSummary,
  Lookup,
  Params,
  RatingRecord,
  Ratings,
  RatingScale,
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
    iss_floater: null,
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
    issuer: { id: 'e-1', name: 'Министерство финансов Российской Федерации' },
    iss_type: 'ofz_bond',
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

export const ISSUERS: readonly IssuerSummary[] = [
  { id: 'e-1', iss_id: 1228, name: 'Министерство финансов Российской Федерации', inn: '7710168360', instruments: 2 },
  { id: 'e-2', iss_id: 406, name: 'государственная корпорация развития «ВЭБ.РФ»', inn: null, instruments: 1 },
];

export const ISSUER: Issuer = {
  id: 'e-1',
  iss_id: 1228,
  name: 'Министерство финансов Российской Федерации',
  inn: '7710168360',
  instruments: [SUMMARY, { ...SUMMARY, id: 'i-2', secid: 'LQDT', short_name: 'LQDT', kind: 'fund_share' }],
};

/** Две ступени общей шкалы у двух агентств — достаточно для формы. */
export const SCALE: RatingScale = {
  agencies: [
    { code: 'acra', name: 'АКРА', ratings: ['AAA(RU)', 'AA+(RU)', 'AA-(RU)'] },
    { code: 'expert_ra', name: 'Эксперт РА', ratings: ['ruAAA', 'ruAA+', 'ruAA-'] },
  ],
  grades: ['AAA', 'AA+', 'AA-'],
};

export function ratingRecord(id: string, fields: Partial<RatingRecord> = {}): RatingRecord {
  return {
    id,
    action: 'assigned',
    agency: 'acra',
    agency_name: 'АКРА',
    rating: 'AA+(RU)',
    grade: 'AA+',
    valid_from: '2026-06-01',
    comment: null,
    author: 'risk',
    created_at: '2026-11-02T09:00:00Z',
    cancelled_by: null,
    cancelled_at: null,
    cancel_comment: null,
    ...fields,
  };
}

/** Эмитент Г из методики шага 9: АКРА понизило 11.11, ошибочная запись НКР отменена. */
export const RATINGS: Ratings = {
  now: {
    status: 'rated',
    ratings: [{ agency: 'acra', agency_name: 'АКРА', rating: 'AA-(RU)', grade: 'AA-', since: '2026-11-11' }],
  },
  records: [
    ratingRecord('r-1'),
    ratingRecord('r-2', {
      agency: 'nkr',
      agency_name: 'НКР',
      rating: 'A+.ru',
      grade: 'A+',
      valid_from: '2026-11-10',
      cancelled_by: 'risk',
      cancelled_at: '2026-11-10T12:00:00Z',
      cancel_comment: 'Не тот эмитент',
    }),
    ratingRecord('r-3', { rating: 'AA-(RU)', grade: 'AA-', valid_from: '2026-11-11', comment: 'Пресс-релиз АКРА' }),
  ],
};

export const SENIORITY: Classifier = {
  id: 'c-1',
  name: 'Очерёдность',
  target: 'instrument',
  description: 'п. 2 политики',
  created_by: 'risk',
  created_at: '2026-11-02T09:00:00Z',
  values: [
    { id: 'v-1', name: 'старший долг' },
    { id: 'v-2', name: 'субординированная' },
  ],
};

export const TRANCHE: Classifier = {
  ...SENIORITY,
  id: 'c-2',
  name: 'Транш',
  description: null,
  values: [
    { id: 'v-3', name: 'старший' },
    { id: 'v-4', name: 'иной' },
  ],
};

export const ISSUER_KIND: Classifier = {
  ...SENIORITY,
  id: 'c-3',
  name: 'Тип эмитента',
  target: 'issuer',
  description: null,
  values: [
    { id: 'v-5', name: 'банк' },
    { id: 'v-6', name: 'прочие' },
  ],
};

export const CLASSIFICATIONS: Classifications = {
  now: [{ classifier_id: 'c-1', classifier: 'Очерёдность', value_id: 'v-1', value: 'старший долг', since: null }],
  records: [
    {
      id: 'k-1',
      classifier_id: 'c-1',
      classifier: 'Очерёдность',
      value_id: 'v-1',
      value: 'старший долг',
      valid_from: null,
      comment: null,
      author: 'risk',
      created_at: '2026-11-02T09:00:00Z',
      cancelled_by: null,
      cancelled_at: null,
      cancel_comment: null,
    },
    {
      id: 'k-2',
      classifier_id: 'c-1',
      classifier: 'Очерёдность',
      value_id: 'v-2',
      value: 'субординированная',
      valid_from: '2026-11-11',
      comment: 'По проспекту',
      author: 'risk',
      created_at: '2026-11-11T09:00:00Z',
      cancelled_by: 'risk',
      cancelled_at: '2026-11-11T10:00:00Z',
      cancel_comment: 'Ошиблись выпуском',
    },
  ],
};

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
    issuers: answer<IssuerSummary[]>([...ISSUERS]),
    issuer: answer(ISSUER),
    ratingScale: answer(SCALE),
    ratings: answer(RATINGS),
    addRating: answer(RATINGS),
    cancelRating: answer(RATINGS),
    classifiers: answer<Classifier[]>([SENIORITY, TRANCHE, ISSUER_KIND]),
    createClassifier: answer(SENIORITY),
    updateClassifier: answer(SENIORITY),
    addValue: answer(SENIORITY),
    renameValue: answer(SENIORITY),
    classifications: answer(CLASSIFICATIONS),
    classify: answer(CLASSIFICATIONS),
    cancelClassification: answer(CLASSIFICATIONS),
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
