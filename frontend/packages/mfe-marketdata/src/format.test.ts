import { describe, expect, it } from 'vitest';

import { formatPrice, formatTime, fromMoscowInput, moscowInput, sourceLabel } from './format';

describe('formatPrice', () => {
  it.each([
    ['51.823000', '51,823'],
    ['1065.100000', '1 065,1'],
    ['99.000000', '99'],
    ['7.7', '7,7'],
    ['100', '100'],
  ])('%s → %s', (value, expected) => {
    expect(formatPrice(value)).toBe(expected);
  });

  it('shows a dash for no price', () => {
    expect(formatPrice(null)).toBe('—');
  });
});

describe('time', () => {
  it('shows Moscow time', () => {
    expect(formatTime('2026-10-27T08:05:00Z')).toBe('27.10, 11:05');
  });

  it('converts the moment field from and to Moscow time', () => {
    expect(moscowInput(new Date('2026-10-27T08:05:30Z'))).toBe('2026-10-27T11:05');
    expect(fromMoscowInput('2026-10-27T11:05')).toBe('2026-10-27T11:05:00+03:00');
  });
});

describe('sourceLabel', () => {
  it('names service sources and keeps script codes', () => {
    expect([sourceLabel('iss'), sourceLabel('bars'), sourceLabel('quik')]).toEqual([
      'MOEX ISS',
      'по барам',
      'quik',
    ]);
  });
});
