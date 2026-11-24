import { describe, expect, it } from 'vitest';

import type { Line } from './api';
import {
  activePreset,
  bondType,
  breakdown,
  columnLabel,
  compactMoney,
  defaultColumns,
  fixed,
  groupLines,
  makeRefs,
  monthEndLabel,
  moveColumn,
  nearbyWeekday,
  parseChoice,
  parseGrouping,
  parseSize,
  parseView,
  pickRating,
  presetColumns,
  previousMonthEnd,
  previousQuarterEnd,
  quarterEndLabel,
  rubles,
  shareText,
  shortDay,
  shownColumns,
  signed,
  toggleColumn,
  validGrouping,
  yieldText,
} from './positions';
import { ATTRIBUTES, positions, SCALE } from './test/fixtures';

const [OFZ] = positions().lines as [Line];
const REFS = makeRefs(ATTRIBUTES, SCALE);
const { classifiers } = REFS;

describe('columns (decision 164)', () => {
  it('starts with the default set and keeps only known columns', () => {
    expect(defaultColumns(classifiers)).toContain('class:c-sector');
    expect(defaultColumns(classifiers)).not.toContain('class:c-rank');
    expect(shownColumns({ columns: ['value', 'class:gone'], custom: null }, classifiers)).toEqual(['value']);
  });

  it('names columns for the order list', () => {
    expect(columnLabel('realized:quarter', classifiers)).toBe('Реализованный: с нач. кв.');
    expect(columnLabel('class:c-rank', classifiers)).toBe('Очередность');
    expect(columnLabel('class:gone', classifiers)).toBe('class:gone');
    expect(columnLabel('dv01', classifiers)).toBe('DV01');
    expect(columnLabel('unknown', classifiers)).toBe('unknown');
  });

  it('recognises a preset and otherwise calls the set my own', () => {
    expect(activePreset(presetColumns('risk', classifiers), classifiers)).toBe('risk');
    expect(activePreset(['value'], classifiers)).toBe('mine');
  });

  it('puts a switched-on column in its natural place and moves columns', () => {
    expect(toggleColumn(['value', 'rating'], 'yield', classifiers)).toEqual(['value', 'yield', 'rating']);
    expect(toggleColumn(['value'], 'class:c-rank', classifiers)).toEqual(['value', 'class:c-rank']);
    expect(toggleColumn(['value', 'yield'], 'quantity', classifiers)).toEqual(['quantity', 'value', 'yield']);
    expect(toggleColumn(['yield', 'result:year'], 'realized:week', classifiers)).toEqual(['yield', 'result:year', 'realized:week']);
    expect(toggleColumn(['value', 'yield'], 'value', classifiers)).toEqual(['yield']);
    expect(moveColumn(['a', 'b', 'c'], 'b', 1)).toEqual(['a', 'c', 'b']);
    expect(moveColumn(['a', 'b', 'c'], 'b', -1)).toEqual(['b', 'a', 'c']);
  });

  it('reads the stored choice and ignores a broken one', () => {
    expect(parseChoice('{"columns":["value"],"custom":null}')).toEqual({ columns: ['value'], custom: null });
    expect(parseChoice('{"columns":[1],"custom":"x"}')).toEqual({ columns: null, custom: null });
    expect(parseChoice('not json')).toEqual({ columns: null, custom: null });
    expect(parseChoice(null)).toEqual({ columns: null, custom: null });
    expect([parseSize('large'), parseSize('huge'), parseGrouping(null)]).toEqual(['large', 'normal', 'type']);
  });
});

