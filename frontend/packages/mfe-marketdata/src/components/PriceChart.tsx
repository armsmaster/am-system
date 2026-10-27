import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  LineSeries,
  type ISeriesApi,
  type SeriesType,
  type UTCTimestamp,
} from 'lightweight-charts';
import { useEffect, useRef } from 'react';

import type { Series } from '../api';
import { candles, demaLine, manualMarkers, priceLines } from '../chartData';

// Цвета линий различимы и на светлой, и на тёмной теме.
const COLORS = ['#3b82f6', '#ef4444', '#22c55e', '#a855f7', '#f59e0b', '#14b8a6', '#ec4899'];
const DEMA_COLOR = '#0ea5e9';

/** График цен бумаги: линии всех цен, свечи по барам, DEMA, ручные цены. */
export function PriceChart({
  series,
  timeframe,
}: {
  readonly series: Series;
  readonly timeframe: number | null;
}) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Эффект запускается после монтирования: контейнер уже есть.
    const element = container.current!;
    const text = getComputedStyle(document.documentElement).getPropertyValue('--text') || '#1c2024';
    const chart = createChart(element, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: text },
      timeScale: { timeVisible: true },
    });
    let anchor: ISeriesApi<SeriesType> | null = null;

    const bars = candles(series, timeframe);
    if (bars.length) {
      anchor = chart.addSeries(CandlestickSeries, { title: 'бары' });
      anchor.setData(bars.map((bar) => ({ ...bar, time: bar.time as UTCTimestamp })));
    }
    priceLines(series).forEach((line, index) => {
      const added = chart.addSeries(LineSeries, {
        color: COLORS[index % COLORS.length]!,
        lineWidth: 1,
        title: line.label,
      });
      added.setData(line.points.map((p) => ({ ...p, time: p.time as UTCTimestamp })));
      anchor ??= added;
    });
    const smoothed = demaLine(series);
    if (smoothed.length) {
      const added = chart.addSeries(LineSeries, { color: DEMA_COLOR, lineWidth: 2, title: 'DEMA' });
      added.setData(smoothed.map((p) => ({ ...p, time: p.time as UTCTimestamp })));
      anchor ??= added;
    }
    const markers = manualMarkers(series);
    if (anchor && markers.length) {
      createSeriesMarkers(
        anchor,
        markers.map((m) => ({
          time: m.time as UTCTimestamp,
          position: 'aboveBar' as const,
          shape: 'circle' as const,
          color: '#f97316',
          text: m.text,
        })),
      );
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [series, timeframe]);

  return <div ref={container} className="chart" role="img" aria-label="График цен, время московское" />;
}
