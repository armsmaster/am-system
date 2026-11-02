import type { AccountDay, AccountStatus, Difference, LineStatus, MovementKind, Side } from './api';

const NO_VALUE = '—';

export const ACCOUNT_STATUS_LABELS: Readonly<Record<AccountStatus, string>> = {
  reconciled: 'сверено',
  discrepancies: 'есть расхождения',
  no_statement: 'нет выписки за день',
  non_trading: 'неторговый день — сверять нечего',
  recalculating: 'фонд пересчитывается — сверьте после пересчёта',
  not_calculated: 'расчёта за день ещё нет',
};

/** Статус строки; «нет в выписке» — словами того, кто выписку дал: брокер или банк. */
export function lineStatus(status: LineStatus, cashKind: AccountDay['cash_kind']): string {
  const labels: Readonly<Record<LineStatus, string>> = {
    matched: 'сходится',
    mismatched: 'расходится',
    not_in_system: 'нет в системе',
    not_in_statement: cashKind === 'broker' ? 'нет у брокера' : 'нет в банке',
    accepted: 'принято',
    paired: 'сошлось с принятой строкой',
    explained: 'объясняют принятые расхождения',
  };
  return labels[status];
}

export const RESOLVED: ReadonlySet<LineStatus> = new Set(['matched', 'accepted', 'paired', 'explained']);

export const MOVEMENT_LABELS: Readonly<Record<MovementKind, string>> = {
  settlement: 'расчёты по сделке',
  fee: 'комиссия по сделке',
  repo_first: 'первая часть РЕПО',
  repo_second: 'вторая часть РЕПО',
  repo_contribution: 'компенсационный взнос',
  coupon: 'купон',
  amortization: 'амортизация',
  redemption: 'погашение',
  transfer: 'перевод между счетами фонда',
  unit_settlement: 'расчёт по паям',
  fee_payment: 'вознаграждение и комиссии от СЧА',
  expense_payment: 'выплата расхода',
  other: 'прочее',
};

/** Какой ключ у движения (решение 120): номер сделки, бумага или ничего. */
export const BY_TRADE: ReadonlySet<MovementKind> = new Set([
  'settlement',
  'fee',
  'repo_first',
  'repo_second',
  'repo_contribution',
]);
export const BY_SECURITY: ReadonlySet<MovementKind> = new Set(['coupon', 'amortization', 'redemption']);

/**
 * «расчёты по сделке 1000000001», «купон SU26238RMFS4 по сделке РЕПО 2000000001»:
 * вид, бумага и номер сделки; сделка без номера в системе — «без номера».
 */
export function movementTitle(kind: MovementKind, security: string | null, trade: string | null): string {
  const number = trade?.startsWith('id:') ? 'без номера' : trade;
  const parts = [MOVEMENT_LABELS[kind], security];
  if (number) {
    parts.push(BY_TRADE.has(kind) ? number : `по сделке РЕПО ${number}`);
  }
  return parts.filter(Boolean).join(' ');
}

export const SIDE_LABELS: Readonly<Record<Side, string>> = { buy: 'покупка', sell: 'продажа' };

const FIELD_LABELS: Readonly<Record<string, string>> = {
  external_id: 'номер сделки',
  kind: 'вид сделки',
  side: 'направление',
  security: 'бумага',
  quantity: 'количество',
  price: 'цена',
  accrued: 'НКД на бумагу',
  amount: 'сумма',
  fee: 'комиссия',
  settle_date: 'дата расчётов',
  repo_rate: 'ставка РЕПО',
  second_date: 'дата второй части',
  second_amount: 'сумма второй части',
};

const MONEY_FIELDS = new Set(['accrued', 'amount', 'fee', 'second_amount']);
const DATE_FIELDS = new Set(['settle_date', 'second_date']);
const VALUE_LABELS: Readonly<Record<string, string>> = {
  ...SIDE_LABELS,
  outright: 'покупка или продажа',
  repo: 'РЕПО',
};

/** «комиссия: 0,00 в системе, 2 800,05 в выписке». */
export function describeDifference(difference: Difference): string {
  const show = (value: string | null) => {
    if (value === null) {
      return NO_VALUE;
    }
    if (MONEY_FIELDS.has(difference.field)) {
      return formatMoney(value);
    }
    if (DATE_FIELDS.has(difference.field)) {
      return formatDate(value);
    }
    if (difference.field === 'quantity') {
      return group(value);
    }
    return VALUE_LABELS[value] ?? value.replace('.', ',');
  };
  const name = FIELD_LABELS[difference.field] ?? difference.field;
  return `${name}: ${show(difference.system)} в системе, ${show(difference.statement)} в выписке`;
}

function group(whole: string): string {
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

/** 28000500.00 → 28 000 500,00; −885000 → −885 000,00. */
export function formatMoney(value: string | null): string {
  if (value === null) {
    return NO_VALUE;
  }
  const negative = value.startsWith('-');
  const [whole = '', fraction = ''] = value.replace('-', '').split('.');
  return `${negative ? '−' : ''}${group(whole)},${fraction.padEnd(2, '0').slice(0, 2)}`;
}

/** 50000 → 50 000. */
export function formatQuantity(value: number): string {
  return group(String(value));
}

/** 2026-11-30 → 30.11.2026. */
export function formatDate(iso: string): string {
  return iso.split('-').reverse().join('.');
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

/** Дата по Москве для поля date: 2026-12-03. */
export function moscowToday(date: Date = new Date()): string {
  return date.toLocaleString('sv-SE', { timeZone: 'Europe/Moscow' }).slice(0, 10);
}

/** Сверяют вчерашний день: выписка за D приходит утром D+1. */
export function previousDay(iso: string): string {
  const day = new Date(`${iso}T00:00:00Z`);
  day.setUTCDate(day.getUTCDate() - 1);
  return day.toISOString().slice(0, 10);
}

/** «99,5» → «99.5»: число, как его вводят по-русски, → для API. */
export function decimalInput(value: string): string {
  return value.trim().replace(/\s/g, '').replace(',', '.').replace('−', '-');
}
