import { FilterChips, LoadedView, useLoad } from '@am/ui';
import { Fragment, useCallback, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router';

import type { Context } from '../App';
import type { Decimal, PeriodResult, ResultSummary, SummaryLine } from '../api';
import { FlagNotes } from '../components/FlagNotes';
import { formatDate, moscowToday, shortMoney } from '../format';
import { fixed, rubles, signed } from '../positions';
import {
  ANNUAL_KEY,
  anyValue,
  columnDates,
  columnTitle,
  COLUMNS_KEY,
  CUSTOM_KEY,
  EXPENSE_PARTS,
  expensesTotal,
  lineGroups,
  parseColumns,
  parseCustom,
  parseFlag,
  PERCENT_KEY,
  PERIOD_KEYS,
  PERIOD_LABELS,
  previousMonth,
  SECURITY_PARTS,
  securitiesTotal,
  shownPeriods,
  type CustomPeriod,
  type FixedKey,
} from '../results';
import { useStored } from '../storage';

/**
 * Финансовый результат портфеля (решение 167) — как отчёт о финансовом результате:
 * статьи строками, периоды колонками; колонки, свой период и строки доходности
 * выбирает пользователь, выбор запоминается.
 */
export function ResultsPage({ context }: { readonly context: Context }) {
  const [params, setParams] = useSearchParams();
  const today = moscowToday();
  const fallback = context.portfolios.find((p) => p.is_fund) ?? context.portfolios[0];
  const portfolioId = params.get('portfolio') ?? fallback?.id ?? '';
  const date = params.get('date') ?? today;
  const [columns, setColumns] = useStored(COLUMNS_KEY, parseColumns, (value) => JSON.stringify(value));
  const [custom, setCustom] = useStored<CustomPeriod | null>(CUSTOM_KEY, parseCustom, (value) => JSON.stringify(value));
  const [percent, setPercent] = useStored(PERCENT_KEY, parseFlag);
  const [annual, setAnnual] = useStored(ANNUAL_KEY, parseFlag);
  const load = useCallback(
    () => context.api.resultSummary(portfolioId, date, custom),
    [context.api, portfolioId, date, custom],
  );
  const [state] = useLoad(load);

  if (context.portfolios.length === 0) {
    return <p className="muted">Нет доступных портфелей: доступ выдаёт администратор.</p>;
  }
  const update = (changes: { portfolio?: string; date?: string }) =>
    setParams({ portfolio: changes.portfolio ?? portfolioId, date: changes.date ?? date });
  const toggle = (key: FixedKey) =>
    setColumns(PERIOD_KEYS.filter((k) => (k === key ? !columns.includes(k) : columns.includes(k))));

  return (
    <>
      <div className="toolbar results-toolbar">
        <select aria-label="Портфель" value={portfolioId} onChange={(e) => update({ portfolio: e.target.value })}>
          {context.portfolios.map((p) => (
            <option key={p.id} value={p.id}>
              {p.code} — {p.name}
            </option>
          ))}
        </select>
        <span className="period-dates">
          на дату
          <input
            type="date"
            aria-label="Дата"
            value={date}
            required
            onChange={(e) => e.target.value && update({ date: e.target.value })}
          />
        </span>
        <FilterChips
          label="Быстрые даты"
          choices={[{ value: 'today', label: 'Сегодня' }]}
          value={date === today ? 'today' : 'other'}
          onChange={() => update({ date: today })}
        />
      </div>
      <div className="toolbar results-toolbar">
        <div className="toggle-row" role="group" aria-label="Колонки">
          {PERIOD_KEYS.map((key) => (
            <button key={key} type="button" className="toggle-pill" aria-pressed={columns.includes(key)} onClick={() => toggle(key)}>
              {PERIOD_LABELS[key]}
            </button>
          ))}
          <button
            type="button"
            className="toggle-pill"
            aria-pressed={custom !== null}
            onClick={() => setCustom(custom ? null : previousMonth(today))}
          >
            {custom ? 'Свой период' : '+ Свой период'}
          </button>
        </div>
        {custom && (
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
          <div className="toggle-row" role="group" aria-label="Доходность">
            <button type="button" className="toggle-pill" aria-pressed={percent} onClick={() => setPercent(!percent)}>
              % от вложений
            </button>
            <button type="button" className="toggle-pill" aria-pressed={annual} onClick={() => setAnnual(!annual)}>
              % годовых
            </button>
          </div>
        </span>
      </div>
      <LoadedView state={state}>
        {(summary) => (
          <Statement
            summary={summary}
            periods={shownPeriods(summary, columns)}
            percent={percent}
            annual={annual}
            today={today}
            onDropCustom={() => setCustom(null)}
          />
        )}
      </LoadedView>
    </>
  );
}

function Signed({ value }: { readonly value: Decimal }) {
  const { text, tone } = signed(value);
  return <span className={tone || undefined}>{text}</span>;
}

function Percent({ value }: { readonly value: Decimal | null }) {
  if (value === null) {
    return <>—</>;
  }
  const amount = Number(value);
  return <span className={amount > 0 ? 'up' : amount < 0 ? 'down' : undefined}>{fixed(value, 2)}%</span>;
}

function Statement({
  summary,
  periods,
  percent,
  annual,
  today,
  onDropCustom,
}: {
  readonly summary: ResultSummary;
  readonly periods: readonly PeriodResult[];
  readonly percent: boolean;
  readonly annual: boolean;
  readonly today: string;
  readonly onDropCustom: () => void;
}) {
  const [open, setOpen] = useState(false);
  const flags = [...new Set(periods.flatMap((p) => p.flags))];
  const groups = lineGroups(summary.lines, periods);
  const columns = periods.length + 1;
  const row = (label: ReactNode, cell: (period: PeriodResult) => ReactNode, className?: string) => (
    <tr className={className}>
      <td>{label}</td>
      {periods.map((p) => (
        <td key={p.key} className="num">
          {cell(p)}
        </td>
      ))}
    </tr>
  );
  const section = (label: string) => (
    <tr className="statement-section">
      <td colSpan={columns}>{label}</td>
    </tr>
  );
  const bonds = (lines: readonly SummaryLine[]) =>
    lines.map((line) => (
      <tr key={line.instrument_id} className="statement-bond">
        <td>
          <span className="tag">{line.short_name}</span> <span className="mono">{line.secid}</span>
        </td>
        {periods.map((p) => (
          <td key={p.key} className="num">
            <Signed value={line.totals[p.key] ?? '0'} />
          </td>
        ))}
      </tr>
    ));
  const expensesShown = EXPENSE_PARTS.some(([part]) => anyValue(periods, (p) => p[part]));
  return (
    <>
      <p className="muted">Портфели: {summary.portfolios.join(', ')}.</p>
      {summary.waiting_since && (
        <p className="notice notice-warning">
          Пересчитывается с {formatDate(summary.waiting_since)} — пока показан прежний расчёт.
        </p>
      )}
      <Tiles summary={summary} />
      <FlagNotes flags={flags} lead="Оценка неполная в периодах со звёздочкой" />
      {periods.length === 0 ? (
        <p className="muted">Колонки не выбраны — включите хотя бы один период.</p>
      ) : (
        <div className="positions-scroll">
          <table className="data statement" aria-label="Финансовый результат">
            <thead>
              <tr>
                <th>Статья, ₽</th>
                {periods.map((p) => (
                  <th key={p.key} className="num">
                    {columnTitle(p, today)}
                    {flags.length > 0 && p.flags.some((f) => f !== 'due_estimated') && <span className="danger"> *</span>}
                    {p.key === 'custom' && (
                      <button type="button" className="statement-drop" aria-label="Убрать свой период" onClick={onDropCustom}>
                        ×
                      </button>
                    )}
                    <span className="th-note">{columnDates(p)}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {section('Бумаги')}
              {SECURITY_PARTS.filter(([part]) => anyValue(periods, (p) => p[part])).map(([part, label]) => (
                <Fragment key={part}>{row(label, (p) => <Signed value={p[part]} />, 'statement-part')}</Fragment>
              ))}
              {row(
                <>
                  {summary.lines.length > 0 && (
                    <button
                      type="button"
                      className="statement-expand"
                      aria-expanded={open}
                      aria-label={open ? 'Свернуть по бумагам' : 'По бумагам'}
                      onClick={() => setOpen(!open)}
                    >
                      {open ? '−' : '+'}
                    </button>
                  )}
                  Результат по бумагам
                </>,
                (p) => <Signed value={securitiesTotal(p)} />,
                'statement-part statement-subtotal',
              )}
              {open && bonds(groups.held)}
              {open && groups.closed.length > 0 && (
                <>
                  <tr className="statement-bond-group">
                    <td colSpan={columns}>Проданные и погашенные</td>
                  </tr>
                  {bonds(groups.closed)}
                </>
              )}
              {anyValue(periods, (p) => p.repo) && (
                <>
                  {section('РЕПО')}
                  {row('Проценты по РЕПО', (p) => <Signed value={p.repo} />, 'statement-part')}
                </>
              )}
              {expensesShown && (
                <>
                  {section('Расходы фонда')}
                  {EXPENSE_PARTS.filter(([part]) => anyValue(periods, (p) => p[part])).map(([part, label]) => (
                    <Fragment key={part}>{row(label, (p) => <Signed value={p[part]} />, 'statement-part')}</Fragment>
                  ))}
                  {row('Итого расходы', (p) => <Signed value={expensesTotal(p)} />, 'statement-part statement-subtotal')}
                </>
              )}
              {row('Финансовый результат', (p) => <Signed value={p.total} />, 'statement-total')}
              {percent && row('% от вложений', (p) => <Percent value={p.return_percent} />, 'statement-percent')}
              {annual && row('% годовых', (p) => <Percent value={p.annual_percent} />, 'statement-percent')}
              {section('Справочно')}
              {row('Стоимость на конец периода', (p) => rubles(p.end_value), 'statement-part statement-reference')}
              {anyValue(periods, (p) => p.flows) &&
                row('Приток: паи и переводы', (p) => rubles(p.flows), 'statement-part statement-reference')}
              {(percent || annual) &&
                row('Вложено, средневзвешенно', (p) => rubles(p.invested), 'statement-part statement-reference')}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/** Стоимость, результат дня и с начала года, доходность с начала года. */
function Tiles({ summary }: { readonly summary: ResultSummary }) {
  const day = summary.periods.find((p) => p.key === 'day')!;
  const year = summary.periods.find((p) => p.key === 'year')!;
  const money = (value: Decimal) => {
    const amount = Number(value);
    return (
      <span className={amount > 0 ? 'up' : amount < 0 ? 'down' : undefined}>
        {amount > 0 ? '+' : ''}
        {shortMoney(value)}
      </span>
    );
  };
  return (
    <dl className="tiles results-tiles" aria-label="Итоги результата">
      <div className="tile">
        <dt>Стоимость портфеля</dt>
        <dd>{shortMoney(day.end_value)}</dd>
      </div>
      <div className="tile">
        <dt>Результат за день</dt>
        <dd>{money(day.total)}</dd>
      </div>
      <div className="tile">
        <dt>С начала года</dt>
        <dd>{money(year.total)}</dd>
      </div>
      <div className="tile">
        <dt>С начала года, % годовых</dt>
        <dd>
          <Percent value={year.annual_percent} />
        </dd>
      </div>
    </dl>
  );
}
