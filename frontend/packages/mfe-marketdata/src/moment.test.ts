import { describe, expect, it } from 'vitest';

import {
  changeClass,
  changePercent,
  endOfPreviousMonth,
  formatChange,
  momentAt,
  monthGenitive,
  moscowMoment,
  nudge,
  quarterInMoscow,
  shiftDay,
  TIMES,
  todayInMoscow,
} from './moment';

// Вторник 27.10.2026, 11:05 по Москве.
const NOW = new Date('2026-10-27T08:05:00Z');

describe('moment', () => {
  it('offers times every 15 minutes and the end of the day', () => {
    expect(TIMES).toHaveLength(97);
    expect(TIMES.slice(0, 3)).toEqual(['00:00', '00:15', '00:30']);
    expect(TIMES.slice(-2)).toEqual(['23:45', '23:59']);
  });

  it('knows today and the quarter of an hour in Moscow', () => {
    expect(todayInMoscow(NOW)).toBe('2026-10-27');
    expect(quarterInMoscow(NOW)).toBe('11:00');
    expect(quarterInMoscow(new Date('2026-10-27T05:59:00Z'))).toBe('08:45');
  });

  it('shifts days and finds the end of the previous month', () => {
    expect(shiftDay('2026-11-01', -1)).toBe('2026-10-31');
    expect(shiftDay('2026-12-31', 1)).toBe('2027-01-01');
    expect(endOfPreviousMonth('2026-11-15')).toBe('2026-10-31');
    expect(endOfPreviousMonth('2026-01-10')).toBe('2025-12-31');
    expect(monthGenitive('2026-10-31')).toBe('октября');
  });

  it('turns a choice into a moment for the service', () => {
    expect(moscowMoment('2026-11-14', '23:59')).toBe('2026-11-14T23:59:59+03:00');
    expect(moscowMoment('2026-11-14', '12:15')).toBe('2026-11-14T12:15:00+03:00');
    expect(momentAt({ kind: 'now' }, '2026-10-27')).toBeUndefined();
    expect(momentAt({ kind: 'yesterday' }, '2026-10-27')).toBe('2026-10-26T23:59:59+03:00');
    expect(momentAt({ kind: 'month' }, '2026-10-27')).toBe('2026-09-30T23:59:59+03:00');
    expect(momentAt({ kind: 'custom', day: '2026-10-20', time: '18:45' }, '2026-10-27')).toBe(
      '2026-10-20T18:45:00+03:00',
    );
  });

  it('steps by 15 minutes across midnight', () => {
    expect(nudge('2026-10-27', '11:00', 1)).toEqual({ day: '2026-10-27', time: '11:15' });
    expect(nudge('2026-10-27', '23:45', 1)).toEqual({ day: '2026-10-27', time: '23:59' });
    expect(nudge('2026-10-27', '23:59', 1)).toEqual({ day: '2026-10-28', time: '00:00' });
    expect(nudge('2026-10-27', '00:00', -1)).toEqual({ day: '2026-10-26', time: '23:59' });
  });

  it('measures the change in percent', () => {
    expect(changePercent('101', '100')).toBeCloseTo(1);
    expect(changePercent(null, '100')).toBeNull();
    expect(changePercent('100', null)).toBeNull();
    expect(changePercent('100', '0')).toBeNull();
    expect([formatChange(0.1449), formatChange(-0.4), formatChange(0.001), formatChange(null)]).toEqual([
      '+0,14%',
      '−0,40%',
      '0,00%',
      '—',
    ]);
    expect([changeClass(0.2), changeClass(-0.2), changeClass(0.001), changeClass(null)]).toEqual([
      'up',
      'down',
      undefined,
      undefined,
    ]);
  });
});
