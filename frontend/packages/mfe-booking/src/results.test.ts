import { describe, expect, it } from 'vitest';

import {
  anyValue,
  columnDates,
  columnTitle,
  DEFAULT_COLUMNS,
  expensesTotal,
  lineGroups,
  parseColumns,
  parseCustom,
  parseFlag,
  previousMonth,
  securitiesTotal,
  shownPeriods,
} from './results';
import { period, resultSummary } from './test/fixtures';

describe('results statement rules (decision 167)', () => {
  it('reads the remembered choices and ignores broken ones', () => {
    expect(parseColumns('["all","day","gone"]')).toEqual(['day', 'all']);
    expect(parseColumns('{"day":true}')).toEqual(DEFAULT_COLUMNS);
    expect(parseColumns('not json')).toEqual(DEFAULT_COLUMNS);
    expect(parseColumns(null)).toEqual(DEFAULT_COLUMNS);
    expect(parseCustom('{"from":"2026-10-01","till":"2026-10-31"}')).toEqual({ from: '2026-10-01', till: '2026-10-31' });
    expect(parseCustom('{"from":"октябрь"}')).toBeNull();
    expect(parseCustom('{')).toBeNull();
    expect(parseCustom(null)).toBeNull();
    expect([parseFlag('true'), parseFlag('false'), parseFlag(null)]).toEqual([true, false, false]);
  });

  it('offers the previous month as a custom period and names a month column', () => {
    expect(previousMonth('2026-11-25')).toEqual({ from: '2026-10-01', till: '2026-10-31' });
    expect(previousMonth('2026-01-15')).toEqual({ from: '2025-12-01', till: '2025-12-31' });
    const custom = (first: string, last: string) => period({ key: 'custom', first, last });
    expect(columnTitle(custom('2026-10-01', '2026-10-31'), '2026-11-25')).toBe('Октябрь');
    expect(columnTitle(custom('2025-12-01', '2025-12-31'), '2026-11-25')).toBe('Декабрь 2025');
    expect(columnTitle(custom('2026-10-01', '2026-10-30'), '2026-11-25')).toBe('Свой период');
    expect(columnTitle(custom('2026-10-02', '2026-10-31'), '2026-11-25')).toBe('Свой период');
    expect(columnTitle(period(), '2026-11-25')).toBe('Неделя');
  });

  it('writes the dates under each column', () => {
    const dates = resultSummary().periods.map(columnDates);
    expect(dates).toEqual(['03.12', '27.11–03.12', '04.11–03.12', 'с 01.10', 'с 01.01', 'с 26.11.2026']);
    expect(columnDates(period({ key: 'custom', first: '2026-10-01', last: '2026-10-31' }))).toBe('01.10–31.10');
  });

  it('keeps the chosen periods and a custom one last', () => {
    const summary = resultSummary({ periods: [...resultSummary().periods, period({ key: 'custom' })] });
    expect(shownPeriods(summary, ['all', 'day']).map((p) => p.key)).toEqual(['day', 'all', 'custom']);
  });

  it('sums the securities and the expenses and finds rows worth showing', () => {
    const found = period({ fees: '-10.00', expenses: '-3000.00', charges: '-500.00', correction: '20.00' });
    expect([securitiesTotal(found), expensesTotal(found)]).toEqual(['95390.00', '-3480.00']);
    expect([anyValue([found], (p) => p.repo), anyValue([found], (p) => p.fees)]).toEqual([false, true]);
  });

  it('orders bonds by the longest shown period and splits off the sold ones', () => {
    const line = (id: string, held: boolean, week: string, all: string) => ({
      instrument_id: id,
      secid: id.toUpperCase(),
      short_name: id,
      held,
      totals: { week, all },
    });
    const lines = [
      line('b', true, '10.00', '-500.00'),
      line('a', true, '10.00', '500.00'),
      line('c', true, '0.00', '900.00'),
      line('z', false, '0.00', '0.00'),
      line('y', false, '-1.00', '-1.00'),
    ];
    const week = period();
    const all = period({ key: 'all', days: 400 });
    expect(lineGroups(lines, [week, all])).toEqual({
      held: [lines[2], lines[1], lines[0]],
      closed: [lines[4]],
    });
    // Только неделя: бумага без результата за неделю не показывается.
    expect(lineGroups(lines, [week]).held.map((l) => l.short_name)).toEqual(['a', 'b']);
    // Нет итога за период — ноль.
    const bare = { ...lines[0]!, totals: {} };
    expect(lineGroups([bare], [week])).toEqual({ held: [], closed: [] });
  });
});
