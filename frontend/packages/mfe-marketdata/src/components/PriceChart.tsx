import {
  AreaSeries,
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  LineSeries,
  LineStyle,
  type ISeriesApi,
  type SeriesType,
  type UTCTimestamp,
} from 'lightweight-charts';
import { useEffect, useRef, useState } from 'react';

import type { PriceType, Rule, Series } from '../api';
import {
  candles,
  defaultLayers,
  demaLine,
  LAYER_LABELS,
  layers,
  manualMarkers,
  priceLine,
  type Layer,
  type Point,
} from '../chartData';

// Цвета различимы и на светлой, и на тёмной теме; VWAP — акцентом, как в интерфейсе.
const COLORS: Readonly<Record<Layer, string>> = {
  vwap: '#3b82f6',
  last: '#8b5cf6',
  bidask: '#94a3b8',
  close: '#14b8a6',
  market_price: '#f59e0b',
  admitted_quote: '#ec4899',
  dema: '#f97316',
  bars: '#22c55e',
  manual: '#d97706',
};

const asTime = (points: readonly Point[]) => points.map((p) => ({ ...p, time: p.time as UTCTimestamp }));

function token(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/** 99.7512 → 99,7512: цены на оси и у последнего значения — с запятой. */
function price(value: number): string {
  return value.toFixed(value >= 10 ? 2 : 4).replace('.', ',');
}

/**
 * График цен за две недели: VWAP с заливкой, DEMA и ручные цены сразу; bid/ask, другие
 * цены и бары — переключателями. Время — московское.
 */
export function PriceChart({ series, rule }: { readonly series: Series; readonly rule: Rule }) {
  const available = layers(series);
  const [shown, setShown] = useState<readonly Layer[]>(() => defaultLayers(available, rule));

  function toggle(layer: Layer) {
    setShown(shown.includes(layer) ? shown.filter((l) => l !== layer) : [...shown, layer]);
  }

  return (
    <>
      {available.length > 0 && (
        <div className="chart-layers" role="group" aria-label="Что показать на графике">
          {available.map((layer) => (
            <button
              key={layer}
              type="button"
              aria-pressed={shown.includes(layer)}
              onClick={() => toggle(layer)}
            >
              <span className="chart-swatch" style={{ background: COLORS[layer] }} aria-hidden="true" />
              {LAYER_LABELS[layer]}
            </button>
          ))}
        </div>
      )}
      <Canvas series={series} rule={rule} shown={shown} />
    </>
  );
}

function Canvas({
  series,
  rule,
  shown,
}: {
  readonly series: Series;
  readonly rule: Rule;
  readonly shown: readonly Layer[];
}) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Эффект запускается после монтирования: контейнер уже есть.
    const element = container.current!;
    const chart = createChart(element, {
      autoSize: true,
      layout: {
        background: { color: 'transparent' },
        textColor: token('--muted', '#5d6672'),
        fontFamily: getComputedStyle(document.body).fontFamily,
        fontSize: 12,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: token('--border', '#e2e5ea') } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.08 } },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      crosshair: { mode: CrosshairMode.Magnet },
      // Колесо листает страницу, а не график: двигают и масштабируют перетаскиванием и щипком.
      handleScroll: { mouseWheel: false, vertTouchDrag: false },
      handleScale: { mouseWheel: false },
      localization: { locale: 'ru-RU', priceFormatter: price },
    });
    let anchor: ISeriesApi<SeriesType> | null = null;
    const line = (layer: Layer, points: readonly Point[], style: LineStyle = LineStyle.Solid) => {
      const added = chart.addSeries(LineSeries, {
        color: COLORS[layer],
        lineWidth: layer === 'dema' ? 2 : 1,
        lineStyle: style,
        lastValueVisible: false,
        priceLineVisible: false,
      });
      added.setData(asTime(points));
      anchor ??= added;
    };

    if (shown.includes('bars')) {
      const bars = chart.addSeries(CandlestickSeries, {
        upColor: '#22c55e',
        downColor: '#ef4444',
        wickUpColor: '#22c55e',
        wickDownColor: '#ef4444',
        borderVisible: false,
      });
      bars.setData(candles(series, rule.dema_timeframe).map((bar) => ({ ...bar, time: bar.time as UTCTimestamp })));
      anchor = bars;
    }
    // Слои — в постоянном порядке, как бы их ни включали.
    for (const layer of layers(series).filter((l) => shown.includes(l))) {
      if (layer === 'vwap') {
        // Главная линия — с заливкой и последним значением на оси.
        const area = chart.addSeries(AreaSeries, {
          lineColor: COLORS.vwap,
          topColor: 'rgba(59, 130, 246, 0.22)',
          bottomColor: 'rgba(59, 130, 246, 0)',
          lineWidth: 2,
          priceLineStyle: LineStyle.Dotted,
        });
        area.setData(asTime(priceLine(series, 'vwap')));
        anchor = area;
      } else if (layer === 'bidask') {
        line('bidask', priceLine(series, 'bid'), LineStyle.Dashed);
        line('bidask', priceLine(series, 'ask'), LineStyle.Dashed);
      } else if (layer === 'dema') {
        line('dema', demaLine(series));
      } else if (layer !== 'bars' && layer !== 'manual') {
        line(layer, priceLine(series, layer as PriceType));
      }
    }
    const markers = manualMarkers(series);
    if (anchor && shown.includes('manual')) {
      createSeriesMarkers(
        anchor,
        markers.map((m) => ({
          time: m.time as UTCTimestamp,
          position: 'aboveBar' as const,
          shape: 'circle' as const,
          color: COLORS.manual,
          text: m.text,
        })),
      );
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [series, rule, shown]);

  return <div ref={container} className="chart" role="img" aria-label="График цен за две недели, время московское" />;
}
