import type { Decimal, FeeKind, FeeRate, NavForecast, NavPoint } from './api';
import { shiftDay } from './format';

/**
 * Раздел «СЧА» (решение 168): график стоимости пая или СЧА, сглаженная фильтром Калмана,
 * сравнение с LQDT, периоды и запомненный выбор. Здесь — только правила.
 */

export type Metric = 'unit' | 'nav';
export type Period = 'month' | 'quarter' | 'year' | 'all' | 'custom';

export const PERIOD_LABELS: Readonly<Record<Period, string>> = {
  month: '1 месяц',
  quarter: '3 месяца',
  year: 'Год',
  all: 'Весь срок',
  custom: 'Свой период',
};

export const METRIC_KEY = 'am.booking.nav.metric';
export const PERIOD_KEY = 'am.booking.nav.period';
export const CUSTOM_KEY = 'am.booking.nav.custom';
export const KALMAN_KEY = 'am.booking.nav.kalman';
export const BENCHMARK_KEY = 'am.booking.nav.benchmark';
export const TABLE_KEY = 'am.booking.nav.table';

export function parseMetric(raw: string | null): Metric {
  return raw === 'nav' ? 'nav' : 'unit';
}

export function parsePeriod(raw: string | null): Period {
  return raw === 'quarter' || raw === 'year' || raw === 'all' || raw === 'custom' ? raw : 'month';
}

/** ISO-дата на ``months`` месяцев раньше; 31-е число короткого месяца — последний день. */
export function monthsBefore(iso: string, months: number): string {
  const [year, month, day] = iso.split('-').map(Number) as [number, number, number];
  const target = new Date(Date.UTC(year, month - 1 - months, 1, 12));
  const last = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0, 12)).getUTCDate();
  target.setUTCDate(Math.min(day, last));
  return target.toISOString().slice(0, 10);
}

/** С какой и по какую дату просить историю: весь срок — без дат. */
export function periodRange(
  period: Period,
  today: string,
  custom: { readonly from: string; readonly till: string } | null,
): { readonly from: string | undefined; readonly till: string | undefined } {
  switch (period) {
    case 'month':
      return { from: shiftDay(monthsBefore(today, 1), 1), till: undefined };
    case 'quarter':
      return { from: shiftDay(monthsBefore(today, 3), 1), till: undefined };
    case 'year':
      return { from: shiftDay(monthsBefore(today, 12), 1), till: undefined };
    case 'all':
      return { from: undefined, till: undefined };
    case 'custom':
      return { from: custom?.from, till: custom?.till };
  }
}

// --- комиссии от СЧА ------------------------------------------------------------------

export const FEE_SHORT: Readonly<Record<FeeKind, string>> = {
  management: 'управление',
  specdep: 'спецдеп',
  registrar: 'регистратор',
  other: 'прочие',
};

/** Действующие на дату ставки — последняя по каждому виду, которая уже началась. */
export function activeRates(rates: readonly FeeRate[], day: string): FeeRate[] {
  const latest = new Map<FeeKind, FeeRate>();
  for (const rate of rates) {
    const found = latest.get(rate.kind);
    if (rate.valid_from <= day && (found === undefined || rate.valid_from > found.valid_from)) {
      latest.set(rate.kind, rate);
    }
  }
  return [...latest.values()].filter((rate) => Number(rate.rate) > 0);
}

// --- график ---------------------------------------------------------------------------

export type LineKey = 'unit' | 'nav' | 'smoothed' | 'forecast' | 'benchmark';

export interface ChartPoint {
  readonly time: string;
  readonly value: number;
}

export interface ChartLine {
  readonly key: LineKey;
  readonly label: string;
  readonly data: readonly ChartPoint[];
}

export interface ChartData {
  /** С LQDT — изменение в % от начала периода: у пая и LQDT разные цены. */
  readonly percent: boolean;
  readonly lines: readonly ChartLine[];
}

function series(points: readonly NavPoint[], pick: (point: NavPoint) => Decimal | null): ChartPoint[] {
  return points.flatMap((p) => {
    const value = pick(p);
    return value === null ? [] : [{ time: p.date, value: Number(value) }];
  });
}

function relative(data: readonly ChartPoint[], base: number): ChartPoint[] {
  return data.map((p) => ({ time: p.time, value: (p.value / base - 1) * 100 }));
}

/**
 * Линии графика. СЧА фонда — одна линия: в ней приток и отток пайщиков, сглаживать и
 * сравнивать её не с чем. Стоимость пая — с Калманом и прогнозом на завтра, с LQDT —
 * всё в % от первого дня периода.
 */
export function chartData(
  points: readonly NavPoint[],
  forecast: NavForecast | null,
  options: { readonly metric: Metric; readonly kalman: boolean; readonly benchmark: string | null },
): ChartData {
  if (options.metric === 'nav') {
    return { percent: false, lines: [{ key: 'nav', label: 'СЧА фонда', data: series(points, (p) => p.nav) }] };
  }
  const unit = series(points, (p) => p.unit_price);
  const smoothed = series(points, (p) => p.smoothed);
  const ahead: ChartPoint[] =
    options.kalman && forecast && smoothed.length > 0 && points.at(-1)?.date === smoothed.at(-1)!.time
      ? [smoothed.at(-1)!, { time: forecast.date, value: Number(forecast.value) }]
      : [];
  const lines: ChartLine[] = [
    { key: 'unit', label: 'Стоимость пая', data: unit },
    ...(options.kalman ? [{ key: 'smoothed' as const, label: 'Калман', data: smoothed }] : []),
    ...(ahead.length > 0 ? [{ key: 'forecast' as const, label: 'Прогноз', data: ahead }] : []),
  ];
  const benchmark = options.benchmark === null ? [] : series(points, (p) => p.benchmark);
  if (benchmark.length === 0 || unit.length === 0) {
    return { percent: false, lines };
  }
  const base = unit[0]!.value;
  return {
    percent: true,
    lines: [
      ...lines.map((line) => ({ ...line, data: relative(line.data, base) })),
      { key: 'benchmark', label: options.benchmark!, data: relative(benchmark, benchmark[0]!.value) },
    ],
  };
}

/** Изменение за период: в % и в % годовых (простые); меньше двух точек — null. */
export function periodChange(
  points: readonly NavPoint[],
  pick: (point: NavPoint) => Decimal | null,
): { readonly percent: number; readonly annual: number } | null {
  const found = series(points, pick);
  if (found.length < 2) {
    return null;
  }
  const first = found[0]!;
  const last = found.at(-1)!;
  const percent = (last.value / first.value - 1) * 100;
  const days = (Date.parse(last.time) - Date.parse(first.time)) / 86_400_000;
  return { percent, annual: (percent * 365) / days };
}
