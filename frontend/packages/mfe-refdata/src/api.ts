import { ApiError, type ApiClient } from '@am/platform';

export type InstrumentKind = 'bond' | 'fund_share';
export type CouponKind = 'fixed' | 'floater' | 'unknown';
export type RateKind = 'key_rate' | 'ruonia' | 'rusfar' | 'other';
export type NominalIndexation = 'none' | 'ruonia' | 'unknown';
export type OfferKind = 'put' | 'call';

/** Десятичное число строкой, как его отдаёт сервис: так не теряется точность. */
export type Decimal = string;
/** Дата в формате ГГГГ-ММ-ДД. */
export type IsoDate = string;

export interface Coupon {
  readonly date: IsoDate;
  readonly start_date: IsoDate | null;
  readonly record_date: IsoDate | null;
  readonly rate: Decimal | null;
  readonly amount: Decimal | null;
}

export interface Amortization {
  readonly date: IsoDate;
  readonly percent: Decimal | null;
  readonly amount: Decimal | null;
  readonly is_maturity: boolean;
}

export interface Offer {
  readonly date: IsoDate;
  readonly kind: OfferKind;
  readonly window_start: IsoDate | null;
  readonly window_end: IsoDate | null;
  readonly price: Decimal | null;
  readonly iss_type: string | null;
}

export interface FloaterTerms {
  readonly rate: RateKind;
  readonly margin_bp: Decimal;
  readonly capitalized: boolean;
}

export interface Params {
  readonly name: string;
  readonly short_name: string;
  readonly board: string;
  readonly currency: string;
  readonly issuer: string | null;
  readonly face_value: Decimal | null;
  readonly initial_face_value: Decimal | null;
  readonly issue_date: IsoDate | null;
  readonly maturity_date: IsoDate | null;
  readonly coupon_kind: CouponKind | null;
  readonly coupon_frequency: number | null;
  readonly nominal_indexation: NominalIndexation;
  readonly coupons: readonly Coupon[];
  readonly amortizations: readonly Amortization[];
  readonly offers: readonly Offer[];
  readonly floater: FloaterTerms | null;
}

export type Field = keyof Params;

export interface Version {
  readonly number: number;
  readonly valid_from: IsoDate | null;
  readonly source: 'iss' | 'manual';
  readonly author: string;
  readonly comment: string | null;
  readonly created_at: string;
  readonly pinned: readonly Field[];
  readonly params: Params;
}

export type VersionHeader = Omit<Version, 'params'>;

export interface Alert {
  readonly code: string;
  readonly message: string;
}

export interface InstrumentSummary {
  readonly id: string;
  readonly secid: string;
  readonly isin: string | null;
  readonly kind: InstrumentKind;
  readonly name: string;
  readonly short_name: string;
  readonly added_by: string;
  readonly last_synced_at: string | null;
  readonly last_sync_error: string | null;
}

export interface Instrument extends InstrumentSummary {
  readonly current: Version;
  readonly versions: readonly VersionHeader[];
  readonly alerts: readonly Alert[];
}

/** Что будет загружено из ISS. */
export interface Lookup {
  readonly secid: string;
  readonly isin: string | null;
  readonly kind: InstrumentKind;
  readonly params: Params;
  /** Бумага уже в справочнике. */
  readonly instrument_id: string | null;
}

/** Похожая бумага, если ISS не нашёл точного совпадения. */
export interface Candidate {
  readonly secid: string;
  readonly isin: string | null;
  readonly short_name: string;
  readonly name: string;
  readonly type_name: string | null;
}

export interface Edit {
  readonly valid_from: IsoDate;
  readonly comment: string;
  readonly changes: Partial<Params>;
  readonly unpin: readonly Field[];
}

export interface CalendarDay {
  readonly day: IsoDate;
  readonly is_trading: boolean;
  readonly comment: string | null;
}

const BASE = '/api/refdata';

/** Вызовы API сервиса справочника. */
export function refdataApi(client: ApiClient) {
  return {
    lookup: (code: string) => client.get<Lookup>(`${BASE}/lookup?${query({ code })}`),
    load: (code: string) => client.post<Instrument>(`${BASE}/instruments`, { code }),
    search: (text: string) =>
      client.get<InstrumentSummary[]>(`${BASE}/instruments${text ? `?${query({ query: text })}` : ''}`),
    instrument: (id: string) => client.get<Instrument>(`${BASE}/instruments/${id}`),
    version: (id: string, number: number) =>
      client.get<Version>(`${BASE}/instruments/${id}/versions/${number}`),
    edit: (id: string, body: Edit) =>
      client.post<Instrument>(`${BASE}/instruments/${id}/versions`, body),
    sync: (id: string) => client.post<Instrument>(`${BASE}/instruments/${id}/sync`, {}),

    calendar: (year: number) => client.get<CalendarDay[]>(`${BASE}/calendar/${year}`),
    setDay: (day: IsoDate, body: { is_trading: boolean; comment: string | null }) =>
      client.put<null>(`${BASE}/calendar/days/${day}`, body),
    clearDay: (day: IsoDate) => client.delete<null>(`${BASE}/calendar/days/${day}`),
    addTradingDays: (day: IsoDate, days: number) =>
      client.get<{ date: IsoDate }>(
        `${BASE}/calendar/add-trading-days?${query({ date: day, days: String(days) })}`,
      ),
  };
}

export type RefdataApi = ReturnType<typeof refdataApi>;

function query(params: Record<string, string>): string {
  return new URLSearchParams(params).toString();
}

/** Похожие бумаги из ответа «ISS не нашёл бумагу»; для других ошибок — пусто. */
export function candidatesOf(error: unknown): readonly Candidate[] {
  if (!(error instanceof ApiError)) {
    return [];
  }
  const detail = (error.body as { detail?: { code?: string; candidates?: Candidate[] } } | null)
    ?.detail;
  return detail?.code === 'not_in_iss' ? (detail.candidates ?? []) : [];
}