describe('reference data', () => {
  it('picks the highest rating of the paper, else of the issuer', () => {
    expect(pickRating(OFZ, REFS)).toEqual({ kind: 'rated', rating: 'AAA(RU)', grade: 'AAA', agency: 'АКРА', issuer: true });
    // Без шкалы — первая запись: ступени не с чем сравнить.
    expect(pickRating(OFZ, makeRefs(ATTRIBUTES, null))).toMatchObject({ rating: 'ruAA+' });
    // Ступени нет в шкале — она ниже всех известных.
    expect(pickRating(OFZ, makeRefs(ATTRIBUTES, { grades: ['AA+'] }))).toMatchObject({ rating: 'ruAA+' });
    expect(pickRating(OFZ, makeRefs(null, null))).toEqual({ kind: 'unknown' });
  });

  it('takes the bond type from analytics, else from the issue terms', () => {
    const terms = (coupon_kind: 'fixed' | 'floater' | 'unknown', nominal_indexation: 'none' | 'ruonia' = 'none') =>
      makeRefs({ ...ATTRIBUTES, instruments: [{ ...ATTRIBUTES.instruments[0]!, coupon_kind, nominal_indexation }] }, null);
    const unknown = { ...OFZ, bond_type: null };
    expect(bondType(OFZ, makeRefs(null, null))).toBe('fixed');
    expect(bondType(unknown, terms('floater'))).toBe('floater');
    expect(bondType(unknown, terms('fixed', 'ruonia'))).toBe('linker');
    expect(bondType(unknown, terms('unknown'))).toBeNull();
    expect(bondType(unknown, makeRefs(null, null))).toBeNull();
  });

  it('groups by a classifier and falls back to the type for a removed one', () => {
    expect(validGrouping('class:gone', classifiers)).toBe('type');
    expect(validGrouping('class:c-rank', classifiers)).toBe('class:c-rank');
    expect(groupLines([OFZ], 'class:c-rank', REFS).map((g) => g.label)).toEqual(['Старший']);
  });
});

describe('numbers', () => {
  it('formats yields, spreads and results', () => {
    expect(yieldText({ ...OFZ, yield_continuous: null }, 'continuous')).toBeNull();
    expect(yieldText({ ...OFZ, yield_percent: null, spread_bp: '81.51', spread_continuous_bp: null }, 'continuous')).toBeNull();
    expect(yieldText({ ...OFZ, yield_percent: null, spread_bp: '81.51' }, 'annual')).toBe('спред 82 бп');
    expect(signed('0.40')).toEqual({ text: '0', tone: '' });
    expect(signed('-563.6')).toEqual({ text: '−564', tone: 'down' });
    expect(fixed('-0.125', 2)).toBe('−0,13');
    expect(rubles(null)).toBe('—');
  });
});

describe('quick dates', () => {
  it('finds the ends of the previous month and quarter', () => {
    expect(previousMonthEnd('2026-11-24')).toBe('2026-10-31');
    expect(previousQuarterEnd('2026-11-24')).toBe('2026-09-30');
    expect(previousQuarterEnd('2027-01-15')).toBe('2026-12-31');
    expect(monthEndLabel('2026-10-31', '2026-11-24')).toBe('Конец октября');
    expect(monthEndLabel('2026-12-31', '2027-01-15')).toBe('Конец декабря 2026');
    expect(quarterEndLabel('2026-09-30', '2026-11-24')).toBe('Конец III кв.');
    expect(quarterEndLabel('2026-12-31', '2027-01-15')).toBe('Конец IV кв. 2026');
  });

  it('names a neighbour day and skips weekends without the calendar', () => {
    expect(shortDay('2026-11-23')).toBe('пн 23.11');
    expect(nearbyWeekday('2026-11-27', 1)).toBe('2026-11-30');
    expect(nearbyWeekday('2026-11-30', -1)).toBe('2026-11-27');
    expect(nearbyWeekday('2026-11-25', 1)).toBe('2026-11-26');
  });
});

