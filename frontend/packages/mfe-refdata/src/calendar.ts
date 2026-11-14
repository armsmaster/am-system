import type { CalendarDay, IsoDate } from './api';

/**
 * Торговый календарь года: по умолчанию торги с понедельника по пятницу,
 * исключения — праздники в будни и рабочие выходные. Здесь — расчёты для
 * сетки года и сводки; источник исключений — справочник.
 */

export const MONTHS = [
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
] as const;

const MONTHS_GENITIVE = [
  'января',
  'февраля',
  'марта',
  'апреля',
  'мая',
  'июня',
  'июля',
  'августа',
  'сентября',
  'октября',
  'ноября',
  'декабря',
] as const;

const WEEKDAYS_FULL = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'] as const;

/** Шапка месяца: неделя с понедельника. */
export const WEEK_HEADER = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'] as const;

function parts(day: IsoDate): [number, number, number] {
  const [year, month, date] = day.split('-').map(Number);
  return [year!, month! - 1, date!];
}

function iso(year: number, month: number, date: number): IsoDate {
  return `${year}-${String(month + 1).padStart(2, '0')}-${String(date).padStart(2, '0')}`;
}

function dayOfWeek(day: IsoDate): number {
  const [year, month, date] = parts(day);
  return new Date(Date.UTC(year, month, date)).getUTCDay();
}

export function isWeekend(day: IsoDate): boolean {
  const weekday = dayOfWeek(day);
  return weekday === 0 || weekday === 6;
}

/** Идут ли торги: исключение решает за выходной и будний день. */
export function isTrading(day: IsoDate, exception: CalendarDay | undefined): boolean {
  return exception ? exception.is_trading : !isWeekend(day);
}

/** «4 ноября 2026, среда». */
export function longDate(day: IsoDate): string {
  const [year, month, date] = parts(day);
  return `${date} ${MONTHS_GENITIVE[month]} ${year}, ${WEEKDAYS_FULL[dayOfWeek(day)]}`;
}

/** «4 ноября, среда» — для результата калькулятора и подписей дней. */
export function dayAndWeekday(day: IsoDate): string {
  const [, month, date] = parts(day);
  return `${date} ${MONTHS_GENITIVE[month]}, ${WEEKDAYS_FULL[dayOfWeek(day)]}`;
}

/** «4 ноя» — коротко, для списка исключений. */
export function shortDate(day: IsoDate): string {
  const [, month, date] = parts(day);
  return `${date} ${MONTHS_GENITIVE[month]!.slice(0, 3)}`;
}

/** 1 день, 2 дня, 5 дней. */
export function plural(n: number, one: string, few: string, many: string): string {
  const tens = n % 100;
  const units = n % 10;
  if (tens >= 11 && tens <= 14) {
    return many;
  }
  if (units === 1) {
    return one;
  }
  return units >= 2 && units <= 4 ? few : many;
}

export interface Cell {
  readonly day: IsoDate;
  readonly date: number;
  readonly weekend: boolean;
  readonly exception: CalendarDay | undefined;
}

/** Недели месяца с понедельника; пустые клетки до первого и после последнего числа — null. */
export function monthWeeks(
  year: number,
  month: number,
  exceptions: ReadonlyMap<IsoDate, CalendarDay>,
): (Cell | null)[][] {
  const length = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  const offset = (dayOfWeek(iso(year, month, 1)) + 6) % 7;
  const cells: (Cell | null)[] = Array.from({ length: offset }, () => null);
  for (let date = 1; date <= length; date++) {
    const day = iso(year, month, date);
    cells.push({ day, date, weekend: isWeekend(day), exception: exceptions.get(day) });
  }
  while (cells.length % 7 !== 0) {
    cells.push(null);
  }
  return Array.from({ length: cells.length / 7 }, (_, week) => cells.slice(week * 7, week * 7 + 7));
}

/** Торговых дней в месяце. */
export function tradingDaysInMonth(year: number, month: number, exceptions: ReadonlyMap<IsoDate, CalendarDay>): number {
  return monthWeeks(year, month, exceptions)
    .flat()
    .filter((cell) => cell !== null && isTrading(cell.day, cell.exception)).length;
}

export interface YearStats {
  readonly trading: number;
  /** Праздники, выпавшие на будни: только они сокращают торговые дни. */
  readonly holidays: number;
  readonly workingWeekends: number;
}

/** Сводка года — для сверки с календарём биржи. */
export function yearStats(year: number, exceptions: ReadonlyMap<IsoDate, CalendarDay>): YearStats {
  let trading = 0;
  for (let month = 0; month < 12; month++) {
    trading += tradingDaysInMonth(year, month, exceptions);
  }
  const days = [...exceptions.values()];
  return {
    trading,
    holidays: days.filter((d) => !d.is_trading && !isWeekend(d.day)).length,
    workingWeekends: days.filter((d) => d.is_trading && isWeekend(d.day)).length,
  };
}

/** Праздники в будни между датами (после первой, до второй включительно) — их пропустил расчёт Т+N. */
export function skippedHolidays(from: IsoDate, to: IsoDate, days: readonly CalendarDay[]): CalendarDay[] {
  return days
    .filter((d) => !d.is_trading && !isWeekend(d.day) && d.day > from && d.day <= to)
    .sort((a, b) => a.day.localeCompare(b.day));
}

export function yearOf(day: IsoDate): number {
  return parts(day)[0];
}
