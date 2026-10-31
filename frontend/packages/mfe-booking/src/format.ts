import type {
  Decimal,
  ExpenseKind,
  FeeKind,
  MovementKind,
  OperationKind,
  Side,
  Status,
  ValuationFlag,
} from './api';

export const SIDE_LABELS: Readonly<Record<Side, string>> = { buy: 'покупка', sell: 'продажа' };

export const STATUS_LABELS: Readonly<Record<Status, string>> = {
  entered: 'введена',
  confirmed: 'подтверждена',
  cancelled: 'отменена',
};

export const VENUE_LABELS = { exchange: 'биржа', otc: 'внебиржевая' } as const;

export const MOVEMENT_LABELS: Readonly<Record<MovementKind, string>> = {
  settlement: 'расчёты по сделке',
  fee: 'комиссия',
  coupon: 'купон',
  amortization: 'амортизация',
  redemption: 'погашение',
  payment_unknown: 'выплата: сумма не объявлена',
  unit_issue: 'выдача паёв',
  unit_redemption: 'погашение паёв',
  unit_settlement: 'расчёт по паям',
  expense_accrual: 'начисление расхода',
  expense_payment: 'выплата расхода',
  transfer_out: 'перевод: ушло',
  transfer_in: 'перевод: пришло',
  fee_payment: 'выплата вознаграждения',
};

export const OPERATION_LABELS: Readonly<Record<OperationKind, string>> = {
  unit_issue: 'выдача паёв',
  unit_redemption: 'погашение паёв',
  expense_accrual: 'начисление расхода',
  expense_payment: 'выплата расхода',
  cash_transfer: 'перевод денег',
  security_transfer: 'перевод бумаг',
  fee_payment: 'выплата вознаграждения',
};

/** Комиссии от СЧА (решение 21). */
export const FEE_LABELS: Readonly<Record<FeeKind, string>> = {
  management: 'вознаграждение УК',
  specdep: 'спецдепозитарий',
  registrar: 'регистратор',
  other: 'прочие',
};

export const EXPENSE_LABELS: Readonly<Record<ExpenseKind, string>> = {
  specdep: 'специализированный депозитарий',
  registrar: 'регистратор',
  auditor: 'аудитор',
  exchange_broker: 'биржа и брокер',
  other: 'прочие',
};

/** Поля сделки, как их называет карточка версий. */
export const FIELD_LABELS: Readonly<Record<string, string>> = {
  portfolio_id: 'портфель',
  instrument_id: 'бумага',
  side: 'направление',
  quantity: 'количество',
  price: 'цена',
  face_value: 'номинал',
  accrued: 'НКД',
  fee: 'комиссия',
  trade_at: 'время сделки',
  settle_date: 'дата расчётов',
  venue: 'место',
  counterparty: 'контрагент',
  external_id: 'номер сделки',
};

/** Почему оценка неполная — словами (design/06). */
export const FLAG_LABELS: Readonly<Record<ValuationFlag, string>> = {
  no_price: 'цены не было — по цене покупки',
  stale_price: 'цена не за этот день',
  no_accrued: 'нет НКД на дату расчётов — без НКД',
  no_face: 'нет номинала на день — номинал лотов',
  coupon_unknown: 'сумма купона к получению не объявлена',
};

const NO_VALUE = '—';

/** Сумма денежных значений до копейки — без ошибок дробной арифметики. */
export function sumMoney(values: readonly Decimal[]): Decimal {
  const kopecks = values.reduce((total, value) => {
    const negative = value.startsWith('-');
    const [whole = '0', fraction = ''] = value.replace('-', '').split('.');
    const amount = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0').slice(0, 2));
    return total + (negative ? -amount : amount);
  }, 0n);
  const sign = kopecks < 0n ? '-' : '';
  const absolute = kopecks < 0n ? -kopecks : kopecks;
  return `${sign}${absolute / 100n}.${String(absolute % 100n).padStart(2, '0')}`;
}

function group(whole: string): string {
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

/** 28000500.00 → 28 000 500,00; −16776300 → −16 776 300,00. */
export function formatMoney(value: Decimal | null): string {
  if (value === null) {
    return NO_VALUE;
  }
  const negative = value.startsWith('-');
  const [whole = '', fraction = ''] = value.replace('-', '').split('.');
  return `${negative ? '−' : ''}${group(whole)},${fraction.padEnd(2, '0').slice(0, 2)}`;
}

/** 52.500000 → 52,5: цена без лишних нулей. */
export function formatPrice(value: Decimal | null): string {
  if (value === null) {
    return NO_VALUE;
  }
  const negative = value.startsWith('-');
  const [whole = '', fraction = ''] = value.replace('-', '').split('.');
  const digits = fraction.replace(/0+$/, '');
  return `${negative ? '−' : ''}${group(whole)}${digits ? `,${digits}` : ''}`;
}

/** 50000 → 50 000; −10000 → −10 000. */
export function formatQuantity(value: number): string {
  return `${value < 0 ? '−' : ''}${group(String(Math.abs(value)))}`;
}

/** 2026-11-30 → 30.11.2026. */
export function formatDate(iso: string | null): string {
  return iso === null ? NO_VALUE : iso.split('-').reverse().join('.');
}

/** Время по Москве: в нём работают биржа и пользователи. */
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString('ru-RU', {
    timeZone: 'Europe/Moscow',
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** Значение для поля datetime-local по Москве: 2026-11-27T11:42. */
export function moscowInput(date: Date): string {
  return date.toLocaleString('sv-SE', { timeZone: 'Europe/Moscow' }).slice(0, 16).replace(' ', 'T');
}

/** Поле datetime-local (время по Москве) → момент со смещением для API. */
export function fromMoscowInput(value: string): string {
  return `${value}:00+03:00`;
}

/** Дата по Москве для поля date: 2026-12-03. */
export function moscowToday(date: Date = new Date()): string {
  return moscowInput(date).slice(0, 10);
}

/** «99,5» → «99.5»: число, как его вводят по-русски, → для API. */
export function decimalInput(value: string): string {
  return value.trim().replace(/\s/g, '').replace(',', '.');
}
