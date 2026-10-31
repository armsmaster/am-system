import { ApiError, type Role, type Session } from '@am/platform';
import { render } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { vi } from 'vitest';

import { Booking } from '../App';
import type {
  Account,
  BookingApi,
  ImportReport,
  Instrument,
  Ladder,
  Operation,
  Portfolio,
  Positions,
  Trade,
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
    comment: null,
    author: 'trader',
    created_at: '2026-11-27T08:43:00Z',
    versions: [
      { number: 1, status: 'entered', author: 'trader', created_at: '2026-11-27T08:43:00Z', comment: null, changed: [] },
    ],
    warnings: [],
    ...fields,
  };
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
      },
    ],
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
      },
    ],
    ...fields,
  };
}

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
    as_of: '2026-11-29',
    waiting_since: null,
    instruments: [{ instrument_id: 'i-ofz', secid: 'SU26238RMFS4', short_name: 'ОФЗ 26238' }],
    days: [
      {
        date: '2026-11-30',
        trading: true,
        cash: '31999500.00',
        securities: ofz(50000),
        movements: [
          {
            portfolio_code: 'OPIF1-BONDS',
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
export function renderBooking(api: FakeApi = fakeApi(), path = '/booking', isOperations = false) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/booking/*"
          element={
            <>
              <Booking api={asApi(api)} basePath="/booking" isOperations={isOperations} />
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
