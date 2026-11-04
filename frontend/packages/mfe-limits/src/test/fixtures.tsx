import type { Role, Session } from '@am/platform';
import { render } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { vi } from 'vitest';

import { Limits } from '../App';
import type {
  CheckUsage,
  Classifier,
  Fund,
  FundDay,
  Instrument,
  Issuer,
  LimitSet,
  LimitsApi,
  SetBody,
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

export const FUNDS: Fund[] = [
  { id: 'f-dohod', code: 'DOHOD2', name: 'Доход 2' },
  { id: 'f-opif', code: 'OPIF1', name: 'ОПИФ 1' },
];

export const CLASSIFIERS: Classifier[] = [
  {
    id: 'c-seniority',
    name: 'Очерёдность',
    target: 'instrument',
    values: [
      { id: 'v-senior', name: 'старший долг' },
      { id: 'v-sub', name: 'субординированная' },
    ],
  },
  { id: 'c-ncc', name: 'Принимается НКЦ в обеспечение', target: 'instrument', values: [{ id: 'v-yes', name: 'да' }] },
];

export const ISSUERS: Issuer[] = [{ id: 'e-veb', name: 'ВЭБ.РФ' }];
export const INSTRUMENTS: Instrument[] = [{ id: 'i-lqdt', secid: 'LQDT' }];

/** Набор «Доход 2» в сокращении: по одной проверке каждого типа. */
export const BODY: SetBody = {
  document: { name: 'Инвестиционная политика «Доход 2»', version: '1.0', approved_on: '2026-10-23' },
  settings: {
    ratings: {
      agencies: ['acra', 'expert_ra'],
      pick: 'highest',
      groups: { AAA: 'AAA', 'AA+': 'AA+', AA: 'AA', 'AA-': 'AA' },
      rules: [
        { filter: [{ attribute: 'class:c-seniority', values: ['v-sub'] }], source: 'issuer' },
        { filter: [], source: 'instrument_or_issuer' },
      ],
    },
    warn_at: '90',
  },
  limits: [
    { code: 'Л1', name: 'Плечо', clause: 'п. 5.1', checks: [{ type: 'metric', metric: 'leverage', max: '4.0' }] },
    {
      code: 'Л3',
      name: 'Корпоративные облигации AAA и AA+',
      checks: [
        {
          type: 'share',
          label: 'на эмитента',
          filter: [
            { attribute: 'iss_type', values: ['ofz_bond'], negate: true },
            { attribute: 'class:c-seniority', values: ['v-senior'] },
            { attribute: 'rating_group', values: ['AAA', 'AA+', 'AA'] },
          ],
          group: 'issuer',
          exclude_issuers: ['e-veb'],
          max: '10',
        },
      ],
    },
    {
      code: '4',
      name: 'Допустимые инструменты',
      checks: [
        {
          type: 'eligible',
          rules: [
            { name: 'паи БПИФ', filter: [{ attribute: 'instrument', values: ['i-lqdt'] }] },
            { name: 'ОФЗ', filter: [{ attribute: 'iss_type', values: ['ofz_bond'] }], shorts: true },
          ],
        },
      ],
    },
    {
      code: '4.3',
      name: 'Покупка — бумаги, которые НКЦ принимает',
      checks: [
        {
          type: 'purchase',
          filter: [{ attribute: 'kind', values: ['bond'] }],
          condition: [{ attribute: 'class:c-ncc', values: ['v-yes'] }],
        },
      ],
    },
  ],
};

export function limitSet(version: number, fields: Partial<LimitSet> = {}): LimitSet {
  return {
    id: `s-${version}`,
    version,
    valid_from: '2026-11-01',
    comment: 'Политика версии 1.0',
    author: 'risk',
    created_at: '2026-11-02T09:00:00Z',
    body: BODY,
    ...fields,
  };
}

export function check(fields: Partial<CheckUsage> = {}): CheckUsage {
  return {
    code: 'Л3',
    name: 'Корпоративные облигации AAA и AA+',
    clause: 'п. 6.1',
    label: 'на эмитента',
    type: 'share',
    metric: null,
    status: 'warning',
    value: '9.6429',
    maximum: '10',
    minimum: null,
    usage: '96.43',
    groups: [
      { key: 'e-a', name: 'Эмитент А', value: '9.6429', status: 'ok' },
      { key: 'e-b', name: 'Эмитент Б', value: '8.9286', status: 'ok' },
    ],
    violations: [],
    missing: [],
    ...fields,
  };
}

/** Фонд «Доход 2» 10.11 (пример методики шага 9). */
export function fundDay(fields: Partial<FundDay> = {}): FundDay {
  return {
    fund: FUNDS[0]!,
    date: '2026-11-10',
    today: false,
    limit_set: limitSet(1),
    calculated: true,
    totals: {
      assets: '2800000000.00',
      liabilities: '1800000000.00',
      nav: '1000000000.00',
      leverage: '2.8000',
      duration: '0.4319',
    },
    checks: [
      check({
        code: 'Л1',
        name: 'Плечо',
        clause: 'п. 5.1',
        label: null,
        type: 'metric',
        metric: 'leverage',
        status: 'ok',
        value: '2.8000',
        maximum: '4.0',
        usage: '70.00',
        groups: [],
      }),
      check(),
      check({
        code: '4',
        name: 'Допустимые инструменты',
        clause: null,
        label: null,
        type: 'eligible',
        status: 'ok',
        value: '0.0000',
        maximum: null,
        usage: null,
        groups: [],
      }),
    ],
    ...fields,
  };
}

/** Подменный API: отвечает фиксированными данными, вызовы запоминает. */
export function fakeApi() {
  const answer = <T,>(value: T) => vi.fn((..._args: unknown[]) => Promise.resolve(value));
  return {
    funds: answer<Fund[]>([...FUNDS]),
    day: answer(fundDay()),
    sets: answer<LimitSet[]>([limitSet(1)]),
    saveSet: answer(limitSet(2)),
    classifiers: answer<Classifier[]>([...CLASSIFIERS]),
    issuers: answer<Issuer[]>([...ISSUERS]),
    instruments: answer<Instrument[]>([...INSTRUMENTS]),
  } satisfies Record<keyof LimitsApi, unknown>;
}

export type FakeApi = ReturnType<typeof fakeApi>;

/** Раздел под /limits, как в shell. */
export function renderLimits(api: FakeApi = fakeApi(), path = '/limits', canEdit = false) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/limits/*"
          element={<Limits api={api as unknown as LimitsApi} basePath="/limits" canEdit={canEdit} today="2026-11-17" />}
        />
      </Routes>
    </MemoryRouter>,
  );
  return api;
}
