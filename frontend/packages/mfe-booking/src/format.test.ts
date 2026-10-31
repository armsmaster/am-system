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
} from './format';

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
});
