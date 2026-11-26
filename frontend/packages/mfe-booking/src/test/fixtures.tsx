import { ApiError, type Role, type Session } from '@am/platform';
import { render } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { vi } from 'vitest';

import { Booking } from '../App';
import type {
  Account,
  Attributes,
  BookingApi,
  DayResult,
  FeeRate,
  ImportReport,
  Instrument,
  Ladder,
  Nav,
  NavDay,
  NavHistory,
  Operation,
  Portfolio,
  Positions,
  QuikLine,
  PeriodResult,
  QuikReport,
  RatingScale,
  ResultSummary,
  Results,
  Trade,
  WhatIf,
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

/** Фонд «ОПИФ 1»: казначейский портфель и портфель облигаций (design/05, сквозной пример). */
export const PORTFOLIOS: Portfolio[] = [
  {
    id: 'p-fund',
    code: 'OPIF1',
    name: 'ОПИФ 1',
    kind: 'compound',
    is_fund: true,
    parent_ids: [],
    fund_id: null,
    treasury_id: 'p-treasury',
  },
  {
    id: 'p-treasury',
    code: 'OPIF1-TREASURY',
    name: 'Казначейский',
    kind: 'physical',
    is_fund: false,
    parent_ids: ['p-fund'],
    fund_id: 'p-fund',
  },
  {
    id: 'p-bonds',
    code: 'OPIF1-BONDS',
    name: 'Облигации',
    kind: 'physical',
    is_fund: false,
    parent_ids: ['p-fund'],
    fund_id: 'p-fund',
  },
];

export const ACCOUNTS: Account[] = [
  { id: 'a-broker', fund_id: 'p-fund', code: 'OPIF1-BRK', name: 'Брокерский', cash_kind: 'broker', institution: 'Брокер' },
  { id: 'a-bank', fund_id: 'p-fund', code: 'OPIF1-BANK', name: 'Банковский', cash_kind: 'bank', institution: 'Банк' },
];

export const INSTRUMENTS: Instrument[] = [
  { id: 'i-ofz', secid: 'SU26238RMFS4', kind: 'bond', short_name: 'ОФЗ 26238' },
  { id: 'i-lqdt', secid: 'LQDT', kind: 'fund_share', short_name: 'ВИМ Ликвидность' },
];

/** Покупка 50 000 ОФЗ 26238 по 52,50% 27.11, расчёты 30.11. */
export function trade(fields: Partial<Trade> = {}): Trade {
  return {
    id: 't-1',
    number: 1,
    status: 'entered',
    portfolio_id: 'p-bonds',
    portfolio_code: 'OPIF1-BONDS',
    instrument_id: 'i-ofz',
    instrument_kind: 'bond',
    secid: 'SU26238RMFS4',
    short_name: 'ОФЗ 26238',
    side: 'buy',
    quantity: 50000,
    price: '52.500000',
    face_value: '1000.000000',
    accrued: '35.01',
    amount: '28000500.00',
    fee: '0.00',
    trade_at: '2026-11-27T08:42:00Z',
    trade_date: '2026-11-27',
    settle_date: '2026-11-30',
    venue: 'exchange',
    counterparty: null,
    external_id: null,
    kind: 'outright',
    repo_rate: null,
    second_date: null,
    second_amount: null,
    comment: null,
    author: 'trader',
    created_at: '2026-11-27T08:43:00Z',
    versions: [
      { number: 1, status: 'entered', author: 'trader', created_at: '2026-11-27T08:43:00Z', comment: null, changed: [] },
    ],
    warnings: [],
    closed_through: null,
    ...fields,
  };
}

/** Прямое РЕПО Б из методики шага 7: 10 000 ОФЗ 26238 на 30.11–03.12 под 14,50%. */
export function repoTrade(fields: Partial<Trade> = {}): Trade {
  return trade({
    id: 't-repo',
    kind: 'repo',
    side: 'sell',
    quantity: 10000,
    price: '52.600000',
    amount: '5610100.00',
    trade_at: '2026-11-30T08:05:00Z',
    trade_date: '2026-11-30',
    settle_date: '2026-11-30',
    repo_rate: '14.500000',
    second_date: '2026-12-03',
    second_amount: '5616786.01',
    ...fields,
  });
}

/** Портфель облигаций на 02.12: купон пришёл, лот 30 000 по 52,50%. */
export function positions(fields: Partial<Positions> = {}): Positions {
  return {
    portfolio_id: 'p-bonds',
    code: 'OPIF1-BONDS',
    name: 'Облигации',
    date: '2026-12-02',
    cash: '-15714300.00',
    units: '0',
    claims: '0',
    obligations: '0',
    expenses: '0',
    portfolios: [
      {
        id: 'p-bonds',
        code: 'OPIF1-BONDS',
        name: 'Облигации',
        as_of: '2026-12-02',
        waiting_since: null,
        cash: '-15714300.00',
        units: '0',
        claims: '0',
        obligations: '0',
        expenses: '0',
        movements: [
          { kind: 'coupon', instrument_id: 'i-ofz', trade_id: null, operation_id: null, quantity: 0, cash: '1062000.00' },
        ],
        result: dayResult(),
      },
    ],
    result: dayResult(),
    dv01_yield: '9780.00',
    dv01_spread: '9780.00',
    repos: [],
    closed: {
      week: { result: '0.00', realized: '0.00' },
      days30: { result: '0.00', realized: '0.00' },
      month: { result: '0.00', realized: '0.00' },
      quarter: { result: '0.00', realized: '0.00' },
      year: { result: '0.00', realized: '0.00' },
    },
    previous_day: '2026-12-01',
    next_day: '2026-12-03',
    lines: [
      {
        instrument_id: 'i-ofz',
        kind: 'bond',
        secid: 'SU26238RMFS4',
        short_name: 'ОФЗ 26238',
        trade_quantity: 30000,
        settle_quantity: 30000,
        cost: '15750000.00',
        accrued_paid: '1050300.00',
        average_price: '52.500000',
        lots: [
          {
            portfolio_code: 'OPIF1-BONDS',
            trade_id: 't-1',
            opened_at: '2026-11-27T08:42:00Z',
            quantity: 30000,
            price: '52.500000',
            face: '1000.000000',
            cost: '15750000.00',
            accrued_paid: '1050300.00',
          },
        ],
        price: '52.700000',
        clean: '15810000.00',
        accrued_value: '0.00',
        coupon_due: '0.00',
        principal_due: '0.00',
        unrealized: '60000.00',
        revaluation: '36000.00',
        realized: '0.00',
        coupon: '5700.00',
        fees: '0.00',
        result: '41700.00',
        flags: [],
        dv01: '9780.00',
        duration: '5.89',
        metrics_basis: 'yield',
        bond_type: 'fixed',
        yield_percent: '15.935227',
        yield_continuous: '14.786146',
        spread_bp: null,
        spread_continuous_bp: null,
        horizon_kind: 'maturity',
        horizon_date: '2041-05-15',
        duration_continuous: '6.82',
        dv01_continuous: '11337.00',
        // Результат дня по таблице методики: 27.11 +25 000, 30.11 +41 000 (продажа: +20 000
        // реализованных), 01.12 −6 300, 02.12 +41 700 (решение 164).
        periods: {
          week: { result: '101400.00', realized: '20000.00' },
          days30: { result: '101400.00', realized: '20000.00' },
          month: { result: '35400.00', realized: '0.00' },
          quarter: { result: '101400.00', realized: '20000.00' },
          year: { result: '101400.00', realized: '20000.00' },
        },
      },
    ],
    ...fields,
  };
}

/** Справочник на дату: сектор эмитента, очерёдность выпуска, рейтинги двух агентств. */
export const ATTRIBUTES: Attributes = {
  date: '2026-12-02',
  classifiers: [
    {
      id: 'c-sector',
      name: 'Сектор',
      target: 'issuer',
      values: [
        { id: 'v-gov', name: 'Государство' },
        { id: 'v-fin', name: 'Финансы' },
      ],
    },
    { id: 'c-rank', name: 'Очередность', target: 'instrument', values: [{ id: 'v-senior', name: 'Старший' }] },
  ],
  instruments: [
    {
      id: 'i-ofz',
      issuer_id: 'e-minfin',
      coupon_kind: 'fixed',
      nominal_indexation: 'none',
      classes: { 'c-rank': 'v-senior' },
      ratings: { status: 'unknown', ratings: [] },
    },
  ],
  issuers: [
    {
      id: 'e-minfin',
      name: 'Минфин России',
      classes: { 'c-sector': 'v-gov' },
      ratings: {
        status: 'rated',
        ratings: [
          { agency: 'expert', agency_name: 'Эксперт РА', rating: 'ruAA+', grade: 'AA+', since: '2025-03-01' },
          { agency: 'acra', agency_name: 'АКРА', rating: 'AAA(RU)', grade: 'AAA', since: '2025-01-15' },
        ],
      },
    },
  ],
};

export const SCALE: RatingScale = { grades: ['AAA', 'AA+', 'AA', 'AA-', 'A+', 'A', 'A-'] };

/** Портфель облигаций на 02.12: купон пришёл, цена выросла (design/06, сквозной пример). */
export function dayResult(fields: Partial<DayResult> = {}): DayResult {
  return {
    value: '60103400.00',
    flows: '0.00',
    revaluation: '36000.00',
    realized: '0.00',
    coupon: '5700.00',
    fees: '0.00',
    expenses: '0.00',
    charges: '0.00',
    correction: '0.00',
    repo: '0.00',
    total: '41700.00',
    receivable: '0.00',
    payable: '0.00',
    flags: [],
    ...fields,
  };
}

/** Результат фонда за 27.11–30.11 (design/06): без вознаграждения УК. */
export function results(fields: Partial<Results> = {}): Results {
  const day = (date: string, value: string, revaluation: string, realized: string, coupon: string, total: string) => ({
    date,
    value,
    flows: '0.00',
    revaluation,
    realized,
    coupon,
    fees: '0.00',
    expenses: '0.00',
    charges: '0.00',
    correction: '0.00',
    repo: '0.00',
    total,
    flags: [],
  });
  return {
    portfolio_id: 'p-fund',
    code: 'OPIF1',
    name: 'ОПИФ 1',
    first: '2026-11-27',
    last: '2026-11-30',
    portfolios: ['OPIF1-BONDS', 'OPIF1-TREASURY'],
    waiting_since: null,
    days: [
      day('2026-11-27', '100025000.00', '25000.00', '0.00', '0.00', '25000.00'),
      day('2026-11-30', '100066000.00', '11000.00', '20000.00', '10000.00', '41000.00'),
    ],
    lines: [
      {
        instrument_id: 'i-ofz',
        secid: 'SU26238RMFS4',
        short_name: 'ОФЗ 26238',
        revaluation: '36000.00',
        realized: '20000.00',
        coupon: '10000.00',
        fees: '0.00',
        total: '66000.00',
      },
    ],
    ...fields,
  };
}

/** СЧА «ОПИФ 1» за 29.11–30.11 (design/06): вознаграждение УК 1,5%. */
export function nav(fields: Partial<Nav> = {}): Nav {
  const day = (date: string, value: string, price: string, fee: string, owed: string): NavDay => ({
    date,
    nav: value,
    units: '100000.00000',
    unit_price: price,
    cash: '71999500.00',
    clean: '15786000.00',
    accrued: '1056300.00',
    principal_due: '0.00',
    receivable: '11224200.00',
    payable: '0.00',
    claims: '0.00',
    obligations: '0.00',
    expenses: '0.00',
    fees_owed: owed,
    fees_today: { management: fee },
    repo_placed: '0.00',
    repo_borrowed: '0.00',
    flags: [],
  });
  return {
    fund_id: 'p-fund',
    code: 'OPIF1',
    name: 'ОПИФ 1',
    first: '2026-11-29',
    last: '2026-11-30',
    waiting_since: null,
    days: [
      day('2026-11-29', '100008559.57', '1000.09', '4110.11', '16440.43'),
      day('2026-11-30', '100045447.94', '1000.45', '4111.63', '20552.06'),
    ],
    ...fields,
  };
}

export const FEE_RATES: FeeRate[] = [
  {
    kind: 'management',
    rate: '1.500000',
    valid_from: '2026-11-26',
    portfolio_id: 'p-treasury',
    comment: 'ПДУ п. 92',
    author: 'ops',
    created_at: '2026-11-26T09:00:00Z',
  },
];

/** Выдача 100 000 паёв на 100 млн ₽ 26.11, расчёт ещё не отмечен. */
export function operation(fields: Partial<Operation> = {}): Operation {
  return {
    id: 'o-1',
    number: 1,
    status: 'active',
    kind: 'unit_issue',
    fund_id: 'p-fund',
    portfolio_id: 'p-treasury',
    portfolio_code: 'OPIF1-TREASURY',
    receiver_id: null,
    receiver_code: null,
    op_date: '2026-11-26',
    amount: '100000000.00',
    units: '100000.00000',
    planned_date: '2026-11-27',
    settled_date: null,
    expense_kind: null,
    fee_kind: null,
    period_end: null,
    trade_id: null,
    direction: null,
    instrument_id: null,
    secid: null,
    short_name: null,
    quantity: null,
    comment: null,
    author: 'ops',
    created_at: '2026-11-26T09:00:00Z',
    versions: [
      { number: 1, status: 'active', author: 'ops', created_at: '2026-11-26T09:00:00Z', comment: null, changed: [] },
    ],
    warnings: [],
    ...fields,
  };
}

/** Лестница брокерского счёта в понедельник 30.11 (design/05, «Лестница расчётов»). */
export function ladder(fields: Partial<Ladder> = {}): Ladder {
  const ofz = (quantity: number) => [{ instrument_id: 'i-ofz', quantity }];
  return {
    account_id: 'a-broker',
    portfolios: [{ id: 'p-bonds', code: 'OPIF1-BONDS', name: 'Облигации' }],
    today: '2026-11-30',
    as_of: '2026-11-29',
    waiting_since: null,
    instruments: [{ instrument_id: 'i-ofz', secid: 'SU26238RMFS4', short_name: 'ОФЗ 26238' }],
    forecasts: [],
    days: [
      {
        date: '2026-11-30',
        trading: true,
        cash: '31999500.00',
        securities: ofz(50000),
        movements: [
          {
            portfolio_code: 'OPIF1-BONDS',
            repo_term: null,
            kind: 'settlement',
            instrument_id: 'i-ofz',
            trade_id: 't-1',
            operation_id: null,
            quantity: 50000,
            cash: '-28000500.00',
          },
        ],
      },
      {
        date: '2026-12-01',
        trading: true,
        cash: '43223700.00',
        securities: ofz(30000),
        movements: [
          {
            portfolio_code: 'OPIF1-BONDS',
            repo_term: null,
            kind: 'settlement',
            instrument_id: 'i-ofz',
            trade_id: 't-2',
            operation_id: null,
            quantity: -20000,
            cash: '11224200.00',
          },
        ],
      },
      {
        date: '2026-12-02',
        trading: true,
        cash: '44285700.00',
        securities: ofz(30000),
        movements: [
          {
            portfolio_code: 'OPIF1-BONDS',
            repo_term: null,
            kind: 'coupon',
            instrument_id: 'i-ofz',
            trade_id: null,
            operation_id: null,
            quantity: 0,
            cash: '1062000.00',
          },
        ],
      },
      { date: '2026-12-03', trading: true, cash: '44285700.00', securities: ofz(30000), movements: [] },
    ],
    ...fields,
  };
}

/** Покупка облигаций Эмитента А 13.11 (пример методики шага 9): Л3 на эмитента нарушен. */
export function whatIf(fields: Partial<WhatIf> = {}): WhatIf {
  return {
    limit_set: { version: 1 },
    calculated: true,
    changes: [
      {
        code: 'Л3',
        name: 'Корпоративные облигации AAA и AA+',
        label: 'на эмитента',
        type: 'share',
        metric: null,
        group: 'Эмитент А',
        before: '9.6429',
        after: '10.1786',
        before_status: 'warning',
        after_status: 'breach',
        maximum: '10',
        minimum: null,
        reasons: [],
      },
    ],
    ...fields,
  };
}

/** Строка отчёта QUIK: покупка ОФЗ 26238 — новая сделка, комиссия по тарифу 0,0125%. */
export function quikLine(fields: Partial<QuikLine> = {}): QuikLine {
  return {
    line: 1,
    external_id: '18100000001',
    kind: 'outright',
    code: 'RU000A1038V6',
    name: 'ОФЗ 26238',
    side: 'buy',
    trade_at: '2026-11-27T11:05:00+03:00',
    quantity: 100,
    price: '52.50',
    amount: '56001.00',
    fee: '6.56',
    fee_rate: '0.012500',
    result: 'created',
    trade_id: null,
    error: null,
    message: null,
    warnings: [],
    ...fields,
  };
}

/** Проверка отчёта QUIK: сделка, РЕПО, отказ и строка, которую не разобрать. */
export function quikReport(fields: Partial<QuikReport> = {}): QuikReport {
  return {
    client_code: '00000T_01',
    period_from: '2026-11-27',
    period_till: '2026-11-30',
    tariff: {
      outright_rate: '0.012500',
      repo_rate: '0.500000',
      rps_rate: '0.020000',
      valid_from: '2026-11-01',
      comment: null,
      author: 'admin',
      created_at: '2026-10-30T10:00:00+03:00',
    },
    applied: false,
    new_instruments: [],
    lines: [
      quikLine(),
      quikLine({
        line: 2,
        external_id: '18100000002',
        side: 'sell',
        result: 'unchanged',
        warnings: ['Продажа больше позиции.'],
      }),
      quikLine({
        line: 4,
        external_id: '18100000004',
        code: 'RU000A10FV69',
        name: 'РЖД 1Р-54R',
        result: null,
        error: 'unknown_instrument',
        message: 'Бумаги RU000A10FV69 нет в справочнике.',
      }),
      quikLine({
        line: 6,
        external_id: '18100000006',
        code: '',
        name: '',
        side: null,
        trade_at: null,
        quantity: null,
        price: null,
        amount: null,
        fee: null,
        fee_rate: null,
        result: null,
        error: 'bad_row',
        message: 'Строку не разобрать.',
      }),
      quikLine({
        line: 7,
        external_id: '18100000101',
        kind: 'repo',
        code: 'RU000A0JW4Z1',
        name: 'КСУ обл.',
        quantity: null,
        price: null,
        amount: '30000000.00',
        fee: '1232.88',
        fee_rate: '0.500000',
      }),
      quikLine({
        line: 10,
        external_id: '18100000201',
        kind: 'repo',
        side: 'sell',
        code: 'RU000A0JW4Z1',
        name: 'КСУ обл.',
        quantity: null,
        price: null,
        amount: '23000000.00',
        fee: '315.07',
        fee_rate: '0.500000',
        result: 'amended',
      }),
    ],
    ...fields,
  };
}

/** Период отчёта: по умолчанию — неделя фонда из методики (27.11–03.12). */
export function period(fields: Partial<PeriodResult> = {}): PeriodResult {
  return {
    key: 'week',
    first: '2026-11-27',
    last: '2026-12-03',
    days: 7,
    revaluation: '48000.00',
    realized: '20000.00',
    coupon: '27400.00',
    fees: '0.00',
    repo: '0.00',
    expenses: '0.00',
    charges: '0.00',
    correction: '0.00',
    total: '95400.00',
    flows: '0.00',
    start_value: '100000000.00',
    end_value: '100095400.00',
    invested: '100000000.00',
    return_percent: '0.0954',
    annual_percent: '4.9744',
    flags: [],
    ...fields,
  };
}

/** Отчёт о результате фонда на 03.12 (решение 167): неделя методики и проданная бумага. */
export function resultSummary(fields: Partial<ResultSummary> = {}): ResultSummary {
  const longer = { flows: '100000000.00', start_value: '0.00', annual_percent: '4.3526' };
  const year = { realized: '24484.00', total: '99884.00' };
  return {
    portfolio_id: 'p-fund',
    code: 'OPIF1',
    name: 'ОПИФ 1',
    date: '2026-12-03',
    first_day: '2026-11-26',
    portfolios: ['OPIF1-BONDS', 'OPIF1-TREASURY'],
    waiting_since: null,
    periods: [
      period({
        key: 'day',
        first: '2026-12-03',
        days: 1,
        revaluation: '-6000.00',
        realized: '0.00',
        coupon: '0.00',
        total: '-6000.00',
        start_value: '100101400.00',
        invested: '100101400.00',
        return_percent: '-0.0060',
        annual_percent: '-2.1878',
      }),
      period(),
      period({ key: 'days30', first: '2026-11-04', days: 30, invested: '26666666.67', return_percent: '0.3578', ...longer }),
      period({ key: 'quarter', first: '2026-10-01', days: 64, invested: '12500000.00', return_percent: '0.7632', ...longer }),
      period({ key: 'year', first: '2026-01-01', days: 337, invested: '2373887.24', return_percent: '4.2076', ...longer, ...year }),
      period({ key: 'all', first: '2026-11-26', days: 8, invested: '100000000.00', ...longer, ...year }),
    ],
    lines: [
      {
        instrument_id: 'i-ofz',
        secid: 'SU26238RMFS4',
        short_name: 'ОФЗ 26238',
        held: true,
        totals: { day: '-6000.00', week: '95400.00', days30: '95400.00', quarter: '95400.00', year: '95400.00', all: '95400.00' },
      },
      {
        instrument_id: 'i-old',
        secid: 'RU000A10OLD1',
        short_name: 'Старый выпуск',
        held: false,
        totals: { day: '0.00', week: '0.00', days30: '0.00', quarter: '0.00', year: '4484.00', all: '4484.00' },
      },
    ],
    expenses_owed: '0.00',
    fees_owed: '0.00',
    ...fields,
  };
}

/** Стандартные настройки фильтра Калмана (решения 168, 174). */
export const STANDARD_FILTER = {
  half_life: '2.25',
  trend_memory: '52.2',
  q_level: '0.05',
  q_trend: '0.0005',
  standard: true,
  standard_half_life: '2.25',
  standard_trend_memory: '52.2',
};

/** СЧА фонда за неделю методики (решение 168): 100 000 паёв, LQDT с 27.11, прогноз на 04.12. */
export function navHistory(fields: Partial<NavHistory> = {}): NavHistory {
  const point = (date: string, unit: string, smoothed: string, benchmark: string | null) => ({
    date,
    nav: (Number(unit) * 100000).toFixed(2),
    units: '100000.00000',
    unit_price: unit,
    smoothed,
    benchmark,
  });
  return {
    fund_id: 'p-fund',
    code: 'OPIF1',
    name: 'ОПИФ 1',
    first_day: '2026-11-26',
    today: '2026-12-03',
    waiting_since: null,
    benchmark: 'LQDT',
    points: [
      point('2026-11-26', '1000.00', '1000.00', null),
      point('2026-11-27', '1000.25', '1000.25', '2.1000'),
      point('2026-11-28', '1000.25', '1000.26', '2.1000'),
      point('2026-11-29', '1000.25', '1000.27', '2.1000'),
      point('2026-11-30', '1000.66', '1000.53', '2.1004'),
      point('2026-12-01', '1000.60', '1000.64', '2.1008'),
      point('2026-12-02', '1001.01', '1000.90', '2.1020'),
      point('2026-12-03', '1000.95', '1001.02', '2.1024'),
    ],
    forecast: { date: '2026-12-04', value: '1001.12', low: '1000.98', high: '1001.26', trend: '0.1050', trend_percent: '3.83' },
    filter: STANDARD_FILTER,
    latest: [point('2026-12-02', '1001.01', '1000.90', null), point('2026-12-03', '1000.95', '1001.02', null)],
    ...fields,
  };
}

/** Подменный API: отвечает фиксированными данными, вызовы запоминает. */
export function fakeApi() {
  const answer = <T,>(value: T) => vi.fn((..._args: unknown[]) => Promise.resolve(value));
  return {
    portfolios: answer<Portfolio[]>(PORTFOLIOS),
    instruments: answer<Instrument[]>(INSTRUMENTS),
    defaults: answer({
      instrument_id: 'i-ofz',
      kind: 'bond' as const,
      settle_date: '2026-11-30',
      face_value: '1000' as string | null,
      accrued: '35.01' as string | null,
    }),
    trades: answer<Trade[]>([trade()]),
    trade: answer(trade()),
    enter: answer(trade()),
    amend: answer(trade({ number: 2 })),
    cancel: answer(trade({ status: 'cancelled' })),
    confirm: answer(trade({ status: 'confirmed' })),
    positions: answer(positions()),
    accounts: answer<Account[]>(ACCOUNTS),
    operations: answer<Operation[]>([operation()]),
    enterOperation: answer(operation()),
    settleOperation: answer(operation({ settled_date: '2026-11-27' })),
    cancelOperation: answer(operation({ status: 'cancelled' })),
    ladder: answer(ladder()),
    limitsCheck: answer<WhatIf>(whatIf()),
    attributes: answer<Attributes>(ATTRIBUTES),
    ratingScale: answer<RatingScale>(SCALE),
    results: answer<Results>(results()),
    resultSummary: answer<ResultSummary>(resultSummary()),
    navHistory: answer<NavHistory>(navHistory()),
    navWorkbook: answer<Blob>(new Blob(['xlsx'])),
    nav: answer<Nav>(nav()),
    feeRates: answer<FeeRate[]>(FEE_RATES),
    setFeeRate: answer<FeeRate[]>(FEE_RATES),
    importQuik: answer<QuikReport>(quikReport()),
    importTrades: answer<ImportReport>({
      created: 1,
      amended: 0,
      cancelled: 0,
      unchanged: 1,
      trades: [
        { index: 0, external_id: 'BX-0042', result: 'unchanged', trade_id: 't-1', warnings: [] },
        {
          index: 2,
          external_id: 'BX-0017',
          result: 'created',
          trade_id: 't-2',
          warnings: ['Продажа больше позиции: на 30.11.2026 в портфеле 0 шт., продаётся 20000.'],
        },
      ],
      rejected: [
        {
          index: 1,
          external_id: 'BX-0051',
          error: 'unknown_instrument',
          message: 'Бумаги LQDT нет в справочнике. Загрузите её в разделе «Справочник».',
        },
      ],
    }),
  } satisfies Record<keyof BookingApi, unknown>;
}

export type FakeApi = ReturnType<typeof fakeApi>;

export function asApi(fake: FakeApi): BookingApi {
  return fake as unknown as BookingApi;
}

export function refused(message: string): ApiError {
  return new ApiError(422, { detail: { code: 'rule', message } });
}

/** Раздел «Сделки» под /booking, как в shell; адрес страницы — в элементе «Адрес». */
export function renderBooking(
  api: FakeApi = fakeApi(),
  path = '/booking',
  isOperations = false,
  seesLimits = false,
) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/booking/*"
          element={
            <>
              <Booking api={asApi(api)} basePath="/booking" isOperations={isOperations} seesLimits={seesLimits} />
              <Where />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
  return api;
}

function Where() {
  const location = useLocation();
  return <output aria-label="Адрес">{`${location.pathname}${location.search}`}</output>;
}
