import { describe, expect, it } from 'vitest';

import { SUMMARY } from './test/fixtures';
import {
  couponCaption,
  couponKindLabel,
  couponLength,
  currencySign,
  floaterCaption,
  formatDate,
  formatDecimal,
  formatTime,
  issTypeLabel,
  periodLabel,
  shortIssuer,
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

describe('couponCaption', () => {
  it.each([
    ['a fixed coupon', {}, 'фикс 7,1%'],
    ['a fixed coupon without a known rate', { coupon_rate: null }, 'фикс'],
    ['a coupon of unknown kind with a rate', { coupon_kind: 'unknown', coupon_rate: '14.30' }, 'купон 14,3%'],
    ['a linker indexed to an unknown index', { nominal_indexation: 'unknown', coupon_rate: null }, 'линкер'],
  ] as const)('%s', (_name, fields, caption) => {
    expect(couponCaption({ ...SUMMARY, ...fields })).toBe(caption);
  });
});

describe('floaterCaption', () => {
  it.each([
    [null, null, 'флоатер'],
    ['ruonia', null, 'RUONIA'],
    ['rusfar', '-20.50', 'RUSFAR − 20,5 бп'],
    ['other', '100', 'плавающая ставка + 100 бп'],
  ] as const)('%s %s', (base, margin, caption) => {
    expect(floaterCaption(base, margin)).toBe(caption);
  });
});

describe('coupon length', () => {
  it.each([
    ['2026-05-27', '2026-11-25', '182D'],
    ['2026-11-12', '2026-12-12', '1M'],
    ['2026-06-15', '2026-12-15', '6M'],
    ['2025-12-15', '2026-12-15', '1Y'],
  ])('%s → %s', (start, end, label) => {
    expect(periodLabel(start, end)).toBe(label);
  });

  it('takes the most common period: an odd one does not count', () => {
    const coupon = (start: string, date: string) => ({ date, start_date: start, record_date: null, rate: null, amount: null });
    expect(
      couponLength([
        coupon('2026-11-12', '2026-12-12'),
        coupon('2026-12-12', '2027-01-12'),
        coupon('2027-01-12', '2027-01-20'),
      ]),
    ).toBe('1M');
    expect(couponLength([{ ...coupon('2026-11-01', '2026-11-12'), start_date: null }])).toBeNull();
  });
});

describe('shortIssuer', () => {
  it.each([
    ['Публичное акционерное общество "Газпром нефть"', 'ПАО «Газпром нефть»'],
    ['Акционерное общество ВИМ Инвестиции', 'АО ВИМ Инвестиции'],
    ['открытое акционерное общество "Российские железные дороги"', 'ОАО «Российские железные дороги»'],
    ['Закрытое акционерное общество "Ромашка"', 'ЗАО «Ромашка»'],
    ['Общество с ограниченной ответственностью "Ромашка"', 'ООО «Ромашка»'],
    ['государственная корпорация развития "ВЭБ.РФ"', 'ГК «ВЭБ.РФ»'],
    ['Министерство финансов Российской Федерации', 'Минфин России'],
    ['ДОМ.РФ', 'ДОМ.РФ'],
  ])('%s', (name, short) => {
    expect(shortIssuer(name)).toBe(short);
  });
});

describe('currencySign', () => {
  it('writes the rouble as a sign and other currencies as codes', () => {
    expect([currencySign('RUB'), currencySign('USD')]).toEqual(['₽', 'USD']);
  });
});
