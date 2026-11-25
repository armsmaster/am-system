import { createChart, CrosshairMode, LineSeries, LineStyle, type Time } from 'lightweight-charts';
import { useEffect, useRef } from 'react';

import type { ChartData, LineKey } from '../nav';

// Цвета различимы и на светлой, и на тёмной теме: пай — акцентный синий, Калман —
// фиолетовый пунктиром (прогноз — точками), LQDT — бирюзовый.
const STYLES: Readonly<Record<LineKey, { readonly color: string; readonly style: number; readonly width: 1 | 2 }>> = {
  unit: { color: '#2a78d6', style: LineStyle.Solid, width: 2 },
  nav: { color: '#2a78d6', style: LineStyle.Solid, width: 2 },
  smoothed: { color: '#7c3aed', style: LineStyle.Dashed, width: 2 },
  forecast: { color: '#7c3aed', style: LineStyle.Dotted, width: 2 },
  benchmark: { color: '#1baf7a', style: LineStyle.Solid, width: 2 },
};

function token(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/** Подписи оси: проценты, стоимость пая до копейки, СЧА — в миллионах. */
export function axisLabel(value: number, data: ChartData, nav: boolean): string {
  if (data.percent) {
    return `${value.toFixed(2).replace('.', ',')}%`;
  }
  if (nav) {
    return `${(value / 1_000_000).toFixed(1).replace('.', ',')} млн`;
  }
  return value.toFixed(2).replace('.', ',');
}

/** График стоимости пая или СЧА по дням (решение 168); легенда — над ним. */
export function NavChart({ data, title }: { readonly data: ChartData; readonly title: string }) {
  const host = useRef<HTMLDivElement>(null);
  const nav = data.lines.some((line) => line.key === 'nav');

  useEffect(() => {
    const chart = createChart(host.current!, {
      autoSize: true,
      height: 280,
      layout: { background: { color: 'transparent' }, textColor: token('--muted', '#5d6672'), attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { color: token('--border', '#e2e5ea') } },
      crosshair: { mode: CrosshairMode.Magnet },
      rightPriceScale: { borderVisible: false },
      // График подстраивается под ширину уже после fitContent: без этих настроек линия
      // занимала бы только часть ширины, а слева оставалось бы пустое место.
      timeScale: { borderVisible: false, fixLeftEdge: true, fixRightEdge: true, lockVisibleTimeRangeOnResize: true },
      localization: { locale: 'ru-RU', priceFormatter: (value: number) => axisLabel(value, data, nav) },
      handleScroll: { mouseWheel: false },
      handleScale: { mouseWheel: false },
    });
    for (const line of data.lines) {
      const style = STYLES[line.key];
      chart
        .addSeries(LineSeries, {
          color: style.color,
          lineStyle: style.style,
          lineWidth: style.width,
          priceLineVisible: false,
          lastValueVisible: line.key !== 'forecast',
          title: line.label,
        })
        .setData(line.data.map((p) => ({ time: p.time as Time, value: p.value })));
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [data, nav]);

  return (
    <div className="nav-chart">
      <div className="nav-chart-head">
        <h3>{title}</h3>
        <ul className="nav-legend" aria-label="Линии графика">
          {data.lines.map((line) => (
            <li key={line.key}>
              <span className={`nav-swatch nav-swatch-${line.key}`} aria-hidden="true" />
              {line.label}
            </li>
          ))}
        </ul>
      </div>
      <div ref={host} className="nav-chart-plot" role="img" aria-label={title} />
    </div>
  );
}
