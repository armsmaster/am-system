import { describe, expect, it } from 'vitest';

import {
  couponKindLabel,
  formatDate,
  formatDecimal,
  formatTime,
  issTypeLabel,
  todayInMoscow,
  weekday,
} from './format';

describe('issTypeLabel', () => {
  it('names the ISS type, keeps an unknown code and dashes no type', () => {
    expect(issTypeLabel('ofz_bond')).toBe('ОФЗ');
    expect(issTypeLabel('euro_bond')).toBe('euro_bond');
    expect(issTypeLabel(null)).toBe('—');
  });
});

describe('formatDate', () => {
  it('writes the date the Russian way', () => {
    expect(formatDate('2026-10-26')).toBe('26.10.2026');
  });

  it('shows a dash for no date', () => {
    expect(formatDate(null)).toBe('—');
  });
});

describe('formatTime', () => {
  it('shows Moscow time', () => {
    expect(formatTime('2026-10-26T03:00:00Z')).toBe('26.10.2026, 06:00');
  });

  it('shows a dash for no time', () => {
    expect(formatTime(null)).toBe('—');
  });
});

describe('formatDecimal', () => {
  it.each([
    ['1234567.50', '', '1 234 567,50'],
    ['1000', 'RUB', '1 000 RUB'],
    ['-15', '', '-15'],
    ['0.000001', '', '0,000001'],
  ])('%s → %s', (value, unit, expected) => {
    expect(formatDecimal(value, unit)).toBe(expected.replaceAll(' ', ' '));
  });

  it('keeps every digit the service sent', () => {
    expect(formatDecimal('35.40000000000000000001')).toBe('35,40000000000000000001');
  });

  it('shows a dash for no value', () => {
    expect(formatDecimal(null, 'RUB')).toBe('—');
  });
});

describe('weekday', () => {
  it.each([
    ['2026-10-26', 'пн'],
    ['2026-11-01', 'вс'],
    ['2026-12-26', 'сб'],
  ])('%s is %s', (day, expected) => {
    expect(weekday(day)).toBe(expected);
  });
});

describe('todayInMoscow', () => {
  it('is already tomorrow in Moscow late in the evening UTC', () => {
    expect(todayInMoscow(new Date('2026-10-26T22:30:00Z'))).toBe('2026-10-27');
  });

  it('defaults to now', () => {
    expect(todayInMoscow()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe('couponKindLabel', () => {
  it('names the coupon kind', () => {
    expect(couponKindLabel('floater')).toBe('флоатер');
  });

  it('shows a dash for a fund share', () => {
    expect(couponKindLabel(null)).toBe('—');
  });
});
