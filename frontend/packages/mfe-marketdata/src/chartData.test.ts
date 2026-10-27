import { describe, expect, it } from 'vitest';

import { candles, chartTime, demaLine, manualMarkers, priceLines } from './chartData';
import { series } from './test/fixtures';

// 2026-10-27 08:00 UTC = 11:00 МСК; на оси графика — московское время.
const ELEVEN = Date.UTC(2026, 9, 27, 11, 0) / 1000;

describe('chart data', () => {
  it('shifts time to Moscow', () => {
    expect(chartTime('2026-10-27T08:00:00Z')).toBe(ELEVEN);
  });

  it('draws a line per price type and source', () => {
    const duplicate = series({
      quotes: [
        ...series().quotes,
        { type: 'vwap', source: 'quik', time: '2026-10-27T08:00:00Z', value: '51.830000' },
      ],
    });

    expect(priceLines(duplicate)).toEqual([
      {
        label: 'VWAP · quik',
        points: [
          { time: ELEVEN - 600, value: 51.8 },
          // На одну отметку времени — последнее значение.
          { time: ELEVEN, value: 51.83 },
        ],
      },
      { label: 'bid · MOEX ISS', points: [{ time: ELEVEN, value: 51.75 }] },
    ]);
  });

  it('draws candles of the DEMA timeframe or the shortest one', () => {
    const ten = [{ time: ELEVEN - 3600, open: 51.7, high: 51.9, low: 51.6, close: 51.8 }];

    expect(candles(series(), 10)).toEqual(ten);
    expect(candles(series(), null)).toEqual(ten);
    expect(candles(series(), 60)).toEqual([]);
    expect(candles(series({ bars: {} }), null)).toEqual([]);
  });

  it('draws DEMA and manual prices', () => {
    expect(demaLine(series())).toEqual([{ time: ELEVEN - 3000, value: 51.8 }]);
    expect(manualMarkers(series())).toEqual([{ time: ELEVEN - 1800, text: 'ручная 51,5' }]);
  });
});
