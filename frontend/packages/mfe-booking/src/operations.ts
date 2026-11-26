import type { Decimal, ExpenseKind, FeeKind, Operation, OperationKind } from './api';
import { shiftDay, sumMoney } from './format';

/**
 * Операции фонда (решение 175): группы видов для фильтра и ввода, расчёт по паям,
 * итоги периода и подсказки до проведения.
 */

export type OpGroup = 'units' | 'expenses' | 'fees' | 'transfers' | 'repo';
/** Взнос по РЕПО вводят из карточки сделки — в форме его нет. */
export type FormKind = Exclude<OperationKind, 'repo_contribution'>;
export type FormGroup = Exclude<OpGroup, 'repo'>;

export const GROUP_OF: Readonly<Record<OperationKind, OpGroup>> = {
  unit_issue: 'units',
  unit_redemption: 'units',
  expense_accrual: 'expenses',
  expense_payment: 'expenses',
  fee_payment: 'fees',
  cash_transfer: 'transfers',
  security_transfer: 'transfers',
  repo_contribution: 'repo',
};

export const GROUP_LABELS: Readonly<Record<OpGroup, string>> = {
  units: 'Паи',
  expenses: 'Расходы',
  fees: 'Вознаграждение',
  transfers: 'Переводы',
  repo: 'Взносы РЕПО',
};

export const GROUP_KINDS: Readonly<Record<FormGroup, readonly FormKind[]>> = {
  units: ['unit_issue', 'unit_redemption'],
  expenses: ['expense_accrual', 'expense_payment'],
  fees: ['fee_payment'],
  transfers: ['cash_transfer', 'security_transfer'],
};

export const GROUP_HINTS: Readonly<Record<FormGroup, string>> = {
  units: 'выдача, погашение',
  expenses: 'начисление, выплата',
  fees: 'выплата за период',
  transfers: 'деньги, бумаги',
};

/** Вид внутри группы — на переключателе. */
export const KIND_SHORT: Readonly<Record<FormKind, string>> = {
  unit_issue: 'Выдача',
  unit_redemption: 'Погашение',
  expense_accrual: 'Начисление',
  expense_payment: 'Выплата',
  fee_payment: 'Выплата',
  cash_transfer: 'Деньги',
  security_transfer: 'Бумаги',
};

/** Кнопка проведения называет, что произойдёт. */
export const SUBMIT_LABELS: Readonly<Record<FormKind, string>> = {
  unit_issue: 'Провести выдачу',
  unit_redemption: 'Провести погашение',
  expense_accrual: 'Начислить расход',
  expense_payment: 'Провести выплату',
  fee_payment: 'Провести выплату',
  cash_transfer: 'Провести перевод',
  security_transfer: 'Провести перевод',
};

/** Поля версии — в карточке «что изменилось». */
export const OPERATION_FIELDS: Readonly<Record<string, string>> = {
  status: 'статус',
  op_date: 'дата',
  portfolio_id: 'портфель',
  receiver_id: 'получатель',
  units: 'паи',
  amount: 'сумма',
  planned_date: 'план расчёта',
  settled_date: 'расчёт',
  expense_kind: 'вид расхода',
  fee_kind: 'вид комиссии',
  period_end: 'оплачено по',
  instrument_id: 'бумага',
  quantity: 'количество',
};

export type Settlement = 'none' | 'waiting' | 'overdue' | 'settled';

/** Расчёт по паям: отмечен, ждёт или просрочен — план раньше сегодня. */
export function settlementOf(operation: Operation, today: string): Settlement {
  if (GROUP_OF[operation.kind] !== 'units') {
    return 'none';
  }
  if (operation.settled_date !== null) {
    return 'settled';
  }
  return operation.planned_date !== null && operation.planned_date < today ? 'overdue' : 'waiting';
}

export function isPending(operation: Operation, today: string): boolean {
  const state = settlementOf(operation, today);
  return operation.status === 'active' && (state === 'waiting' || state === 'overdue');
}

export type OpFilter = 'all' | OpGroup | 'pending';

export function matches(operation: Operation, filter: OpFilter, today: string): boolean {
  if (filter === 'all') {
    return true;
  }
  return filter === 'pending' ? isPending(operation, today) : GROUP_OF[operation.kind] === filter;
}

export type OpPeriod = 'month' | 'quarter' | 'year' | 'custom';

export const OP_PERIOD_LABELS: Readonly<Record<OpPeriod, string>> = {
  month: 'Месяц',
  quarter: 'Квартал',
  year: 'Год',
  custom: 'Период',
};

/** Быстрые периоды — последние 30, 91 и 365 дней со сегодня, как в «Сделках». */
export function opRanges(today: string): Readonly<Record<Exclude<OpPeriod, 'custom'>, readonly [string, string]>> {
  return {
    month: [shiftDay(today, -29), today],
    quarter: [shiftDay(today, -90), today],
    year: [shiftDay(today, -364), today],
  };
}

export interface OpTotals {
  readonly issued: Decimal;
  readonly redeemed: Decimal;
  /** Выплаты расходов и вознаграждения. */
  readonly paid: Decimal;
  readonly pending: number;
  readonly pendingAmount: Decimal;
}

/** Итоги периода без отменённых. */
export function opTotals(operations: readonly Operation[], today: string): OpTotals {
  const live = operations.filter((o) => o.status === 'active');
  const sum = (kinds: readonly OperationKind[]) =>
    sumMoney(live.filter((o) => kinds.includes(o.kind)).map((o) => o.amount ?? '0'));
  const pending = live.filter((o) => isPending(o, today));
  return {
    issued: sum(['unit_issue']),
    redeemed: sum(['unit_redemption']),
    paid: sum(['expense_payment', 'fee_payment']),
    pending: pending.length,
    pendingAmount: sumMoney(pending.map((o) => o.amount ?? '0')),
  };
}

/** Начислено и не выплачено по виду расхода фонда на дату — по операциям. */
export function expenseOwed(
  operations: readonly Operation[],
  fundId: string,
  kind: ExpenseKind,
  day: string,
  except: string | null = null,
): number {
  return operations
    .filter(
      (o) =>
        o.status === 'active' &&
        o.id !== except &&
        o.fund_id === fundId &&
        o.expense_kind === kind &&
        o.op_date <= day,
    )
    .reduce((total, o) => total + (o.kind === 'expense_accrual' ? 1 : -1) * Number(o.amount), 0);
}

/** Сколько вознаграждения закроет выплата: начислено по месяц конца периода включительно. */
export function feesDueThrough(
  due: Readonly<Partial<Record<FeeKind, Readonly<Record<string, Decimal>>>>>,
  kind: FeeKind,
  periodEnd: string,
): number {
  const last = periodEnd.slice(0, 7);
  return Object.entries(due[kind] ?? {})
    .filter(([month]) => month <= last)
    .reduce((total, [, amount]) => total + Number(amount), 0);
}
