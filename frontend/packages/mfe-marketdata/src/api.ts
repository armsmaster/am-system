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
  readonly kind: 'quotes' | 'bars';
  readonly received_at: string;
  readonly accepted: number;
  readonly rejected: readonly Rejected[];
}

export interface RuleInput extends Rule {
  readonly comment: string;
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
  };
}

export type MarketdataApi = ReturnType<typeof marketdataApi>;

function query(params: Record<string, string | undefined>): string {
  const filled = Object.entries(params).filter((entry): entry is [string, string] => !!entry[1]);
  return filled.length ? `?${new URLSearchParams(filled).toString()}` : '';
}
