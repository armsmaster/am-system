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

/** Ставка и маржа флоатера, как их публикует ISS; капитализацию ISS не сообщает. */
export interface IssFloater {
  /** Код ставки в ISS: RREFKEYR, RUONIA… */
  readonly benchmark: string;
  readonly rate: RateKind;
  readonly margin_bp: Decimal | null;
}

export interface Params {
  readonly name: string;
  readonly short_name: string;
  readonly board: string;
  readonly currency: string;
  readonly issuer: string | null;
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
  readonly iss_floater: IssFloater | null;
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
  /** Для подписи о купоне и быстрых фильтров — из последней версии. */
  readonly issuer_name: string | null;
  readonly coupon_kind: CouponKind | null;
  readonly nominal_indexation: NominalIndexation;
  /** % годовых: ближайший купон с известной ставкой. */
  readonly coupon_rate: Decimal | null;
  readonly floater_rate: RateKind | null;
  /** Спред к базе флоатера, бп. */
  readonly floater_margin_bp: Decimal | null;
  readonly maturity_date: IsoDate | null;
  /** Ближайшая будущая оферта. */
  readonly next_offer_date: IsoDate | null;
}

export interface IssuerRef {
  readonly id: string;
  readonly name: string;
}

export interface Instrument extends InstrumentSummary {
  readonly current: Version;
  readonly versions: readonly VersionHeader[];
  readonly alerts: readonly Alert[];
  /** Эмитент по коду ISS; у бумаг, загруженных раньше, — после сверки с ISS. */
  readonly issuer: IssuerRef | null;
  /** Тип бумаги ISS: ofz_bond, exchange_bond… */
  readonly iss_type: string | null;
}

export interface IssuerSummary {
  readonly id: string;
  readonly iss_id: number;
  readonly name: string;
  readonly inn: string | null;
  /** Бумаг эмитента в справочнике. */
  readonly instruments: number;
}

export interface Issuer extends Omit<IssuerSummary, 'instruments'> {
  readonly instruments: readonly InstrumentSummary[];
}

/** Бумага или эмитент — ровно одно из двух. */
export type Subject = { readonly instrument_id: string } | { readonly issuer_id: string };

export type RatingAction = 'assigned' | 'withdrawn' | 'unrated';
/** unknown — рейтинг ещё не вводили; unrated — рейтинга нет. */
export type RatingStatus = 'rated' | 'unrated' | 'unknown';

export interface AgencyRating {
  readonly agency: string;
  readonly agency_name: string;
  /** Запись агентства: AA-(RU). */
  readonly rating: string;
  /** Ступень общей шкалы: AA-. */
  readonly grade: string;
  readonly since: IsoDate;
}

export interface RatingRecord {
  readonly id: string;
  readonly action: RatingAction;
  readonly agency: string | null;
  readonly agency_name: string | null;
  readonly rating: string | null;
  readonly grade: string | null;
  readonly valid_from: IsoDate;
  readonly comment: string | null;
  readonly author: string;
  readonly created_at: string;
  readonly cancelled_by: string | null;
  readonly cancelled_at: string | null;
  readonly cancel_comment: string | null;
}

export interface Ratings {
  readonly now: { readonly status: RatingStatus; readonly ratings: readonly AgencyRating[] };
  readonly records: readonly RatingRecord[];
}

export interface Agency {
  readonly code: string;
  readonly name: string;
  /** Записи агентства — от высшей ступени к низшей. */
  readonly ratings: readonly string[];
}

export interface RatingScale {
  readonly agencies: readonly Agency[];
  readonly grades: readonly string[];
}

export interface NewRating {
  readonly action: RatingAction;
  readonly agency: string | null;
  readonly rating: string | null;
  readonly valid_from: IsoDate;
  readonly comment: string | null;
}

export type Target = 'instrument' | 'issuer';

export interface ClassValue {
  readonly id: string;
  readonly name: string;
}

export interface Classifier {
  readonly id: string;
  readonly name: string;
  readonly target: Target;
  readonly description: string | null;
  readonly created_by: string;
  readonly created_at: string;
  readonly values: readonly ClassValue[];
}

export interface NewClassifier {
  readonly name: string;
  readonly target: Target;
  readonly description: string | null;
  readonly values: readonly string[];
}

export interface ClassificationRecord {
  readonly id: string;
  readonly classifier_id: string;
  readonly classifier: string;
  readonly value_id: string;
  readonly value: string;
  /** null — с начала обращения. */
  readonly valid_from: IsoDate | null;
  readonly comment: string | null;
  readonly author: string;
  readonly created_at: string;
  readonly cancelled_by: string | null;
  readonly cancelled_at: string | null;
  readonly cancel_comment: string | null;
}

export interface CurrentClass {
  readonly classifier_id: string;
  readonly classifier: string;
  readonly value_id: string;
  readonly value: string;
  readonly since: IsoDate | null;
}

export interface Classifications {
  readonly now: readonly CurrentClass[];
  readonly records: readonly ClassificationRecord[];
}

export interface NewClassification {
  readonly classifier_id: string;
  readonly value_id: string;
  readonly valid_from: IsoDate | null;
  readonly comment: string | null;
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

    issuers: (text: string) =>
      client.get<IssuerSummary[]>(`${BASE}/issuers${text ? `?${query({ query: text })}` : ''}`),
    issuer: (id: string) => client.get<Issuer>(`${BASE}/issuers/${id}`),

    ratingScale: () => client.get<RatingScale>(`${BASE}/ratings/scale`),
    ratings: (subject: Subject) => client.get<Ratings>(`${BASE}/ratings?${query(subject)}`),
    addRating: (subject: Subject, body: NewRating) =>
      client.post<Ratings>(`${BASE}/ratings`, { ...subject, ...body }),
    cancelRating: (id: string, comment: string) =>
      client.post<Ratings>(`${BASE}/ratings/${id}/cancel`, { comment }),

    classifiers: () => client.get<Classifier[]>(`${BASE}/classifiers`),
    createClassifier: (body: NewClassifier) => client.post<Classifier>(`${BASE}/classifiers`, body),
    updateClassifier: (id: string, body: { name: string; description: string | null }) =>
      client.put<Classifier>(`${BASE}/classifiers/${id}`, body),
    addValue: (id: string, name: string) =>
      client.post<Classifier>(`${BASE}/classifiers/${id}/values`, { name }),
    renameValue: (id: string, valueId: string, name: string) =>
      client.put<Classifier>(`${BASE}/classifiers/${id}/values/${valueId}`, { name }),
    classifications: (subject: Subject) =>
      client.get<Classifications>(`${BASE}/classifications?${query(subject)}`),
    classify: (subject: Subject, body: NewClassification) =>
      client.post<Classifications>(`${BASE}/classifications`, { ...subject, ...body }),
    cancelClassification: (id: string, comment: string) =>
      client.post<Classifications>(`${BASE}/classifications/${id}/cancel`, { comment }),
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
