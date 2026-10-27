import { render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { series } from '../test/fixtures';
import { PriceChart } from './PriceChart';

// Canvas в jsdom не рисуется: проверяем, какие данные получает библиотека графиков.
const charts = vi.hoisted(() => {
  const added: { kind: string; options: Record<string, unknown>; data: unknown[] }[] = [];
  const markers: unknown[][] = [];
  const removed = { count: 0 };
  return { added, markers, removed };
});

vi.mock('lightweight-charts', () => ({
  LineSeries: 'line',
  CandlestickSeries: 'candles',
  createChart: () => ({
    addSeries: (kind: string, options: Record<string, unknown>) => {
      const entry = { kind, options, data: [] as unknown[] };
      charts.added.push(entry);
      return { setData: (data: unknown[]) => entry.data.push(...data) };
    },
    timeScale: () => ({ fitContent: () => undefined }),
    remove: () => {
      charts.removed.count += 1;
    },
  }),
  createSeriesMarkers: (_series: unknown, markers: unknown[]) => charts.markers.push(markers),
}));

beforeEach(() => {
  charts.added.length = 0;
  charts.markers.length = 0;
  charts.removed.count = 0;
});

describe('PriceChart', () => {
  it('draws candles, price lines, DEMA and manual prices', () => {
    const { unmount } = render(<PriceChart series={series()} timeframe={10} />);

    expect(charts.added.map((s) => [s.kind, s.options.title, s.data.length])).toEqual([
      ['candles', 'бары', 1],
      ['line', 'VWAP · quik', 2],
      ['line', 'bid · MOEX ISS', 1],
      ['line', 'DEMA', 1],
    ]);
    expect(charts.markers).toEqual([
      [expect.objectContaining({ text: 'ручная 51,5', shape: 'circle' })],
    ]);

    unmount();
    expect(charts.removed.count).toBe(1);
  });

  it('anchors manual prices to a price line when there are no bars', () => {
    render(<PriceChart series={series({ bars: {}, dema: [] })} timeframe={null} />);

    expect(charts.added.map((s) => s.kind)).toEqual(['line', 'line']);
    expect(charts.markers).toHaveLength(1);
  });

  it('anchors to DEMA when it is the only line', () => {
    render(<PriceChart series={series({ bars: {}, quotes: [] })} timeframe={null} />);

    expect(charts.added.map((s) => s.options.title)).toEqual(['DEMA']);
    expect(charts.markers).toHaveLength(1);
  });

  it('draws nothing but the frame without data', () => {
    render(<PriceChart series={series({ bars: {}, quotes: [], dema: [] })} timeframe={null} />);

    expect(charts.added).toEqual([]);
    expect(charts.markers).toEqual([]);
  });
});
