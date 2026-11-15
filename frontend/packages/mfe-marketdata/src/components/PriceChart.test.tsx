import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Rule } from '../api';
import { series } from '../test/fixtures';
import { PriceChart } from './PriceChart';

// Canvas в jsdom не рисуется: проверяем, какие данные и настройки получает библиотека графиков.
const charts = vi.hoisted(() => {
  const added: { kind: string; options: Record<string, unknown>; data: unknown[] }[] = [];
  const markers: unknown[][] = [];
  const created: Record<string, unknown>[] = [];
  const removed = { count: 0 };
  return { added, markers, created, removed };
});

vi.mock('lightweight-charts', () => ({
  AreaSeries: 'area',
  LineSeries: 'line',
  CandlestickSeries: 'candles',
  CrosshairMode: { Magnet: 1 },
  LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 },
  createChart: (_element: unknown, options: Record<string, unknown>) => {
    charts.created.push(options);
    return {
      addSeries: (kind: string, seriesOptions: Record<string, unknown>) => {
        const entry = { kind, options: seriesOptions, data: [] as unknown[] };
        charts.added.push(entry);
        return { setData: (data: unknown[]) => entry.data.push(...data) };
      },
      timeScale: () => ({ fitContent: () => undefined }),
      remove: () => {
        charts.removed.count += 1;
      },
    };
  },
  createSeriesMarkers: (_series: unknown, markers: unknown[]) => charts.markers.push(markers),
}));

const RULE: Rule = { price_types: ['vwap'], dema_timeframe: 10, dema_half_life: '60', spread_threshold_bp: 50 };

beforeEach(() => {
  charts.added.length = 0;
  charts.markers.length = 0;
  charts.created.length = 0;
  charts.removed.count = 0;
});

afterEach(() => {
  document.documentElement.style.removeProperty('--muted');
});

/** Что нарисовано последним графиком: вид и число точек каждой серии. */
function drawn() {
  return charts.added.map((s) => [s.kind, s.data.length]);
}

describe('PriceChart', () => {
  it('starts with VWAP as an area, DEMA and manual prices; the wheel scrolls the page', () => {
    const { unmount } = render(<PriceChart series={series()} rule={RULE} />);

    expect(drawn()).toEqual([
      ['area', 2],
      ['line', 1],
    ]);
    expect(charts.markers).toEqual([[expect.objectContaining({ text: 'ручная 51,5', shape: 'circle' })]]);
    expect(
      screen.getAllByRole('button').map((b) => [b.textContent, b.getAttribute('aria-pressed')]),
    ).toEqual([
      ['VWAP', 'true'],
      ['Bid / Ask', 'false'],
      ['DEMA', 'true'],
      ['Бары', 'false'],
      ['Ручные', 'true'],
    ]);
    const options = charts.created[0] as {
      handleScroll: unknown;
      handleScale: unknown;
      localization: { priceFormatter: (v: number) => string };
      layout: { textColor: string };
    };
    expect(options.handleScroll).toEqual({ mouseWheel: false, vertTouchDrag: false });
    expect(options.handleScale).toEqual({ mouseWheel: false });
    expect([options.localization.priceFormatter(99.75), options.localization.priceFormatter(1.5)]).toEqual([
      '99,75',
      '1,5000',
    ]);
    expect(options.layout.textColor).toBe('#5d6672');

    unmount();
    expect(charts.removed.count).toBe(1);
  });

  it('adds bid and ask, bars and other prices by the switches', async () => {
    const withLast = series({
      quotes: [...series().quotes, { type: 'last', source: 'iss', time: '2026-10-27T08:00:00Z', value: '51.8' }],
    });
    render(<PriceChart series={withLast} rule={RULE} />);

    await userEvent.click(screen.getByRole('button', { name: 'Bid / Ask' }));
    await userEvent.click(screen.getByRole('button', { name: 'Бары' }));
    await userEvent.click(screen.getByRole('button', { name: 'Последняя' }));
    await userEvent.click(screen.getByRole('button', { name: 'VWAP' }));

    // Бары — снизу, дальше линии в постоянном порядке: последняя, bid и ask пунктиром, DEMA.
    expect(drawn().slice(-5)).toEqual([
      ['candles', 1],
      ['line', 1],
      ['line', 1],
      ['line', 0],
      ['line', 1],
    ]);
    expect(charts.added.slice(-4).map((s) => s.options.lineStyle)).toEqual([0, 2, 2, 0]);
    expect(screen.getByRole('button', { name: 'VWAP' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('takes the colours of the theme', () => {
    document.documentElement.style.setProperty('--muted', '#98a1ad');
    render(<PriceChart series={series()} rule={RULE} />);

    expect((charts.created[0] as { layout: { textColor: string } }).layout.textColor).toBe('#98a1ad');
  });

  it('puts manual prices nowhere when no line is shown', () => {
    render(<PriceChart series={series({ quotes: [], dema: [], bars: {} })} rule={RULE} />);

    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['Ручные']);
    expect(charts.added).toEqual([]);
    expect(charts.markers).toEqual([]);
  });

  it('draws only the frame without data', () => {
    render(<PriceChart series={series({ quotes: [], dema: [], bars: {}, manual: [] })} rule={RULE} />);

    expect(screen.queryByRole('group')).not.toBeInTheDocument();
    expect(charts.added).toEqual([]);
  });
});