describe('MV structure (decision 165)', () => {
  /** Бумага эмитента ``n`` с MV ``value`` ₽. */
  const paper = (n: number, value: string): Line => ({
    ...OFZ,
    instrument_id: `i-${n}`,
    clean: value,
    accrued_value: '0.00',
  });
  const issuers = (count: number) =>
    makeRefs(
      {
        ...ATTRIBUTES,
        instruments: Array.from({ length: count }, (_, n) => ({ ...ATTRIBUTES.instruments[0]!, id: `i-${n}`, issuer_id: `e-${n}` })),
        issuers: Array.from({ length: count }, (_, n) => ({ ...ATTRIBUTES.issuers[0]!, id: `e-${n}`, name: `Эмитент ${n}` })),
      },
      SCALE,
    );

  it('shows seven largest issuers and folds the rest into one row', () => {
    const lines = Array.from({ length: 9 }, (_, n) => paper(n, `${(n + 1) * 1000}.00`));
    const buckets = breakdown(lines, 'issuer', issuers(9), null);
    expect(buckets.map((b) => b.label)).toEqual([
      'Эмитент 8',
      'Эмитент 7',
      'Эмитент 6',
      'Эмитент 5',
      'Эмитент 4',
      'Эмитент 3',
      'Эмитент 2',
      'Прочие · 2',
    ]);
    expect(buckets[7]).toMatchObject({ tone: 'rest', value: '3000.00', ids: ['i-0', 'i-1'] });
    expect(buckets[0]!.share).toBeCloseTo(9000 / 45000);
    // Равные группы — по алфавиту.
    expect(breakdown([paper(2, '5.00'), paper(1, '5.00')], 'issuer', issuers(3), null).map((b) => b.label)).toEqual([
      'Эмитент 1',
      'Эмитент 2',
    ]);
    // Восемь эмитентов — все отдельно: «Прочие» из одного не нужны.
    expect(breakdown(lines.slice(1), 'issuer', issuers(9), null).map((b) => b.tone)).not.toContain('rest');
  });

  it('orders ratings by the scale and puts the missing ones last', () => {
    const rated = (id: string, grade: string) => ({
      ...ATTRIBUTES.instruments[0]!,
      id,
      issuer_id: null,
      ratings: { status: 'rated' as const, ratings: [{ agency: 'a', agency_name: 'А', rating: grade, grade, since: '2025-01-01' }] },
    });
    const refs = (scale: typeof SCALE | null) =>
      makeRefs(
        {
          ...ATTRIBUTES,
          instruments: [
            rated('i-1', 'A+'),
            rated('i-2', 'AAA'),
            rated('i-3', 'BB'),
            rated('i-4', 'B'),
            { ...ATTRIBUTES.instruments[0]!, id: 'i-5', issuer_id: null, ratings: { status: 'unrated', ratings: [] } },
          ],
        },
        scale,
      );
    const share = { ...paper(7, '100.00'), kind: 'fund_share' as const };
    const lines = [paper(6, '100.00'), share, ...[1, 2, 3, 4, 5].map((n) => paper(n, '100.00'))];
    expect(breakdown(lines, 'rating', refs(SCALE), null).map((b) => b.label)).toEqual([
      'AAA',
      'A+',
      'B',
      'BB',
      'Нет рейтинга',
      'Нет данных',
      'Паи фондов',
    ]);
    expect(breakdown(lines, 'rating', refs(null), null).map((b) => b.label).slice(0, 4)).toEqual(['A+', 'AAA', 'B', 'BB']);
  });

  it('puts papers without a classifier value together and handles an empty total', () => {
    const lines = [paper(1, '0.00'), paper(2, '0.00')];
    expect(breakdown(lines, 'class', REFS, null)).toEqual([
      { key: '', label: 'Без значения', tone: 'missing', value: '0.00', share: 0, ids: ['i-1', 'i-2'] },
    ]);
  });

  it('formats amounts and shares for the bars', () => {
    expect(compactMoney('21485951.37')).toBe('21,5 млн');
    expect(compactMoney('-203544.00')).toBe('−204 тыс.');
    expect(compactMoney('950.40')).toBe('950');
    expect([shareText(0.4795), shareText(0.004), shareText(-0.004), shareText(0)]).toEqual(['48%', '<1%', '−<1%', '0%']);
    expect([parseView('structure'), parseView('chart'), parseView(null)]).toEqual(['structure', 'table', 'table']);
  });
});
