import type { Decimal, PeriodResult, ResultSummary, SummaryKey, SummaryLine } from './api';
import { formatDate, sumMoney } from './format';

/**
 * Отчёт о финансовом результате (решение 167): статьи строками, периоды колонками.
 * Здесь — только правила; что и как нарисовать, решает страница.
 */

export type FixedKey = Exclude<SummaryKey, 'custom'>;

export const PERIOD_KEYS: readonly FixedKey[] = ['day', 'week', 'days30', 'quarter', 'year', 'all'];

export const PERIOD_LABELS: Readonly<Record<FixedKey, string>> = {
  day: 'День',
  week: 'Неделя',
  days30: '30 дней',
  quarter: 'С нач. квартала',
  year: 'С нач. года',
  all: 'Всё время',
};

export const DEFAULT_COLUMNS: readonly FixedKey[] = ['day', 'week', 'days30', 'quarter', 'year'];

export interface CustomPeriod {
  readonly from: string;
  readonly till: string;
}

export const COLUMNS_KEY = 'am.booking.results.columns';
export const CUSTOM_KEY = 'am.booking.results.custom';
export const PERCENT_KEY = 'am.booking.results.percent';
export const ANNUAL_KEY = 'am.booking.results.annual';

/** Колонки из хранилища; испорченная запись — по умолчанию. */
export function parseColumns(raw: string | null): readonly FixedKey[] {
  try {
    const found: unknown = JSON.parse(raw ?? 'null');
    return Array.isArray(found) ? PERIOD_KEYS.filter((key) => found.includes(key)) : DEFAULT_COLUMNS;
  } catch {
    return DEFAULT_COLUMNS;
  }
}

export function parseCustom(raw: string | null): CustomPeriod | null {
  try {
    const found = JSON.parse(raw ?? 'null') as Partial<CustomPeriod> | null;
    const valid = (value: unknown) => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value);
    return found && valid(found.from) && valid(found.till) ? { from: found.from!, till: found.till! } : null;
  } catch {
    return null;
  }
}

export function parseFlag(raw: string | null): boolean {
  return raw === 'true';
}

const MONTHS = [
  'Январь',
  'Февраль',
  'Март',
  'Апрель',
  'Май',
  'Июнь',
  'Июль',
  'Август',
  'Сентябрь',
  'Октябрь',
  'Ноябрь',
  'Декабрь',
];

function lastOfMonth(iso: string): string {
  const day = new Date(`${iso.slice(0, 8)}01T12:00:00Z`);
  day.setUTCMonth(day.getUTCMonth() + 1, 0);
  return day.toISOString().slice(0, 10);
}

/** Прошлый календарный месяц — свой период по умолчанию. */
export function previousMonth(today: string): CustomPeriod {
  const first = new Date(`${today.slice(0, 8)}01T12:00:00Z`);
  first.setUTCMonth(first.getUTCMonth() - 1);
  const from = first.toISOString().slice(0, 10);
  return { from, till: lastOfMonth(from) };
}

/** Заголовок колонки: «Неделя», свой период ровно на месяц — «Октябрь». */
export function columnTitle(period: PeriodResult, today: string): string {
  if (period.key !== 'custom') {
    return PERIOD_LABELS[period.key];
  }
  const month = period.first.endsWith('-01') && period.last === lastOfMonth(period.first);
  if (!month) {
    return 'Свой период';
  }
  const year = period.first.slice(0, 4) === today.slice(0, 4) ? '' : ` ${period.first.slice(0, 4)}`;
  return `${MONTHS[Number(period.first.slice(5, 7)) - 1]}${year}`;
}

/** Даты под заголовком: «25.11», «19.11–25.11», «с 01.10», «с 26.11.2026». */
export function columnDates(period: PeriodResult): string {
  const short = (iso: string) => formatDate(iso).slice(0, 5);
  switch (period.key) {
    case 'day':
      return short(period.last);
    case 'quarter':
    case 'year':
      return `с ${short(period.first)}`;
    case 'all':
      return `с ${formatDate(period.first)}`;
    default:
      return `${short(period.first)}–${short(period.last)}`;
  }
}

/** Колонки отчёта в порядке периодов; свой период — последним. */
export function shownPeriods(summary: ResultSummary, columns: readonly FixedKey[]): PeriodResult[] {
  return summary.periods.filter((p) => p.key === 'custom' || columns.includes(p.key));
}

// --- статьи --------------------------------------------------------------------------

export type Part = 'revaluation' | 'realized' | 'coupon' | 'fees' | 'repo' | 'expenses' | 'charges' | 'correction';

export const SECURITY_PARTS: readonly (readonly [Part, string])[] = [
  ['revaluation', 'Переоценка'],
  ['realized', 'Реализованный результат'],
  ['coupon', 'Купонный доход'],
  ['fees', 'Комиссии по сделкам'],
];

export const EXPENSE_PARTS: readonly (readonly [Part, string])[] = [
  ['expenses', 'Расходы фонда'],
  ['charges', 'Вознаграждение и комиссии от СЧА'],
  ['correction', 'Корректировка комиссий при выплате'],
];

export function securitiesTotal(period: PeriodResult): Decimal {
  return sumMoney(SECURITY_PARTS.map(([part]) => period[part]));
}

export function expensesTotal(period: PeriodResult): Decimal {
  return sumMoney(EXPENSE_PARTS.map(([part]) => period[part]));
}

/** Строка нужна, если хоть в одной показанной колонке не ноль. */
export function anyValue(periods: readonly PeriodResult[], pick: (period: PeriodResult) => Decimal): boolean {
  return periods.some((p) => Number(pick(p)) !== 0);
}

// --- бумаги --------------------------------------------------------------------------

export interface LineGroups {
  readonly held: readonly SummaryLine[];
  readonly closed: readonly SummaryLine[];
}

/**
 * Бумаги для раскрытия: в позиции и проданные или погашенные — по убыванию результата
 * за самый длинный показанный период; бумаги без результата в показанных колонках скрыты.
 */
export function lineGroups(lines: readonly SummaryLine[], periods: readonly PeriodResult[]): LineGroups {
  const longest = [...periods].sort((a, b) => b.days - a.days)[0];
  const value = (line: SummaryLine, key: SummaryKey) => Number(line.totals[key] ?? 0);
  const shown = lines
    .filter((line) => periods.some((p) => value(line, p.key) !== 0))
    .sort(
      (a, b) =>
        Math.abs(value(b, longest!.key)) - Math.abs(value(a, longest!.key)) || a.short_name.localeCompare(b.short_name, 'ru'),
    );
  return { held: shown.filter((line) => line.held), closed: shown.filter((line) => !line.held) };
}
