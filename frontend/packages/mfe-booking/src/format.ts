import type {
  CouponForecast,
  Decimal,
  ExpenseKind,
  FeeKind,
  Movement,
  MovementKind,
  OperationKind,
  Side,
  Status,
  Trade,
  ValuationFlag,
} from './api';

export const SIDE_LABELS: Readonly<Record<Side, string>> = { buy: 'покупка', sell: 'продажа' };

/** Сторона первой части РЕПО: продажа — прямое, покупка — обратное (design/07). */
export const REPO_LABELS: Readonly<Record<Side, string>> = {
  sell: 'прямое РЕПО',
  buy: 'обратное РЕПО',
};

/** Что за сделка — словами: «покупка» или «прямое РЕПО». */
export function tradeLabel(trade: Pick<Trade, 'kind' | 'side'>): string {
  return trade.kind === 'repo' ? REPO_LABELS[trade.side] : SIDE_LABELS[trade.side];
}

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
  repo_first: 'РЕПО: первая часть',
  repo_second: 'РЕПО: вторая часть',
  repo_contribution: 'РЕПО: компенсационный взнос',
  coupon_forecast: 'купон, прогноз',
};

const INDEX_LABELS: Readonly<Record<CouponForecast['index'], string>> = {
  key_rate: 'КС',
  ruonia: 'RUONIA',
  rusfar: 'RUSFAR',
};

/** Из чего прогноз купона: «КС 14% + 1,3%», с капитализацией — так и пишем. */
export function forecastBasis(forecast: CouponForecast): string {
  const margin = Number(forecast.margin_bp) / 100;
  const sign = margin < 0 ? '−' : '+';
  const percent = (value: number) => `${formatPrice(String(value))}%`;
  return [
    `${INDEX_LABELS[forecast.index]} ${percent(Number(forecast.index_value))} ${sign} ${percent(Math.abs(margin))}`,
    forecast.capitalized ? ', с капитализацией' : '',
    `, ${formatMoney(forecast.coupon)} ₽ на бумагу`,
  ].join('');
}

/** Движение словами; выплата со ссылкой на сделку — передача дохода по РЕПО (решение 112). */
export function movementLabel(movement: Pick<Movement, 'kind' | 'trade_id'>): string {
  const label = MOVEMENT_LABELS[movement.kind];
  const paid = movement.kind === 'coupon' || movement.kind === 'amortization' || movement.kind === 'redemption';
  return paid && movement.trade_id !== null ? `${label}: передача дохода по РЕПО` : label;
}

export const OPERATION_LABELS: Readonly<Record<OperationKind, string>> = {
  unit_issue: 'выдача паёв',
  unit_redemption: 'погашение паёв',
  expense_accrual: 'начисление расхода',
  expense_payment: 'выплата расхода',
  cash_transfer: 'перевод денег',
  security_transfer: 'перевод бумаг',
  fee_payment: 'выплата вознаграждения',
  repo_contribution: 'компенсационный взнос по РЕПО',
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
  kind: 'вид сделки',
  amount: 'сумма',
  repo_rate: 'ставка РЕПО',
  second_date: 'дата второй части',
  second_amount: 'сумма второй части',
};

/** Почему оценка неполная — словами (design/06). */
export const FLAG_LABELS: Readonly<Record<ValuationFlag, string>> = {
  no_price: 'цены не было — по цене покупки',
  stale_price: 'цена не за этот день',
  no_accrued: 'нет НКД на дату расчётов — без НКД',
  no_face: 'нет номинала на дату расчётов — номинал лотов',
  coupon_unknown: 'сумма купона к получению не объявлена',
  principal_unknown: 'сумма погашения к получению не объявлена',
  due_estimated: 'купон или погашение линкера к получению — оценка по номиналу ISS',
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

/**
 * Сумма второй части РЕПО по ставке и сроку — только для проверки введённой
 * по отчёту брокера (решение 110): первая × (1 + ставка × дни / 365), до копейки.
 */
export function secondAmount(first: string, rate: string, firstDate: string, secondDate: string): string | null {
  const days = (Date.parse(secondDate) - Date.parse(firstDate)) / 86_400_000;
  const amount = Number(decimalInput(first));
  const percent = Number(decimalInput(rate));
  if (!(days > 0) || !(amount > 0) || !first.trim() || !rate.trim() || Number.isNaN(percent)) {
    return null;
  }
  return (Math.round(amount * (1 + (percent / 100) * (days / 365)) * 100) / 100).toFixed(2);
}

/** «99,5» → «99.5»: число, как его вводят по-русски, → для API. */
export function decimalInput(value: string): string {
  return value.trim().replace(/\s/g, '').replace(',', '.');
}
