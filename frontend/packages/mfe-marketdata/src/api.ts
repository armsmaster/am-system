import type { ApiClient } from '@am/platform';

export type PriceType =
  | 'vwap'
  | 'last'
  | 'bid'
  | 'ask'
  | 'close'
  | 'market_price'
  | 'admitted_quote'
  | 'dema';

export const PRICE_TYPES: readonly PriceType[] = [
  'vwap',
  'last',
  'bid',
  'ask',
  'close',
  'market_price',
  'admitted_quote',
  'dema',
];

/** Десятичное число строкой, как его отдаёт сервис: так не теряется точность. */
export type Decimal = string;

export interface Instrument {
  readonly id: string;
  readonly secid: string;
  readonly isin: string | null;
  readonly kind: 'bond' | 'fund_share';
  readonly short_name: string;
}

/** Цена для оценки и откуда она. */
export interface Choice {
  readonly value: Decimal;
  readonly type: PriceType | 'manual';
  readonly source: string;
  readonly time: string;
  readonly stale: boolean;
  readonly reason: string;
}

export interface Candidate {
  readonly type: PriceType;
  readonly source: string;
  readonly value: Decimal;
  readonly time: string;
  /** Цена за текущий торговый день. */
  readonly today: boolean;
}

export interface Rule {
  readonly price_types: readonly PriceType[];
  readonly dema_timeframe: number | null;
  readonly dema_half_life: Decimal | null;
  readonly spread_threshold_bp: number;
}

export interface RuleRecord extends Rule {
  readonly valid_from: string;
  readonly author: string;
  readonly comment: string;
}

export interface PriceView {
  readonly instrument: Instrument;
  readonly at: string;
  readonly trading_day: string;
  readonly price: Choice | null;
  readonly candidates: readonly Candidate[];
  readonly rule: Rule;
  readonly spread_bp: Decimal | null;
  readonly wide_spread: boolean;
}

export interface Quote {
  readonly type: PriceType;
  readonly source: string;
  readonly time: string;
  readonly value: Decimal;
}

export interface Bar {
  readonly start: string;
  readonly end: string;
  readonly open: Decimal;
  readonly high: Decimal;
  readonly low: Decimal;
  readonly close: Decimal;
}

export interface ManualPrice {
  readonly value: Decimal;
  readonly entered_at: string;
  readonly author: string;
  readonly comment: string;
}

export interface Series {
  readonly instrument: Instrument;
  readonly quotes: readonly Quote[];
  /** Таймфрейм в минутах → бары. */
  readonly bars: Readonly<Record<string, readonly Bar[]>>;
  readonly dema: readonly { readonly time: string; readonly value: Decimal }[];
  readonly manual: readonly ManualPrice[];
  readonly rules: readonly RuleRecord[];
}

export interface Rejected {
  readonly index: number;
  readonly code: string;
  readonly error: string;
  readonly message: string;
}

export interface Batch {
  readonly id: string;
  readonly client: string;
  readonly source: string;
  readonly kind: 'quotes' | 'bars' | 'rates';
  readonly received_at: string;
  readonly accepted: number;
  readonly rejected: readonly Rejected[];
}

export interface RuleInput extends Rule {
  readonly comment: string;
}

export type RateKind = 'key_rate' | 'ruonia' | 'rusfar';

/** Ставка на дату: последняя известная, её дата и источник; нет ставки — всё null. */
export type Rate = {
  readonly rate: RateKind;
  readonly label: string;
} & (
  | { readonly date: string; readonly value: Decimal; readonly source: string }
  | { readonly date: null; readonly value: null; readonly source: null }
);

export interface Horizon {
  readonly kind: 'offer' | 'maturity';
  readonly date: string;
  readonly price: Decimal;
}

/** Доходность и метрики к горизонту; ``problem`` — почему к нему не посчитать. */
export interface HorizonYield {
  readonly horizon: Horizon;
  readonly yield_percent: Decimal | null;
  readonly macaulay_years: Decimal | null;
  readonly macaulay_days: Decimal | null;
  readonly modified_duration: Decimal | null;
  readonly dv01: Decimal | null;
  readonly convexity: Decimal | null;
  readonly problem: { readonly code: string; readonly message: string } | null;
}

export interface Spread {
  readonly horizon: Horizon;
  /** Плавающая ставка флоатера; у линкера — null. */
  readonly rate: RateKind | null;
  readonly margin_bp: Decimal | null;
  readonly capitalized: boolean | null;
  readonly funding: Rate;
  readonly funding_percent: Decimal;
  readonly index: Rate | null;
  readonly spread_bp: Decimal;
  readonly yield_percent: Decimal;
  readonly breakdown: {
    readonly basis_bp: Decimal;
    readonly capitalization_bp: Decimal;
    readonly margin_bp: Decimal;
    readonly other_bp: Decimal;
    readonly price_bp: Decimal;
  };
  readonly spread_duration: Decimal;
  readonly spread_dv01: Decimal;
}

export interface Analytics {
  readonly instrument: Instrument;
  readonly at: string;
  readonly trading_day: string;
  readonly settlement_date: string;
  readonly type: 'fixed' | 'floater' | 'linker';
  readonly price: {
    readonly basis: 'valuation' | 'price' | 'yield' | 'spread';
    readonly clean: Decimal;
    readonly face_value: Decimal;
    readonly accrued: Decimal;
    readonly dirty: Decimal;
    readonly valuation: Choice | null;
  };
  readonly yields: readonly HorizonYield[];
  readonly spread: Spread | null;
}

/** «Что если»: одно из трёх; ничего — по цене для оценки. */
export interface WhatIf {
  readonly price?: string;
  readonly yield?: string;
  readonly spread?: string;
}

const BASE = '/api/marketdata';

/** Вызовы API сервиса рыночных данных. */
export function marketdataApi(client: ApiClient) {
  return {
    overview: (at?: string) => client.get<PriceView[]>(`${BASE}/overview${query({ at })}`),
    price: (id: string, at?: string) =>
      client.get<PriceView>(`${BASE}/instruments/${id}/price${query({ at })}`),
    series: (id: string, from?: string, to?: string) =>
      client.get<Series>(`${BASE}/instruments/${id}/series${query({ from, to })}`),
    rules: (id: string) => client.get<RuleRecord[]>(`${BASE}/instruments/${id}/rules`),
    setRule: (id: string, body: RuleInput) =>
      client.put<RuleRecord>(`${BASE}/instruments/${id}/rule`, body),
    manualPrice: (id: string, body: { value: string; comment: string }) =>
      client.post<ManualPrice>(`${BASE}/instruments/${id}/manual-prices`, body),
    batches: () => client.get<Batch[]>(`${BASE}/feed/batches`),
    analytics: (id: string, whatIf: WhatIf = {}) =>
      client.get<Analytics>(`${BASE}/instruments/${id}/analytics${query({ ...whatIf })}`),
    rates: (date?: string) => client.get<Rate[]>(`${BASE}/rates${query({ date })}`),
  };
}

export type MarketdataApi = ReturnType<typeof marketdataApi>;

function query(params: Record<string, string | undefined>): string {
  const filled = Object.entries(params).filter((entry): entry is [string, string] => !!entry[1]);
  return filled.length ? `?${new URLSearchParams(filled).toString()}` : '';
}
