import type { PriceType, Rule, Series } from './api';
import { formatPrice, PRICE_LABELS } from './format';

/**
 * Данные для графика. Lightweight Charts показывает время в UTC, поэтому
 * сдвигаем отметки на 3 часа: на оси — московское время, как у биржи.
 */
const MOSCOW_OFFSET_SECONDS = 3 * 3600;

export interface Point {
  readonly time: number;
  readonly value: number;
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

/** Что можно показать на графике: линия типа цены, bid и ask вместе, бары, DEMA, ручные цены. */
export type Layer = Exclude<PriceType, 'bid' | 'ask' | 'dema'> | 'bidask' | 'bars' | 'dema' | 'manual';

const LAYER_ORDER: readonly Layer[] = [
  'vwap',
  'last',
  'bidask',
  'close',
  'market_price',
  'admitted_quote',
  'dema',
  'bars',
  'manual',
];

export const LAYER_LABELS: Readonly<Record<Layer, string>> = {
  vwap: PRICE_LABELS.vwap,
  last: 'Последняя',
  bidask: 'Bid / Ask',
  close: 'Закрытие',
  market_price: 'Рыночная',
  admitted_quote: 'Признаваемая',
  dema: 'DEMA',
  bars: 'Бары',
  manual: 'Ручные',
};

export function chartTime(iso: string): number {
  return Math.floor(Date.parse(iso) / 1000) + MOSCOW_OFFSET_SECONDS;
}

/** Линия типа цены по всем источникам; на одну отметку времени — последнее значение. */
export function priceLine(series: Series, type: PriceType): Point[] {
  const points = new Map<number, number>();
  for (const quote of series.quotes) {
    if (quote.type === type) {
      points.set(chartTime(quote.time), Number(quote.value));
    }
  }
  return [...points].sort(([a], [b]) => a - b).map(([time, value]) => ({ time, value }));
}

/** Слои, для которых есть данные, — в постоянном порядке. */
export function layers(series: Series): Layer[] {
  const types = new Set(series.quotes.map((q) => q.type));
  const present: Record<Layer, boolean> = {
    vwap: types.has('vwap'),
    last: types.has('last'),
    bidask: types.has('bid') || types.has('ask'),
    close: types.has('close'),
    market_price: types.has('market_price'),
    admitted_quote: types.has('admitted_quote'),
    dema: series.dema.length > 0,
    bars: Object.values(series.bars).some((bars) => bars.length > 0),
    manual: series.manual.length > 0,
  };
  return LAYER_ORDER.filter((layer) => present[layer]);
}

/**
 * Что показать сразу: VWAP (нет его — первый тип из правила оценки, у которого есть
 * данные, иначе первая линия), DEMA и ручные цены. Остальное включают переключателями.
 */
export function defaultLayers(available: readonly Layer[], rule: Rule): Layer[] {
  const lines = available.filter((l) => l !== 'dema' && l !== 'bars' && l !== 'manual');
  const fromRule = rule.price_types.map(String).find((t) => lines.some((l) => l === t)) as Layer | undefined;
  const main = lines.includes('vwap') ? 'vwap' : (fromRule ?? lines[0]);
  return available.filter((l) => l === main || l === 'dema' || l === 'manual');
}

/** Свечи по барам таймфрейма; нет таймфрейма — самого короткого из присланных. */
export function candles(series: Series, timeframe: number | null): Candle[] {
  const available = Object.keys(series.bars)
    .map(Number)
    .filter((t) => series.bars[String(t)]!.length > 0)
    .sort((a, b) => a - b);
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
