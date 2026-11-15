import { describe, expect, it } from 'vitest';

import type { Rule } from './api';
import { candles, chartTime, defaultLayers, demaLine, layers, manualMarkers, priceLine } from './chartData';
import { series } from './test/fixtures';

// 2026-10-27 08:00 UTC = 11:00 МСК; на оси графика — московское время.
const ELEVEN = Date.UTC(2026, 9, 27, 11, 0) / 1000;

const RULE: Rule = { price_types: ['vwap'], dema_timeframe: null, dema_half_life: null, spread_threshold_bp: 50 };

describe('chart data', () => {
  it('shifts time to Moscow', () => {
    expect(chartTime('2026-10-27T08:00:00Z')).toBe(ELEVEN);
  });

  it('draws one line per price type across sources, the last value at a moment', () => {
    const both = series({
      quotes: [
        { type: 'vwap', source: 'quik', time: '2026-10-27T08:00:00Z', value: '51.820000' },
        { type: 'vwap', source: 'iss', time: '2026-10-27T07:50:00Z', value: '51.800000' },
        { type: 'vwap', source: 'iss', time: '2026-10-27T08:00:00Z', value: '51.830000' },
        { type: 'bid', source: 'iss', time: '2026-10-27T08:00:00Z', value: '51.750000' },
      ],
    });

    expect(priceLine(both, 'vwap')).toEqual([
      { time: ELEVEN - 600, value: 51.8 },
      { time: ELEVEN, value: 51.83 },
    ]);
    expect(priceLine(both, 'last')).toEqual([]);
  });

  it('lists the layers that have data, in a fixed order', () => {
    expect(layers(series())).toEqual(['vwap', 'bidask', 'dema', 'bars', 'manual']);
    const others = series({
      quotes: (['admitted_quote', 'market_price', 'close', 'last', 'ask'] as const).map((type) => ({
        type,
        source: 'iss',
        time: '2026-10-27T08:00:00Z',
        value: '51.8',
      })),
      bars: { '10': [] },
      dema: [],
      manual: [],
    });
    expect(layers(others)).toEqual(['last', 'bidask', 'close', 'market_price', 'admitted_quote']);
  });

  it('shows VWAP, DEMA and manual prices at first', () => {
    expect(defaultLayers(['vwap', 'last', 'bidask', 'dema', 'bars', 'manual'], RULE)).toEqual([
      'vwap',
      'dema',
      'manual',
    ]);
    // Нет VWAP — первый тип из правила оценки, у которого есть данные.
    expect(defaultLayers(['last', 'close'], { ...RULE, price_types: ['vwap', 'close'] })).toEqual(['close']);
    // Ни одного типа из правила — первая линия.
    expect(defaultLayers(['last', 'close', 'dema'], RULE)).toEqual(['last', 'dema']);
    expect(defaultLayers(['bars', 'manual'], RULE)).toEqual(['manual']);
  });

  it('takes candles of the DEMA timeframe or the shortest one with bars', () => {
    expect(candles(series(), 10)).toEqual([{ time: ELEVEN - 3600, open: 51.7, high: 51.9, low: 51.6, close: 51.8 }]);
    expect(candles(series(), 60)).toHaveLength(1);
    expect(candles(series({ bars: {} }), null)).toEqual([]);
    const [bar] = series().bars['10']!;
    const both = series({ bars: { '60': [{ ...bar!, close: '52' }], '10': [bar!] } });
    expect(candles(both, null).map((c) => c.close)).toEqual([51.8]);
  });

  it('turns DEMA and manual prices into points and markers', () => {
    expect(demaLine(series())).toEqual([{ time: ELEVEN - 3000, value: 51.8 }]);
    expect(manualMarkers(series())).toEqual([{ time: ELEVEN - 1800, text: 'ручная 51,5' }]);
  });
});
