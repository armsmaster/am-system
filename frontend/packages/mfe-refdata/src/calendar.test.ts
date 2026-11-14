import { describe, expect, it } from 'vitest';

import type { CalendarDay, IsoDate } from './api';
import {
  dayAndWeekday,
  isTrading,
  isWeekend,
  longDate,
  monthWeeks,
  plural,
  shortDate,
  skippedHolidays,
  tradingDaysInMonth,
  yearOf,
  yearStats,
} from './calendar';
import { DAYS } from './test/fixtures';

const byDay = (days: readonly CalendarDay[]) => new Map<IsoDate, CalendarDay>(days.map((d) => [d.day, d]));

describe('calendar', () => {
  it('lays a month out in weeks from Monday', () => {
    // 1 ноября 2026 — воскресенье: шесть пустых клеток, шесть недель.
    const weeks = monthWeeks(2026, 10, byDay(DAYS));

    expect(weeks).toHaveLength(6);
    expect(weeks[0]!.map((c) => c?.date ?? null)).toEqual([null, null, null, null, null, null, 1]);
    expect(weeks[5]!.map((c) => c?.date ?? null)).toEqual([30, null, null, null, null, null, null]);
    expect(weeks[1]![2]).toEqual({ day: '2026-11-04', date: 4, weekend: false, exception: DAYS[0] });
    expect(weeks[1]![5]).toMatchObject({ day: '2026-11-07', weekend: true, exception: undefined });
    // Февраль 2026: 6 пустых + 28 дней — пять недель.
    expect(monthWeeks(2026, 1, new Map())).toHaveLength(5);
  });

  it('tells trading days: weekdays, unless an exception says otherwise', () => {
    expect(isWeekend('2026-11-07')).toBe(true);
    expect(isWeekend('2026-11-04')).toBe(false);
    expect(isTrading('2026-11-04', undefined)).toBe(true);
    expect(isTrading('2026-11-04', DAYS[0])).toBe(false);
    expect(isTrading('2026-12-27', DAYS[1])).toBe(true);
    expect(isTrading('2026-12-26', undefined)).toBe(false);
  });

  it('counts trading days of a month and of a year', () => {
    // В ноябре 2026 21 будний день, 4-е — праздник.
    expect(tradingDaysInMonth(2026, 10, byDay(DAYS))).toBe(20);
    // 2026 начинается в четверг: 261 будний день; праздник минус один, рабочее воскресенье плюс один.
    expect(yearStats(2026, byDay(DAYS))).toEqual({ trading: 261, holidays: 1, workingWeekends: 1 });
    // Праздник в субботу и «рабочий» четверг ничего не меняют и в сводку не идут.
    const odd = byDay([
      ...DAYS,
      { day: '2026-11-07', is_trading: false, comment: null },
      { day: '2026-11-05', is_trading: true, comment: null },
    ]);
    expect(yearStats(2026, odd)).toEqual({ trading: 261, holidays: 1, workingWeekends: 1 });
  });

  it('lists holidays skipped between two dates', () => {
    const days: CalendarDay[] = [
      { day: '2026-11-06', is_trading: false, comment: null },
      ...DAYS,
      { day: '2026-11-07', is_trading: false, comment: 'Суббота' },
    ];

    expect(skippedHolidays('2026-11-03', '2026-11-09', days).map((d) => d.day)).toEqual(['2026-11-04', '2026-11-06']);
    // Сама дата отсчёта не пропущена.
    expect(skippedHolidays('2026-11-04', '2026-11-05', DAYS)).toEqual([]);
  });

  it('writes dates in words', () => {
    expect(longDate('2026-11-04')).toBe('4 ноября 2026, среда');
    expect(dayAndWeekday('2027-01-11')).toBe('11 января, понедельник');
    expect(shortDate('2026-12-27')).toBe('27 дек');
    expect(yearOf('2027-01-11')).toBe(2027);
  });

  it.each([
    [1, 'день'],
    [2, 'дня'],
    [4, 'дня'],
    [5, 'дней'],
    [11, 'дней'],
    [14, 'дней'],
    [21, 'день'],
    [22, 'дня'],
    [112, 'дней'],
  ])('agrees a noun with %i', (n, word) => {
    expect(plural(n, 'день', 'дня', 'дней')).toBe(word);
  });
});
