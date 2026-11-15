import type { Decimal } from './api';

/**
 * Момент, на который смотрят цены: сейчас, конец вчерашнего дня, конец прошлого
 * месяца или дата и время с шагом 15 минут (решение 152). Время — московское.
 */
export type Moment =
  | { readonly kind: 'now' }
  | { readonly kind: 'yesterday' }
  | { readonly kind: 'month' }
  | { readonly kind: 'custom'; readonly day: string; readonly time: string };

/** Конец дня — 23:59: так считают отметки для оценки позиций. */
export const END_OF_DAY = '23:59';

/** Время с шагом 15 минут и конец дня. */
export const TIMES: readonly string[] = [
  ...Array.from({ length: 96 }, (_, i) => {
    const minutes = i * 15;
    return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
  }),
  END_OF_DAY,
];

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

/** Сегодня по Москве: 2026-11-15. */
export function todayInMoscow(now: Date = new Date()): string {
  return now.toLocaleDateString('en-CA', { timeZone: 'Europe/Moscow' });
}

/** Время по Москве, округлённое вниз до 15 минут: 11:05 → 11:00. */
export function quarterInMoscow(now: Date = new Date()): string {
  const [hours, minutes] = now
    .toLocaleTimeString('en-GB', { timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit' })
    .split(':')
    .map(Number);
  return `${String(hours).padStart(2, '0')}:${String(Math.floor(minutes! / 15) * 15).padStart(2, '0')}`;
}

/** День со сдвигом: 2026-11-01, −1 → 2026-10-31. */
export function shiftDay(day: string, days: number): string {
  const [year, month, date] = day.split('-').map(Number);
  return new Date(Date.UTC(year!, month! - 1, date! + days)).toISOString().slice(0, 10);
}

/** Последний день прошлого месяца. */
export function endOfPreviousMonth(today: string): string {
  return shiftDay(`${today.slice(0, 8)}01`, -1);
}

/** «октября» — месяц дня в родительном падеже: «Конец октября». */
export function monthGenitive(day: string): string {
  return MONTHS_GENITIVE[Number(day.slice(5, 7)) - 1]!;
}

/** День и время по Москве → момент для API; конец дня — 23:59:59. */
export function moscowMoment(day: string, time: string): string {
  return `${day}T${time}:${time === END_OF_DAY ? '59' : '00'}+03:00`;
}

/** Момент для API; сейчас — без момента. */
export function momentAt(moment: Moment, today: string): string | undefined {
  switch (moment.kind) {
    case 'now':
      return undefined;
    case 'yesterday':
      return moscowMoment(shiftDay(today, -1), END_OF_DAY);
    case 'month':
      return moscowMoment(endOfPreviousMonth(today), END_OF_DAY);
    case 'custom':
      return moscowMoment(moment.day, moment.time);
  }
}

/** Сдвиг на 15 минут вперёд или назад; через полночь — на соседний день. */
export function nudge(day: string, time: string, step: -1 | 1): { day: string; time: string } {
  const next = TIMES.indexOf(time) + step;
  if (next < 0) {
    return { day: shiftDay(day, -1), time: END_OF_DAY };
  }
  if (next >= TIMES.length) {
    return { day: shiftDay(day, 1), time: TIMES[0]! };
  }
  return { day, time: TIMES[next]! };
}

/** Изменение цены в процентах; нет одной из цен — null. */
export function changePercent(current: Decimal | null, base: Decimal | null): number | null {
  if (current === null || base === null || Number(base) === 0) {
    return null;
  }
  return (Number(current) / Number(base) - 1) * 100;
}

/** +0,12% · −0,40% · 0,00%; нет изменения — прочерк. */
export function formatChange(change: number | null): string {
  if (change === null) {
    return '—';
  }
  const rounded = Math.round(change * 100) / 100;
  const sign = rounded > 0 ? '+' : rounded < 0 ? '−' : '';
  return `${sign}${Math.abs(rounded).toFixed(2).replace('.', ',')}%`;
}

/** Класс для цвета изменения: рост — зелёный, падение — красный. */
export function changeClass(change: number | null): string | undefined {
  if (change === null) {
    return undefined;
  }
  const rounded = Math.round(change * 100) / 100;
  return rounded > 0 ? 'up' : rounded < 0 ? 'down' : undefined;
}
