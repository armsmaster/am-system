import { describe, expect, it } from 'vitest';

import type { FeeRate } from './api';
import {
  activeRates,
  chartData,
  fitFilter,
  formatFilter,
  fromSlider,
  HALF_LIFE_RANGE,
  maxHalfLife,
  MEMORY_RANGE,
  monthsBefore,
  parseFilter,
  parseMetric,
  parsePeriod,
  periodChange,
  periodRange,
  toSlider,
} from './nav';
import { FEE_RATES, navHistory } from './test/fixtures';

const HISTORY = navHistory();

describe('NAV page rules (decision 168)', () => {
  it('reads the remembered choices', () => {
    expect([parseMetric('nav'), parseMetric('x'), parseMetric(null)]).toEqual(['nav', 'unit', 'unit']);
    expect(['quarter', 'year', 'all', 'custom', 'week', null].map(parsePeriod)).toEqual([
      'quarter',
      'year',
      'all',
      'custom',
      'month',
      'month',
    ]);
  });

  it('counts periods back in calendar months', () => {
    expect(monthsBefore('2026-12-03', 1)).toBe('2026-11-03');
    expect(monthsBefore('2026-03-31', 1)).toBe('2026-02-28');
    expect(monthsBefore('2026-01-15', 3)).toBe('2025-10-15');
    const custom = { from: '2026-10-01', till: '2026-10-31' };
    expect(periodRange('month', '2026-12-03', null)).toEqual({ from: '2026-11-04', till: undefined });
    expect(periodRange('quarter', '2026-12-03', null)).toEqual({ from: '2026-09-04', till: undefined });
    expect(periodRange('year', '2026-12-03', null)).toEqual({ from: '2025-12-04', till: undefined });
    expect(periodRange('all', '2026-12-03', custom)).toEqual({ from: undefined, till: undefined });
    expect(periodRange('custom', '2026-12-03', custom)).toEqual(custom);
    expect(periodRange('custom', '2026-12-03', null)).toEqual({ from: undefined, till: undefined });
  });

  it('keeps the latest started rate of each kind', () => {
    const rate = (kind: FeeRate['kind'], value: string, from: string): FeeRate => ({ ...FEE_RATES[0]!, kind, rate: value, valid_from: from });
    const rates = [
      rate('management', '1.5', '2026-01-01'),
      rate('management', '1.2', '2026-11-01'),
      rate('management', '0.9', '2027-01-01'),
      rate('specdep', '0.15', '2026-01-01'),
      rate('registrar', '0', '2026-01-01'),
    ];
    expect(activeRates(rates, '2026-12-03').map((r) => [r.kind, r.rate])).toEqual([
      ['management', '1.2'],
      ['specdep', '0.15'],
    ]);
  });

  it('draws one NAV line, or the unit price with Kalman; the forecast stays in its card', () => {
    expect(chartData(HISTORY.points, { metric: 'nav', kalman: true, benchmark: 'LQDT' })).toEqual({
      percent: false,
      lines: [{ key: 'nav', label: 'СЧА фонда', data: expect.any(Array) }],
    });
    const plain = chartData(HISTORY.points, { metric: 'unit', kalman: false, benchmark: null });
    expect(plain.lines.map((l) => [l.key, l.data.length])).toEqual([['unit', 8]]);
    const smooth = chartData(HISTORY.points, { metric: 'unit', kalman: true, benchmark: null });
    expect(smooth.lines.map((l) => [l.key, l.data.length])).toEqual([
      ['unit', 8],
      ['smoothed', 8],
    ]);
    expect(smooth.lines[1]!.data.at(-1)).toEqual({ time: '2026-12-03', value: 1001.02 });
  });

  it('compares with LQDT in percent from the start of the period', () => {
    const found = chartData(HISTORY.points, { metric: 'unit', kalman: true, benchmark: 'LQDT' });
    expect(found.percent).toBe(true);
    const [unit, smoothed, benchmark] = found.lines;
    expect(unit!.data[0]).toEqual({ time: '2026-11-26', value: 0 });
    expect(unit!.data.at(-1)!.value).toBeCloseTo(0.095);
    expect(smoothed!.data.at(-1)!.value).toBeCloseTo(0.102);
    expect(benchmark).toMatchObject({ key: 'benchmark', label: 'LQDT' });
    expect(benchmark!.data[0]).toEqual({ time: '2026-11-27', value: 0 });
    expect(benchmark!.data.at(-1)!.value).toBeCloseTo(0.114, 3);
    // Цен LQDT за период нет — без процентов.
    const none = HISTORY.points.map((p) => ({ ...p, benchmark: null }));
    expect(chartData(none, { metric: 'unit', kalman: false, benchmark: 'LQDT' }).percent).toBe(false);
  });

  it('measures the change over the period', () => {
    const change = periodChange(HISTORY.points, (p) => p.benchmark);
    expect(change!.percent).toBeCloseTo(0.1143, 3);
    expect(change!.annual).toBeCloseTo((0.1143 * 365) / 6, 2);
    expect(periodChange(HISTORY.points.slice(0, 2), (p) => p.benchmark)).toBeNull();
  });
});

describe('own Kalman settings (decision 174)', () => {
  it('limits smoothing by the trend memory, as the server does', () => {
    expect([5, 50, 500].map(maxHalfLife)).toEqual([0.8, 3.2, 10.7]);
    expect(fitFilter(10, 50)).toEqual({ halfLife: 3.2, memory: 50 });
    expect(fitFilter(0.1, 2)).toEqual({ halfLife: 0.5, memory: 5 });
    expect(fitFilter(30, 900)).toEqual({ halfLife: 10.7, memory: 500 });
    expect(fitFilter(1.234, 30.4)).toEqual({ halfLife: 1.2, memory: 30 });
  });

  it('remembers own settings and forgets broken ones', () => {
    expect(parseFilter(formatFilter({ halfLife: 1.5, memory: 30 }))).toEqual({ halfLife: 1.5, memory: 30 });
    expect(parseFilter('{"halfLife": 9, "memory": 50}')).toEqual({ halfLife: 3.2, memory: 50 });
    expect([parseFilter(null), parseFilter('null'), parseFilter('{"halfLife": "1"}'), parseFilter('{oops')]).toEqual([
      null,
      null,
      null,
      null,
    ]);
    expect(formatFilter(null)).toBe('null');
  });

  it('moves sliders on a log scale', () => {
    expect(toSlider(0.5, HALF_LIFE_RANGE)).toBe(0);
    expect(toSlider(500, MEMORY_RANGE)).toBe(1000);
    expect(toSlider(50, MEMORY_RANGE)).toBe(500);
    expect(fromSlider(500, MEMORY_RANGE)).toBeCloseTo(50);
  });
});
