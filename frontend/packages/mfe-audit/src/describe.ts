import type { AuditEvent } from './api';

/**
 * Перевод событий журнала на язык пользователя: что сделали, с чем
 * и что именно изменилось.
 */

const ACTIONS: Readonly<Record<string, string>> = {
  'portfolio.created': 'Создан портфель',
  'portfolio.updated': 'Изменён портфель',
  'portfolio.linked': 'Портфель включён в compound',
  'portfolio.unlinked': 'Портфель исключён из compound',
  'portfolio.account_changed': 'Сменён счёт портфеля',
  'fund.treasury_changed': 'Сменён казначейский портфель',
  'account.created': 'Создан счёт',
  'account.updated': 'Изменён счёт',
  'access.granted': 'Выдан доступ',
  'access.revoked': 'Отозван доступ',
};

const FIELDS: Readonly<Record<string, string>> = {
  code: 'Код',
  name: 'Название',
  kind: 'Тип',
  is_fund: 'Это фонд',
  account_code: 'Счёт',
  treasury_code: 'Казначейский портфель',
  fund_code: 'Фонд',
  cash_kind: 'Денежный счёт',
  institution: 'Брокер или банк',
  cash_account: 'Номер денежного счёта',
  depo_account: 'Счёт депо',
  stock_market: 'Фондовый рынок',
  derivatives_market: 'Срочный рынок',
};

/** Значения перечислений — словами. */
const VALUES: Readonly<Record<string, string>> = {
  broker: 'брокерский',
  bank: 'банковский',
};

export interface Change {
  readonly field: string;
  readonly before: string;
  readonly after: string;
}

type State = Readonly<Record<string, unknown>>;
interface Ref {
  readonly code?: string;
  readonly username?: string;
  readonly subject?: string;
}

export function actionLabel(type: string): string {
  return ACTIONS[type] ?? type;
}

/** С чем совершено действие: код портфеля или счёта, пользователь и портфель. */
export function objectLabel(event: AuditEvent): string {
  const data = event.data as {
    before?: State;
    after?: State;
    parent?: Ref;
    child?: Ref;
    user?: Ref;
    portfolio?: Ref;
  };
  if (data.child && data.parent) {
    return `${data.child.code} ↔ ${data.parent.code}`;
  }
  if (data.user && data.portfolio) {
    return `${data.user.username ?? data.user.subject}: ${data.portfolio.code}`;
  }
  const state = data.after ?? data.before;
  return typeof state?.code === 'string' ? state.code : event.entity_id;
}

/** Поля «было → стало». Для созданного объекта «было» — прочерк. */
export function changes(event: AuditEvent): Change[] {
  const before = event.data.before as State | undefined;
  const after = event.data.after as State | undefined;
  if (!after) {
    return [];
  }
  // Идентификаторы нужны машинам; человеку журнал показывает коды (поля *_code).
  return Object.keys(after)
    .filter((key) => key !== 'id' && !key.endsWith('_id'))
    .filter((key) => !before || JSON.stringify(before[key]) !== JSON.stringify(after[key]))
    .map((key) => ({
      field: FIELDS[key] ?? key,
      before: before ? show(before[key]) : '—',
      after: show(after[key]),
    }));
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return '—';
  }
  if (typeof value === 'boolean') {
    return value ? 'да' : 'нет';
  }
  return VALUES[String(value)] ?? String(value);
}

/** Время по Москве: в нём работают биржа и пользователи. */
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString('ru-RU', {
    timeZone: 'Europe/Moscow',
    dateStyle: 'short',
    timeStyle: 'medium',
  });
}
