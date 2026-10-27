import type { Series } from './api';
import { formatPrice, PRICE_LABELS, sourceLabel } from './format';

/**
 * Данные для графика. Lightweight Charts показывает время в UTC, поэтому
 * сдвигаем отметки на 3 часа: на оси — московское время, как у биржи.
 */
const MOSCOW_OFFSET_SECONDS = 3 * 3600;

export interface Point {
  readonly time: number;
  readonly value: number;
}

export interface Line {
  readonly label: string;
  readonly points: readonly Point[];
}

export interface Candle {
  readonly time: number;
  readonly open: number;
  readonly high: number;
  readonly low: number;
  readonly close: number;
}

export interface Marker {
  readonly time: number;
  readonly text: string;
}

export function chartTime(iso: string): number {
  return Math.floor(Date.parse(iso) / 1000) + MOSCOW_OFFSET_SECONDS;
}

/** Линии цен: одна на тип и источник; на одну отметку времени — последнее значение. */
export function priceLines(series: Series): Line[] {
  const lines = new Map<string, Map<number, number>>();
  for (const quote of series.quotes) {
    const label = `${PRICE_LABELS[quote.type]} · ${sourceLabel(quote.source)}`;
    const points = lines.get(label) ?? new Map<number, number>();
    points.set(chartTime(quote.time), Number(quote.value));
    lines.set(label, points);
  }
  return [...lines].map(([label, points]) => ({
    label,
    points: [...points]
      .sort(([a], [b]) => a - b)
      .map(([time, value]) => ({ time, value })),
  }));
}

/** Свечи по барам таймфрейма; нет таймфрейма — самого короткого из присланных. */
export function candles(series: Series, timeframe: number | null): Candle[] {
  const available = Object.keys(series.bars).map(Number).sort((a, b) => a - b);
  const chosen = timeframe !== null && available.includes(timeframe) ? timeframe : available[0];
  if (chosen === undefined) {
    return [];
  }
  return series.bars[String(chosen)]!.map((bar) => ({
    time: chartTime(bar.start),
    open: Number(bar.open),
    high: Number(bar.high),
    low: Number(bar.low),
    close: Number(bar.close),
  }));
}

export function demaLine(series: Series): Point[] {
  return series.dema.map((point) => ({ time: chartTime(point.time), value: Number(point.value) }));
}

export function manualMarkers(series: Series): Marker[] {
  return series.manual.map((price) => ({
    time: chartTime(price.entered_at),
    text: `ручная ${formatPrice(price.value)}`,
  }));
}
