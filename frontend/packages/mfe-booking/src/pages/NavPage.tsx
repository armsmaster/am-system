import { Drawer, ErrorNote, FilterChips, LoadedView, useAction, useLoad, type Choice } from '@am/ui';
import { useCallback, useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router';

import type { Context } from '../App';
import type { FeeKind, FeeRate, NavHistory, NavPoint } from '../api';
import { FilterPanel } from '../components/FilterPanel';
import { NavChart } from '../components/NavChart';
import { saveFile } from '../download';
import {
  decimalInput,
  FEE_LABELS,
  formatDate,
  formatMoney,
  formatPrice,
  formatTime,
  moscowToday,
  shortMoney,
} from '../format';
import {
  activeRates,
  BENCHMARK_KEY,
  chartData,
  CUSTOM_KEY,
  FEE_SHORT,
  FILTER_KEY,
  formatFilter,
  KALMAN_KEY,
  METRIC_KEY,
  parseFilter,
  parseMetric,
  parsePeriod,
  PERIOD_KEY,
  PERIOD_LABELS,
  periodChange,
  periodRange,
  TABLE_KEY,
  type Metric,
  type Period,
} from '../nav';
import { fixed } from '../positions';
import { parseCustom, parseFlag, previousMonth, type CustomPeriod } from '../results';
import { useStored } from '../storage';

const FEE_KINDS = Object.keys(FEE_LABELS) as FeeKind[];

const METRICS: readonly Choice<Metric>[] = [
  { value: 'unit', label: 'Стоимость пая' },
  { value: 'nav', label: 'СЧА фонда' },
];

const PERIODS: readonly Choice<Period>[] = (Object.keys(PERIOD_LABELS) as Period[]).map((value) => ({
  value,
  label: PERIOD_LABELS[value],
}));

interface Prefs {
  readonly metric: Metric;
  readonly kalman: boolean;
  readonly benchmark: boolean;
  readonly table: boolean;
}

/**
 * СЧА фонда и стоимость пая (решение 168): наша оценка, не официальная (решение 40).
 * Сверху — стоимость пая сегодня и прогноз на завтра по фильтру Калмана; ниже — график
 * за период, сглаженная, сравнение с LQDT и таблица с выгрузкой в Excel.
 */
export function NavPage({ context }: { readonly context: Context }) {
  const funds = context.portfolios.filter((p) => p.is_fund);
  const [params, setParams] = useSearchParams();
  const fundId = params.get('fund') ?? funds[0]?.id ?? '';
  const today = moscowToday();
  const [metric, setMetric] = useStored(METRIC_KEY, parseMetric);
  const [period, setPeriod] = useStored(PERIOD_KEY, parsePeriod);
  const [custom, setCustom] = useStored<CustomPeriod | null>(CUSTOM_KEY, parseCustom, (value) => JSON.stringify(value));
  const [kalman, setKalman] = useStored(KALMAN_KEY, parseFlag);
  const [benchmark, setBenchmark] = useStored(BENCHMARK_KEY, parseFlag);
  const [table, setTable] = useStored(TABLE_KEY, parseFlag);
  const [filter, setFilter] = useStored(FILTER_KEY, parseFilter, formatFilter);
  const [feesOpen, setFeesOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const { from, till } = periodRange(period, today, custom);
  const loadHistory = useCallback(
    () => context.api.navHistory(fundId, from, till, filter),
    [context.api, fundId, from, till, filter],
  );
  const loadRates = useCallback(() => context.api.feeRates(fundId), [context.api, fundId]);
  const [history] = useLoad(loadHistory);
  const [rates, reloadRates] = useLoad(loadRates);
  const exporting = useAction(() => undefined);

  if (funds.length === 0) {
    return <p className="muted">Нет доступных фондов: доступ выдаёт администратор.</p>;
  }
  const choosePeriod = (next: Period) => {
    setPeriod(next);
    if (next === 'custom' && custom === null) {
      setCustom(previousMonth(today));
    }
  };
  const exportTable = (found: NavHistory) =>
    void exporting.run(async () =>
      saveFile(await context.api.navWorkbook(fundId, from, till, filter), `СЧА ${found.code} ${found.today}.xlsx`),
    );
  const toggles: readonly (readonly [string, boolean, (value: boolean) => void, boolean])[] = [
    ['Калман', kalman, setKalman, metric === 'unit'],
    ['LQDT', benchmark, setBenchmark, metric === 'unit'],
    ['Таблица', table, setTable, true],
  ];

  return (
    <>
      <div className="toolbar">
        <select aria-label="Фонд" value={fundId} onChange={(e) => setParams({ fund: e.target.value })}>
          {funds.map((p) => (
            <option key={p.id} value={p.id}>
              {p.code} — {p.name}
            </option>
          ))}
        </select>
        <span className="subline">наша оценка, не официальная СЧА</span>
      </div>
      <LoadedView state={history}>
        {(found) => (
          <>
            {found.waiting_since && (
              <p className="notice notice-warning">
                Фонд пересчитывается с {formatDate(found.waiting_since)} — пока показан прежний расчёт.
              </p>
            )}
            <Hero history={found} />
            <dl className="tiles nav-cards" aria-label="Комиссии и сравнение">
              <FeesCard rates={rates.status === 'ready' ? rates.data : []} today={today} onOpen={() => setFeesOpen(true)} />
              {metric === 'unit' && kalman && <KalmanCard history={found} onOpen={() => setFilterOpen(true)} />}
              {metric === 'unit' && benchmark && <BenchmarkCard history={found} label={PERIOD_LABELS[period]} />}
            </dl>
            <div className="toolbar nav-toolbar">
              <FilterChips label="Показатель" choices={METRICS} value={metric} onChange={setMetric} />
              <FilterChips label="Период" choices={PERIODS} value={period} onChange={choosePeriod} />
              {period === 'custom' && custom && (
                <span className="period-dates">
                  <input
                    type="date"
                    aria-label="Свой период с"
                    value={custom.from}
                    max={custom.till}
                    onChange={(e) => e.target.value && setCustom({ ...custom, from: e.target.value })}
                  />
                  —
                  <input
                    type="date"
                    aria-label="Свой период по"
                    value={custom.till}
                    min={custom.from}
                    onChange={(e) => e.target.value && setCustom({ ...custom, till: e.target.value })}
                  />
                </span>
              )}
              <span className="toolbar-end">
                <div className="toggle-row" role="group" aria-label="Слои графика">
                  {toggles
                    .filter(([, , , shown]) => shown)
                    .map(([label, on, set]) => (
                      <button key={label} type="button" className="toggle-pill" aria-pressed={on} onClick={() => set(!on)}>
                        {label}
                      </button>
                    ))}
                </div>
              </span>
            </div>
            {found.points.length === 0 ? (
              <p className="muted">Расчёта за период нет.</p>
            ) : (
              <>
                <NavChart
                  data={chartData(found.points, {
                    metric,
                    kalman,
                    benchmark: benchmark ? found.benchmark : null,
                  })}
                  title={metric === 'nav' ? 'СЧА фонда, ₽' : 'Стоимость пая'}
                />
                {metric === 'unit' && benchmark && found.benchmark === null && (
                  <p className="muted">Бумаги для сравнения нет в справочнике — сравнить не с чем.</p>
                )}
                {table && (
                  <NavTable
                    history={found}
                    busy={exporting.busy}
                    error={exporting.error}
                    onExport={() => exportTable(found)}
                  />
                )}
              </>
            )}
            <Drawer title="Настройки фильтра Калмана" open={filterOpen} onClose={() => setFilterOpen(false)}>
              <FilterPanel filter={found.filter} onChange={setFilter} />
            </Drawer>
          </>
        )}
      </LoadedView>
      <Drawer title="Комиссии от СЧА" open={feesOpen} onClose={() => setFeesOpen(false)} wide>
        <LoadedView state={rates}>{(found) => <RatesTable rates={found} />}</LoadedView>
        {context.isOperations && <RateForm context={context} fundId={fundId} onSaved={reloadRates} />}
      </Drawer>
    </>
  );
}

function lastPriced(points: readonly NavPoint[]): [NavPoint | undefined, NavPoint | undefined] {
  const priced = points.filter((p) => p.unit_price !== null);
  return [priced.at(-1), priced.at(-2)];
}

/** Самое важное — крупно: стоимость пая сейчас и прогноз на завтра. */
function Hero({ history }: { readonly history: NavHistory }) {
  const [last, previous] = lastPriced(history.points);
  const { forecast } = history;
  const change = last && previous ? dayChange(last, previous) : null;
  return (
    <div className="nav-hero">
      <section className="nav-now" aria-label="Стоимость пая сейчас">
        <span className="nav-hero-label">Стоимость пая · {last ? formatDate(last.date) : '—'}</span>
        <span className="nav-hero-value">{last ? `${formatMoney(last.unit_price)} ₽` : '—'}</span>
        {last && (
          <span className="nav-hero-note">
            {change && `${change.text} ₽ за день · `}
            СЧА {shortMoney(last.nav)} · {formatPrice(last.units)} паёв
          </span>
        )}
      </section>
      <section className="nav-forecast" aria-label="Прогноз на следующий торговый день">
        <span className="nav-hero-label">
          Прогноз на {forecast ? formatDate(forecast.date) : 'следующий торговый день'} · фильтр Калмана
          {!history.filter.standard && ', свои настройки'}
        </span>
        <span className="nav-hero-value">{forecast ? `${formatMoney(forecast.value)} ₽` : '—'}</span>
        {forecast && (
          <span className="nav-hero-note">
            {forecast.low && forecast.high && `68%: ${formatMoney(forecast.low)} – ${formatMoney(forecast.high)} · `}
            тренд {fixed(forecast.trend, 4)} ₽ за день начисления до комиссий · ≈{' '}
            {fixed(forecast.trend_percent, 2)}% годовых после комиссий
          </span>
        )}
      </section>
    </div>
  );
}

function FeesCard({
  rates,
  today,
  onOpen,
}: {
  readonly rates: readonly FeeRate[];
  readonly today: string;
  readonly onOpen: () => void;
}) {
  const active = activeRates(rates, today);
  const total = active.reduce((sum, rate) => sum + Number(rate.rate), 0);
  return (
    <div className="tile nav-card">
      <dt>Комиссии от СЧА</dt>
      <dd>
        {active.length === 0 ? 'не начисляются' : `${fixed(String(total), 2)}% годовых`}
        {active.length > 0 && (
          <div className="subline">
            {active.map((rate) => `${FEE_SHORT[rate.kind]} ${formatPrice(rate.rate)}`).join(' · ')}
          </div>
        )}
      </dd>
      <button type="button" className="icon-button nav-card-gear" aria-label="Ставки комиссий" onClick={onOpen}>
        ⚙
      </button>
    </div>
  );
}

function KalmanCard({ history, onOpen }: { readonly history: NavHistory; readonly onOpen: () => void }) {
  const last = history.points.filter((p) => p.smoothed !== null).at(-1);
  const { filter } = history;
  return (
    <div className="tile nav-card">
      <dt>Сглаженная · Калман</dt>
      <dd>
        {last ? `${formatMoney(last.smoothed)} ₽` : '—'}
        {history.forecast && (
          <div className="subline">≈ {fixed(history.forecast.trend_percent, 2)}% годовых после комиссий</div>
        )}
        {!filter.standard && (
          <div className="subline nav-own-filter">
            свои настройки: {fixed(filter.half_life, 1)} и {fixed(filter.trend_memory, 0)} дн.
          </div>
        )}
      </dd>
      <button type="button" className="icon-button nav-card-gear" aria-label="Настройки фильтра" onClick={onOpen}>
        ⚙
      </button>
    </div>
  );
}

function BenchmarkCard({ history, label }: { readonly history: NavHistory; readonly label: string }) {
  if (history.benchmark === null) {
    return (
      <div className="tile nav-card">
        <dt>Сравнение</dt>
        <dd className="muted">бумаги нет в справочнике</dd>
      </div>
    );
  }
  const change = periodChange(history.points, (p) => p.benchmark);
  const last = history.points.filter((p) => p.benchmark !== null).at(-1);
  return (
    <div className="tile nav-card">
      <dt>
        {history.benchmark} · {label.toLowerCase()}
      </dt>
      <dd>
        {change === null ? (
          '—'
        ) : (
          <span className={change.percent > 0 ? 'up' : change.percent < 0 ? 'down' : undefined}>
            {change.percent > 0 ? '+' : ''}
            {fixed(String(change.percent), 2)}%
          </span>
        )}
        <div className="subline">
          {last ? `${formatPrice(last.benchmark)} ₽` : 'цен нет'}
          {change && ` · ≈ ${fixed(String(change.annual), 2)}% годовых`}
        </div>
      </dd>
    </div>
  );
}

/** Дни периода — новые сверху; выгрузка — тех же дней в Excel. */
function NavTable({
  history,
  busy,
  error,
  onExport,
}: {
  readonly history: NavHistory;
  readonly busy: boolean;
  readonly error: string | null;
  readonly onExport: () => void;
}) {
  const days = [...history.points].reverse();
  return (
    <section className="nav-table">
      <div className="nav-table-head">
        <h3>По дням</h3>
        <button type="button" onClick={onExport} disabled={busy}>
          Экспортировать в Excel
        </button>
      </div>
      <ErrorNote message={error} />
      <div className="positions-scroll">
        <table className="data" aria-label="СЧА по дням">
          <thead>
            <tr>
              <th>Дата</th>
              <th className="num">Стоимость пая, ₽</th>
              <th className="num">Сглаженная, ₽</th>
              <th className="num">За день, ₽</th>
              <th className="num">СЧА, ₽</th>
              <th className="num">Паёв</th>
              {history.benchmark && <th className="num">{history.benchmark}, ₽</th>}
            </tr>
          </thead>
          <tbody>
            {days.map((d, index) => {
              const before = days[index + 1];
              const change = before === undefined ? null : dayChange(d, before);
              return (
                <tr key={d.date}>
                  <td>{formatDate(d.date)}</td>
                  <td className="num">{formatMoney(d.unit_price)}</td>
                  <td className="num">{formatMoney(d.smoothed)}</td>
                  <td className="num">
                    {change === null ? '—' : <span className={change.tone || undefined}>{change.text}</span>}
                  </td>
                  <td className="num">{formatMoney(d.nav)}</td>
                  <td className="num">{formatPrice(d.units)}</td>
                  {history.benchmark && <td className="num">{formatPrice(d.benchmark)}</td>}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** Изменение стоимости пая к прошлому дню — до копейки, со знаком; нет цены — null. */
function dayChange(day: NavPoint, before: NavPoint): { readonly text: string; readonly tone: string } | null {
  if (day.unit_price === null || before.unit_price === null) {
    return null;
  }
  const cents = Math.round((Number(day.unit_price) - Number(before.unit_price)) * 100);
  const text = formatMoney((Math.abs(cents) / 100).toFixed(2));
  if (cents > 0) {
    return { text: `+${text}`, tone: 'up' };
  }
  return cents < 0 ? { text: `−${text}`, tone: 'down' } : { text, tone: '' };
}

function RatesTable({ rates }: { readonly rates: readonly FeeRate[] }) {
  if (rates.length === 0) {
    return <p className="muted">Ставок нет — комиссии от СЧА не начисляются.</p>;
  }
  return (
    <table className="data" aria-label="Ставки комиссий">
      <thead>
        <tr>
          <th>Комиссия</th>
          <th className="num">Ставка, % годовых</th>
          <th>Действует с</th>
          <th>Комментарий</th>
          <th>Кто ввёл</th>
        </tr>
      </thead>
      <tbody>
        {rates.map((r) => (
          <tr key={`${r.kind}-${r.valid_from}-${r.created_at}`}>
            <td>{FEE_LABELS[r.kind]}</td>
            <td className="num">{formatPrice(r.rate)}</td>
            <td>{formatDate(r.valid_from)}</td>
            <td>{r.comment ?? '—'}</td>
            <td>
              {r.author}, {formatTime(r.created_at)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Новая ставка — новая строка с датой начала; фонд пересчитывается с этой даты. */
function RateForm({
  context,
  fundId,
  onSaved,
}: {
  readonly context: Context;
  readonly fundId: string;
  readonly onSaved: () => void;
}) {
  const [kind, setKind] = useState<FeeKind>('management');
  const [rate, setRate] = useState('');
  const [validFrom, setValidFrom] = useState(moscowToday());
  const [comment, setComment] = useState('');
  const { run, error, busy } = useAction(onSaved);

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(() =>
      context.api.setFeeRate(fundId, {
        kind,
        rate: decimalInput(rate),
        valid_from: validFrom,
        comment: comment.trim() || null,
      }),
    );
  }

  return (
    <form aria-label="Новая ставка" onSubmit={submit}>
      <h3>Новая ставка</h3>
      <div className="fields">
        <label>
          Комиссия
          <select value={kind} onChange={(e) => setKind(e.target.value as FeeKind)}>
            {FEE_KINDS.map((k) => (
              <option key={k} value={k}>
                {FEE_LABELS[k]}
              </option>
            ))}
          </select>
        </label>
        <label>
          Ставка, % годовых
          <input inputMode="decimal" value={rate} required onChange={(e) => setRate(e.target.value)} />
        </label>
        <label>
          Действует с
          <input type="date" value={validFrom} required onChange={(e) => setValidFrom(e.target.value)} />
        </label>
        <label>
          Основание
          <input value={comment} onChange={(e) => setComment(e.target.value)} />
        </label>
      </div>
      <button type="submit" disabled={busy}>
        Сохранить ставку
      </button>
      <ErrorNote message={error} />
    </form>
  );
}
