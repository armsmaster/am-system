import type { ApiClient } from '@am/platform';

/** Десятичное число строкой, как его отдаёт сервис: так не теряется точность. */
export type Decimal = string;
export type Side = 'buy' | 'sell';
export type Status = 'entered' | 'confirmed' | 'cancelled';

export interface Portfolio {
  readonly id: string;
  readonly code: string;
  readonly name: string;
  readonly kind: 'compound' | 'physical';
  readonly is_fund: boolean;
  readonly parent_ids: readonly string[];
  readonly fund_id: string | null;
}

export interface Instrument {
  readonly id: string;
  readonly secid: string;
  readonly kind: 'bond' | 'fund_share';
  readonly short_name: string;
}

/** Подсказка для формы: дата расчётов T+1, номинал и НКД на неё (решение 86). */
export interface Defaults {
  readonly instrument_id: string;
  readonly kind: 'bond' | 'fund_share';
  readonly settle_date: string;
  readonly face_value: Decimal | null;
  readonly accrued: Decimal | null;
}

export interface TradeVersion {
  readonly number: number;
  readonly status: Status;
  readonly author: string;
  readonly created_at: string;
  readonly comment: string | null;
  /** Какие поля изменились относительно предыдущей версии. */
  readonly changed: readonly string[];
}

export interface Trade {
  readonly id: string;
  readonly number: number;
  readonly status: Status;
  readonly portfolio_id: string;
  readonly portfolio_code: string | null;
  readonly instrument_id: string;
  readonly instrument_kind: 'bond' | 'fund_share';
  readonly secid: string;
  readonly short_name: string;
  readonly side: Side;
  readonly quantity: number;
  readonly price: Decimal;
  readonly face_value: Decimal | null;
  readonly accrued: Decimal | null;
  readonly amount: Decimal;
  readonly fee: Decimal;
  readonly trade_at: string;
  readonly trade_date: string;
  readonly settle_date: string;
  readonly venue: 'exchange' | 'otc';
  readonly counterparty: string | null;
  readonly external_id: string | null;
  readonly comment: string | null;
  readonly author: string;
  readonly created_at: string;
  readonly versions: readonly TradeVersion[];
  readonly warnings: readonly string[];
}

export interface TradeInput {
  readonly portfolio_id: string;
  readonly instrument_id: string;
  readonly side: Side;
  readonly quantity: number;
  readonly price: string;
  readonly trade_at: string;
  readonly settle_date: string;
  readonly face_value: string | null;
  readonly accrued: string | null;
  readonly fee: string;
  readonly venue: 'exchange' | 'otc';
  readonly counterparty: string | null;
  readonly external_id: string | null;
  readonly comment: string | null;
}

export interface Lot {
  readonly portfolio_code: string;
  readonly trade_id: string;
  readonly opened_at: string;
  readonly quantity: number;
  readonly price: Decimal;
  readonly face: Decimal | null;
  readonly cost: Decimal;
  readonly accrued_paid: Decimal;
}

export interface Line {
  readonly instrument_id: string;
  readonly kind: 'bond' | 'fund_share';
  readonly secid: string;
  readonly short_name: string;
  readonly trade_quantity: number;
  readonly settle_quantity: number;
  readonly cost: Decimal;
  readonly accrued_paid: Decimal;
  readonly average_price: Decimal | null;
  readonly lots: readonly Lot[];
}

export type MovementKind =
  | 'settlement'
  | 'fee'
  | 'coupon'
  | 'amortization'
  | 'redemption'
  | 'payment_unknown';

export interface Movement {
  readonly kind: MovementKind;
  readonly instrument_id: string;
  readonly trade_id: string | null;
  readonly quantity: number;
  readonly cash: Decimal;
}

export interface PortfolioState {
  readonly id: string;
  readonly code: string;
  readonly name: string;
  /** День расчёта; null — расчёта ещё нет. */
  readonly as_of: string | null;
  /** Стоит в очереди пересчёта с этой даты. */
  readonly waiting_since: string | null;
  readonly cash: Decimal;
  readonly movements: readonly Movement[];
}

export interface Positions {
  readonly portfolio_id: string;
  readonly code: string;
  readonly name: string;
  readonly date: string;
  readonly cash: Decimal;
  readonly portfolios: readonly PortfolioState[];
  readonly lines: readonly Line[];
}

const BASE = '/api/booking';

/** Вызовы API сделок; портфели — из сервиса портфелей, бумаги — из справочника. */
export function bookingApi(client: ApiClient) {
  return {
    portfolios: () => client.get<Portfolio[]>('/api/portfolio/portfolios'),
    instruments: () => client.get<Instrument[]>('/api/refdata/instruments'),
    defaults: (instrumentId: string, tradeDate: string) =>
      client.get<Defaults>(
        `${BASE}/trade-defaults${query({ instrument_id: instrumentId, trade_date: tradeDate })}`,
      ),
    trades: (portfolioId: string, date: string) =>
      client.get<Trade[]>(`${BASE}/trades${query({ portfolio_id: portfolioId, date })}`),
    trade: (id: string) => client.get<Trade>(`${BASE}/trades/${id}`),
    enter: (body: TradeInput) => client.post<Trade>(`${BASE}/trades`, body),
    amend: (id: string, body: TradeInput) => client.post<Trade>(`${BASE}/trades/${id}/versions`, body),
    cancel: (id: string, comment: string) =>
      client.post<Trade>(`${BASE}/trades/${id}/cancel`, { comment }),
    confirm: (id: string, comment: string) =>
      client.post<Trade>(`${BASE}/trades/${id}/confirm`, { comment: comment || null }),
    positions: (portfolioId: string, date: string) =>
      client.get<Positions>(`${BASE}/positions${query({ portfolio_id: portfolioId, date })}`),
  };
}

export type BookingApi = ReturnType<typeof bookingApi>;

function query(params: Record<string, string>): string {
  return `?${new URLSearchParams(params).toString()}`;
}
