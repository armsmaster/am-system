import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { chartData } from '../nav';
import { navHistory } from '../test/fixtures';
import { axisLabel, NavChart } from './NavChart';

// Canvas в jsdom не рисуется: проверяем, какие данные и настройки получает библиотека графиков.
const charts = vi.hoisted(() => ({
  created: [] as Record<string, unknown>[],
  added: [] as { options: Record<string, unknown>; data: unknown[] }[],
  removed: { count: 0 },
}));

vi.mock('lightweight-charts', () => ({
  LineSeries: 'line',
  CrosshairMode: { Magnet: 1 },
  LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 },
  createChart: (_element: unknown, options: Record<string, unknown>) => {
    charts.created.push(options);
    return {
      addSeries: (_kind: string, options: Record<string, unknown>) => {
        const entry = { options, data: [] as unknown[] };
        charts.added.push(entry);
        return { setData: (data: unknown[]) => entry.data.push(...data) };
      },
      timeScale: () => ({ fitContent: () => undefined }),
      remove: () => {
        charts.removed.count += 1;
      },
    };
  },
}));

const HISTORY = navHistory();

beforeEach(() => {
  charts.created.length = 0;
  charts.added.length = 0;
  charts.removed.count = 0;
});

describe('NavChart', () => {
  it('draws the unit price and Kalman with a legend', () => {
    const data = chartData(HISTORY.points, { metric: 'unit', kalman: true, benchmark: null });
    const { unmount } = render(<NavChart data={data} title="Стоимость пая" />);

    expect(screen.getByRole('img', { name: 'Стоимость пая' })).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Линии графика' })).toHaveTextContent('Стоимость паяКалман');
    expect(charts.added.map((s) => [s.options.title, s.options.lineStyle, s.options.lastValueVisible, s.data.length])).toEqual([
      ['Стоимость пая', 0, true, 8],
      ['Калман', 2, true, 8],
    ]);
    expect(charts.added[0]!.data[0]).toEqual({ time: '2026-11-26', value: 1000 });
    const options = charts.created[0] as { localization: { priceFormatter: (v: number) => string } };
    expect(options.localization.priceFormatter(1000.954)).toBe('1000,95');
    unmount();
    expect(charts.removed.count).toBe(1);
  });

  it('labels the axis in percent, roubles or millions', () => {
    const percent = chartData(HISTORY.points, { metric: 'unit', kalman: false, benchmark: 'LQDT' });
    const nav = chartData(HISTORY.points, { metric: 'nav', kalman: false, benchmark: null });
    expect(axisLabel(0.1234, percent, false)).toBe('0,12%');
    expect(axisLabel(100095400, nav, true)).toBe('100,1 млн');
    expect(axisLabel(1000.5, nav, false)).toBe('1000,50');
  });
});
