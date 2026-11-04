import type { ApiClient } from '@am/platform';

/** Десятичное число строкой, как его отдаёт сервис: так не теряется точность. */
export type Decimal = string;
/** Дата в формате ГГГГ-ММ-ДД. */
export type IsoDate = string;

export interface Fund {
  readonly id: string;
  readonly code: string;
  readonly name: string;
}

// --- набор лимитов (design/09, решение 130) -----------------------------------------

/** Признак бумаги: вид, тип ISS, тип купона, валюта, бумага, эмитент, группа, class:<id>. */
export interface Condition {
  readonly attribute: string;
  readonly values: readonly string[];
  readonly negate?: boolean;
}

export interface MetricCheck {
  readonly type: 'metric';
  readonly label?: string | null;
  readonly metric: 'leverage' | 'duration';
  readonly max?: Decimal | null;
  readonly min?: Decimal | null;
}

export interface ShareCheck {
  readonly type: 'share';
  readonly label?: string | null;
  readonly filter?: readonly Condition[];
  readonly group?: 'all' | 'issuer' | 'instrument';
  readonly exclude_issuers?: readonly string[];
  readonly max: Decimal;
}

export interface Rule {
  readonly name: string;
  readonly filter?: readonly Condition[];
  readonly shorts?: boolean;
}

export interface EligibleCheck {
  readonly type: 'eligible';
  readonly label?: string | null;
  readonly rules: readonly Rule[];
}

export interface PurchaseCheck {
  readonly type: 'purchase';
  readonly label?: string | null;
  readonly filter?: readonly Condition[];
  readonly condition: readonly Condition[];
}

export type Check = MetricCheck | ShareCheck | EligibleCheck | PurchaseCheck;

export interface Limit {
  readonly code: string;
  readonly name: string;
  readonly clause?: string | null;
  readonly checks: readonly Check[];
}

export interface RatingRule {
  readonly filter?: readonly Condition[];
  readonly source: 'instrument' | 'issuer' | 'instrument_or_issuer';
}

export interface SetBody {
  readonly document: { readonly name: string; readonly version?: string | null; readonly approved_on?: IsoDate | null };
  readonly settings: {
    readonly ratings: {
      readonly agencies: readonly string[];
      readonly pick?: 'highest' | 'lowest';
      readonly groups: Readonly<Record<string, string>>;
      readonly rules: readonly RatingRule[];
    };
    readonly warn_at?: Decimal;
  };
  readonly limits: readonly Limit[];
}

export interface SetHeader {
  readonly id: string;
  readonly version: number;
  readonly valid_from: IsoDate;
  readonly comment: string;
  readonly author: string;
  readonly created_at: string;
}

export interface LimitSet extends SetHeader {
  readonly body: SetBody;
}

// --- использование за день ------------------------------------------------------------

export type Status = 'ok' | 'warning' | 'breach' | 'no_data';

export interface Group {
  readonly key: string;
  readonly name: string;
  /** % активов. */
  readonly value: Decimal;
  readonly status: Status;
}

export interface Problem {
  readonly secid: string;
  readonly reason: string;
}

export interface CheckUsage {
  readonly code: string;
  readonly name: string;
  readonly clause: string | null;
  readonly label: string | null;
  readonly type: Check['type'];
  /** У показателя фонда: плечо или дюрация к активам. */
  readonly metric: MetricCheck['metric'] | null;
  readonly status: Status;
  readonly value: Decimal | null;
  readonly maximum: Decimal | null;
  readonly minimum: Decimal | null;
  /** % лимита. */
  readonly usage: Decimal | null;
  readonly groups: readonly Group[];
  readonly violations: readonly Problem[];
  readonly missing: readonly Problem[];
}

export interface Totals {
  readonly assets: Decimal;
  readonly liabilities: Decimal;
  readonly nav: Decimal;
  readonly leverage: Decimal | null;
  readonly duration: Decimal | null;
}

export interface FundDay {
  readonly fund: Fund;
  readonly date: IsoDate;
  /** Сегодня — по текущим ценам. */
  readonly today: boolean;
  readonly limit_set: SetHeader | null;
  readonly calculated: boolean;
  readonly totals: Totals | null;
  readonly checks: readonly CheckUsage[];
}

// --- имена из справочника: чтобы набор читался словами ----------------------------------

export interface Classifier {
  readonly id: string;
  readonly name: string;
  readonly target: 'instrument' | 'issuer';
  readonly values: readonly { readonly id: string; readonly name: string }[];
}

export interface Issuer {
  readonly id: string;
  readonly name: string;
}

export interface Instrument {
  readonly id: string;
  readonly secid: string;
}

const BASE = '/api/limits';
const REFDATA = '/api/refdata';

/** Вызовы API сервиса лимитов и справочника. */
export function limitsApi(client: ApiClient) {
  return {
    funds: () => client.get<Fund[]>(`${BASE}/funds`),
    day: (fundId: string, day: IsoDate) => client.get<FundDay>(`${BASE}/funds/${fundId}/days/${day}`),
    sets: (fundId: string) => client.get<LimitSet[]>(`${BASE}/funds/${fundId}/sets`),
    saveSet: (fundId: string, body: { valid_from: IsoDate; comment: string; body: SetBody }) =>
      client.post<LimitSet>(`${BASE}/funds/${fundId}/sets`, body),

    classifiers: () => client.get<Classifier[]>(`${REFDATA}/classifiers`),
    issuers: () => client.get<Issuer[]>(`${REFDATA}/issuers`),
    instruments: () => client.get<Instrument[]>(`${REFDATA}/instruments`),
  };
}

export type LimitsApi = ReturnType<typeof limitsApi>;
