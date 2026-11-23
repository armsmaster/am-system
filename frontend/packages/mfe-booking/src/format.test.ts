import { describe, expect, it } from 'vitest';

import {
  decimalInput,
  formatDate,
  formatMoney,
  formatPrice,
  formatQuantity,
  formatTime,
  fromMoscowInput,
  moscowInput,
  moscowToday,
  sumMoney,
  clockTime,
  dayTitle,
  incompleteFlags,
  monthStart,
  plural,
  shiftDay,
  shortMoney,
  tradesCount,
  weekday,
} from './format';

describe('dates and counts of the trades page', () => {
  it('names days and periods', () => {
    expect(shiftDay('2026-12-01', -1)).toBe('2026-11-30');
    expect(shiftDay('2026-11-30', 29)).toBe('2026-12-29');
    expect(monthStart('2026-11-30')).toBe('2026-11-01');
    expect(dayTitle('2026-11-20')).toBe('пт, 20 ноября');
    expect(weekday('2026-11-23')).toBe('пн');
    expect(clockTime('2026-11-20T14:12:00Z')).toBe('17:12');
  });

  it.each([
    [1, '1 сделка'],
    [2, '2 сделки'],
    [4, '4 сделки'],
    [5, '5 сделок'],
    [11, '11 сделок'],
    [14, '14 сделок'],
    [21, '21 сделка'],
    [22, '22 сделки'],
    [112, '112 сделок'],
  ])('counts %i trades', (count, text) => {
    expect(tradesCount(count)).toBe(text);
  });

  it('agrees a word with a number', () => {
    expect(plural(1, 'бумага', 'бумаги', 'бумаг')).toBe('1 бумага');
    expect(plural(3, 'бумага', 'бумаги', 'бумаг')).toBe('3 бумаги');
    expect(plural(1012, 'бумага', 'бумаги', 'бумаг')).toBe('1 012 бумаг');
  });

  it('shortens big money to millions', () => {
    expect(shortMoney('28000500.00')).toBe('28,0 млн ₽');
    expect(shortMoney('-1596099.00')).toBe('−1,6 млн ₽');
    expect(shortMoney('2041.20')).toBe('2 041 ₽');
    expect(shortMoney('-2000.00')).toBe('−2 000 ₽');
  });

  it('keeps a linker coupon by the issue terms out of an incomplete valuation', () => {
    expect(incompleteFlags(['due_estimated', 'stale_price'])).toEqual(['stale_price']);
  });
});

describe('format', () => {
  it.each([
    ['28000500.00', '28 000 500,00'],
    ['-16776300', '−16 776 300,00'],
    ['0.5', '0,50'],
  ])('money %s → %s', (value, expected) => {
    expect(formatMoney(value)).toBe(expected);
  });

  it.each([
    ['52.500000', '52,5'],
    ['1000.000000', '1 000'],
    ['-0.25', '−0,25'],
  ])('price %s → %s', (value, expected) => {
    expect(formatPrice(value)).toBe(expected);
  });

  it('shows a dash for nothing', () => {
    expect([formatMoney(null), formatPrice(null), formatDate(null)]).toEqual(['—', '—', '—']);
  });

  it('formats quantities, dates and Moscow time', () => {
    expect([formatQuantity(50000), formatQuantity(-10000)]).toEqual(['50 000', '−10 000']);
    expect(formatDate('2026-11-30')).toBe('30.11.2026');
    expect(formatTime('2026-11-27T08:42:00Z')).toBe('27.11, 11:42');
    expect(moscowInput(new Date('2026-11-27T08:42:30Z'))).toBe('2026-11-27T11:42');
    expect(fromMoscowInput('2026-11-27T11:42')).toBe('2026-11-27T11:42:00+03:00');
    expect(moscowToday(new Date('2026-12-02T22:30:00Z'))).toBe('2026-12-03');
    expect(decimalInput(' 1 000,5 ')).toBe('1000.5');
  });

  it('sums money to the kopeck', () => {
    expect(sumMoney(['0.10', '0.20', '-1.05', '1000000000.7'])).toBe('999999999.95');
    expect(sumMoney(['-0.30', '0.05'])).toBe('-0.25');
    expect(sumMoney([])).toBe('0.00');
  });
});
