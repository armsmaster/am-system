import { Drawer, FilterChips, LoadedView, useLoad, type Choice } from '@am/ui';
import { Fragment, useCallback, useState } from 'react';

import type { Context } from '../App';
import type { DayResult, Instrument, Line, Positions, PortfolioState, RepoLine } from '../api';
import { ColumnsPanel } from '../components/ColumnsPanel';
import { FlagNotes } from '../components/FlagNotes';
import { LotsPanel } from '../components/LotsPanel';
import { PositionsTable, type TableContext } from '../components/PositionsTable';
import { StructureView } from '../components/Structure';
import { useSelection } from '../components/Selection';
import {
  formatDate,
  formatMoney,
  formatPrice,
  formatQuantity,
  moscowToday,
  movementLabel,
  plural,
  shortMoney,
  sumMoney,
} from '../format';
import {
  classColumn,
  COLUMNS_KEY,
  dv01Of,
  GROUP_KEY,
  groupLines,
  makeRefs,
  MEASURE_KEY,
  monthEndLabel,
  nearbyWeekday,
  NO_CHOICE,
  parseChoice,
  parseGrouping,
  parseMeasure,
  parseSize,
  parseView,
  presetColumns,
  previousMonthEnd,
  previousQuarterEnd,
  quarterEndLabel,
  shortDay,
  shownColumns,
  SIZE_KEY,
  validGrouping,
  VIEW_KEY,
  type ColumnChoice,
  type FontSize,
  type Measure,
  type Preset,
  type View,
} from '../positions';
import { useStored } from '../storage';

type QuickDay = 'today' | 'month' | 'quarter' | 'custom';

const MEASURES: readonly Choice<Measure>[] = [
  { value: 'continuous', label: 'Непрерывная' },
  { value: 'annual', label: 'Годовая' },
];

const VIEWS: readonly Choice<View>[] = [
  { value: 'table', label: 'Таблица' },
  { value: 'structure', label: 'Структура' },
];

/** Таблица только с бумагами группы, выбранной на диаграмме структуры. */
interface Filter {
  readonly label: string;
  readonly ids: readonly string[];
}

const SIZES: readonly Choice<FontSize>[] = [
  { value: 'small', label: 'A−' },
  { value: 'normal', label: 'A' },
  { value: 'large', label: 'A+' },
];

/**
 * Позиции на конец дня (design/05, решение 164): облигации, паи фондов и РЕПО
 * отдельными блоками, колонки на выбор, результат за периоды, доходность и рейтинг.
 * Сегодня — по текущей цене. Compound — сумма по своим physical-портфелям.
 */
export function PositionsPage({ context }: { readonly context: Context }) {
  const { portfolioId, date, update } = useSelection(context.portfolios);
  const load = useCallback(
    () =>
      Promise.all([
        context.api.positions(portfolioId, date),
        // Без справочника позиции всё равно нужны: колонки рейтинга и признаков будут пустыми.
        context.api.attributes(date).catch(() => null),
        context.api.ratingScale().catch(() => null),
      ]),
    [context.api, portfolioId, date],
  );
  const [state] = useLoad(load);
  const [measure, setMeasure] = useStored(MEASURE_KEY, parseMeasure);
  const [size, setSize] = useStored(SIZE_KEY, parseSize);
  const [grouping, setGrouping] = useStored(GROUP_KEY, parseGrouping);
  const [choice, setChoice] = useStored<ColumnChoice>(COLUMNS_KEY, parseChoice, (value) => JSON.stringify(value));
  const [mode, setMode] = useStored(VIEW_KEY, parseView);
  const [filter, setFilter] = useState<Filter | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [opened, setOpened] = useState<Line | null>(null);

  if (context.portfolios.length === 0) {
    return <p className="muted">Нет доступных портфелей: доступ выдаёт администратор.</p>;
  }
  // При перезагрузке на экране остаются прежние данные — справочник берём из них.
  const loaded = state.status === 'ready' ? state.data : null;
  const refs = makeRefs(loaded?.[1] ?? null, loaded?.[2] ?? null);
  const fresh = loaded?.[0].date === date ? loaded[0] : null;
  const columns = shownColumns(choice, refs.classifiers);
  const shownGrouping = validGrouping(grouping, refs.classifiers);

  function choosePreset(preset: Preset) {
    const next = preset === 'mine' ? choice.custom : presetColumns(preset, refs.classifiers);
    setChoice({ columns: next, custom: choice.custom });
  }

  return (
    <div className={`positions size-${size}`}>
      <DateBar
        context={context}
        portfolioId={portfolioId}
        date={date}
        previous={fresh?.previous_day ?? nearbyWeekday(date, -1)}
        next={fresh?.next_day ?? nearbyWeekday(date, 1)}
        onChange={update}
      />
      <div className="toolbar positions-toolbar">
        <FilterChips label="Вид" choices={VIEWS} value={mode} onChange={setMode} />
        {mode === 'table' && (
          <>
            <FilterChips
              label="Группировка"
              choices={[
                { value: 'type', label: 'Тип' },
                ...refs.classifiers.map((c) => ({ value: classColumn(c), label: c.name })),
                { value: 'issuer', label: 'Эмитент' },
                { value: 'none', label: 'Без групп' },
              ]}
              value={shownGrouping}
              onChange={setGrouping}
            />
            <FilterChips label="Доходность" choices={MEASURES} value={measure} onChange={setMeasure} />
            <span className="toolbar-end">
              <button type="button" onClick={() => setChoosing(true)}>
                Колонки
              </button>
              <FilterChips label="Размер шрифта" choices={SIZES} value={size} onChange={setSize} />
            </span>
          </>
        )}
      </div>
      <LoadedView state={state}>
        {([view]) =>
          mode === 'structure' ? (
            <>
              {view.portfolios.map((p) => (
                <StatusNote key={p.id} state={p} date={view.date} />
              ))}
              <Tiles view={view} measure={measure} />
              {view.lines.length === 0 ? (
                <p className="muted">Бумаг нет.</p>
              ) : (
                <StructureView
                  lines={view.lines}
                  date={view.date}
                  refs={refs}
                  onPick={(label, ids) => {
                    setFilter({ label, ids });
                    setMode('table');
                  }}
                />
              )}
            </>
          ) : (
            <PositionsView
              view={view}
              instruments={context.instruments}
              columns={columns}
              grouping={shownGrouping}
              filter={filter}
              onShowAll={() => setFilter(null)}
              table={{ refs, measure, several: view.portfolios.length > 1, onLots: setOpened }}
            />
          )
        }
      </LoadedView>
      <Drawer title="Колонки таблицы" open={choosing} onClose={() => setChoosing(false)}>
        <ColumnsPanel
          shown={columns}
          classifiers={refs.classifiers}
          onChange={(next) => setChoice({ columns: next, custom: next })}
          onPreset={choosePreset}
          onReset={() => setChoice(NO_CHOICE)}
        />
      </Drawer>
      {opened && (
        <Drawer title="Лоты и оценка" open onClose={() => setOpened(null)} wide>
          <LotsPanel line={opened} refs={refs} measure={measure} />
        </Drawer>
      )}
    </div>
  );
}

/**
 * Портфель и дата: соседние торговые дни (дата мелко под кнопкой), конец прошлого
 * месяца и квартала, своя дата. Портфель и дата — в адресе страницы.
 */
function DateBar({
  context,
  portfolioId,
  date,
  previous,
  next,
  onChange,
}: {
  readonly context: Context;
  readonly portfolioId: string;
  readonly date: string;
  readonly previous: string;
  readonly next: string;
  readonly onChange: (changes: { portfolio?: string; date?: string }) => void;
}) {
  const today = moscowToday();
  const days: Readonly<Record<QuickDay, string>> = {
    today,
    month: previousMonthEnd(today),
    quarter: previousQuarterEnd(today),
    custom: date,
  };
  const choices: Choice<QuickDay>[] = [
    { value: 'today', label: 'Сегодня' },
    { value: 'month', label: monthEndLabel(days.month, today) },
    { value: 'quarter', label: quarterEndLabel(days.quarter, today) },
  ];
  const quick = choices.find((c) => days[c.value] === date)?.value ?? 'custom';
  return (
    <div className="toolbar positions-toolbar">
      <select aria-label="Портфель" value={portfolioId} onChange={(e) => onChange({ portfolio: e.target.value })}>
        {context.portfolios.map((p) => (
          <option key={p.id} value={p.id}>
            {p.code} — {p.name}
          </option>
        ))}
      </select>
      <span className="day-nav">
        <button
          type="button"
          className="day-step"
          aria-label={`Предыдущий торговый день, ${formatDate(previous)}`}
          onClick={() => onChange({ date: previous })}
        >
          <span>‹ пред.</span>
          <small>{shortDay(previous)}</small>
        </button>
        <input
          type="date"
          aria-label="Дата"
          value={date}
          required
          onChange={(e) => e.target.value && onChange({ date: e.target.value })}
        />
        {next <= today && (
          <button
            type="button"
            className="day-step"
            aria-label={`Следующий торговый день, ${formatDate(next)}`}
            onClick={() => onChange({ date: next })}
          >
            <span>след. ›</span>
            <small>{shortDay(next)}</small>
          </button>
        )}
      </span>
      <FilterChips label="Быстрые даты" choices={choices} value={quick} onChange={(v) => onChange({ date: days[v] })} />
    </div>
  );
}

function PositionsView({
  view,
  instruments,
  columns,
  grouping,
  filter,
  onShowAll,
  table,
}: {
  readonly view: Positions;
  readonly instruments: readonly Instrument[];
  readonly columns: readonly string[];
  readonly grouping: string;
  readonly filter: Filter | null;
  readonly onShowAll: () => void;
  readonly table: TableContext;
}) {
  // Фильтр по группе структуры: только её бумаги, без РЕПО и проданных в периоде.
  const lines = filter === null ? view.lines : view.lines.filter((line) => filter.ids.includes(line.instrument_id));
  const bonds = lines.filter((line) => line.kind === 'bond');
  const shares = lines.filter((line) => line.kind !== 'bond');
  const sold =
    filter === null && Object.values(view.closed).some((p) => Number(p.result) !== 0 || Number(p.realized) !== 0);
  const repos = filter === null ? view.repos : [];
  return (
    <>
      {view.portfolios.map((p) => (
        <StatusNote key={p.id} state={p} date={view.date} />
      ))}
      <Tiles view={view} measure={table.measure} />
      <FundFiguresNote view={view} />
      {view.result && <ResultParts result={view.result} />}
      {filter && (
        <p className="notice filter-note">
          Только {filter.label} — {plural(lines.length, 'бумага', 'бумаги', 'бумаг')}.
          <button type="button" className="link-button" onClick={onShowAll}>
            Показать все
          </button>
        </p>
      )}
      {(bonds.length > 0 || sold) && (
        <section className="positions-block">
          <h2>
            Облигации <span className="subline">{plural(bonds.length, 'бумага', 'бумаги', 'бумаг')}</span>
          </h2>
          <PositionsTable
            label="Облигации"
            groups={groupLines(bonds, grouping, table.refs)}
            grouped={grouping !== 'none'}
            columns={columns}
            closed={sold ? view.closed : null}
            context={table}
          />
        </section>
      )}
      {shares.length > 0 && (
        <section className="positions-block">
          <h2>Паи фондов</h2>
          <PositionsTable
            label="Паи фондов"
            groups={[{ key: 'all', label: '', lines: shares }]}
            grouped={false}
            columns={columns}
            context={table}
          />
        </section>
      )}
      {repos.length > 0 && (
        <section className="positions-block">
          <h2>РЕПО</h2>
          <RepoTable repos={repos} />
        </section>
      )}
      {lines.length === 0 && repos.length === 0 && <p className="muted">Бумаг нет.</p>}
      <Movements portfolios={view.portfolios} instruments={instruments} />
    </>
  );
}

/** Стоимость, деньги, результат дня и с начала года, DV01 — плитками над таблицами. */
function Tiles({ view, measure }: { readonly view: Positions; readonly measure: Measure }) {
  const { result } = view;
  const year = sumMoney([...view.lines.map((line) => line.periods.year.result), view.closed.year.result]);
  const dv01 = sumMoney(view.lines.map((line) => dv01Of(line, measure) ?? '0'));
  return (
    <dl className="tiles positions-tiles" aria-label="Итоги портфеля">
      <div className="tile">
        <dt>Стоимость</dt>
        <dd>{result === null ? '—' : shortMoney(result.value)}</dd>
      </div>
      <div className="tile">
        <dt>Деньги</dt>
        <dd>{shortMoney(view.cash)}</dd>
      </div>
      <div className="tile">
        <dt>Результат дня</dt>
        <dd>{result === null ? '—' : <SignedMoney value={result.total} />}</dd>
      </div>
      <div className="tile">
        <dt>С начала года</dt>
        <dd>
          <SignedMoney value={year} />
          <div className="subline">по бумагам</div>
        </dd>
      </div>
      <div className="tile">
        <dt>DV01</dt>
        <dd>
          {shortMoney(dv01)}
          <div className="subline">за 1 бп, {measure === 'annual' ? 'годовая' : 'непрерывная'}</div>
        </dd>
      </div>
    </dl>
  );
}

function SignedMoney({ value }: { readonly value: string }) {
  const amount = Number(value);
  return (
    <span className={amount > 0 ? 'up' : amount < 0 ? 'down' : undefined}>
      {amount > 0 ? '+' : ''}
      {shortMoney(value)}
    </span>
  );
}

/** Открытые сделки РЕПО: долг и начисленные проценты (design/07). */
function RepoTable({ repos }: { readonly repos: readonly RepoLine[] }) {
  return (
    <div className="positions-scroll">
      <table className="data positions-table" aria-label="РЕПО">
        <thead>
          <tr>
            <th>Сделка</th>
            <th>Обеспечение</th>
            <th className="num">Ставка, %</th>
            <th>Части</th>
            <th className="num">Долг, ₽</th>
            <th className="num">Проценты, ₽</th>
            <th className="num">В стоимости, ₽</th>
          </tr>
        </thead>
        <tbody>
          {repos.map((r) => (
            <tr key={r.id}>
              <td>
                <span className="pill side-repo">{r.direct ? 'прямое' : 'обратное'}</span>
                <div className="subline">
                  {r.direct ? 'привлекли' : 'разместили'} · {r.portfolio_code}
                </div>
              </td>
              <td>
                <div className="cell-head">
                  <span className="tag">{r.short_name}</span>
                  {r.instrument_id !== null && <span className="mono">{r.secid}</span>}
                </div>
                {r.quantity !== 0 && <div className="subline">{formatQuantity(r.quantity)} шт.</div>}
              </td>
              <td className="num">{formatPrice(r.rate)}</td>
              <td>
                {formatDate(r.first_date)} → {formatDate(r.second_date)}
                <div className="subline">вторая часть {formatMoney(r.second_amount)} ₽</div>
              </td>
              <td className="num">{formatMoney(r.principal)}</td>
              <td className="num">{formatMoney(r.accrued)}</td>
              <td className="num">{formatMoney(r.value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Результат дня по частям — под плитками, раскрывается по щелчку; пометки об оценке видны всегда. */
function ResultParts({ result }: { readonly result: DayResult }) {
  const parts = [
    ['переоценка', result.revaluation],
    ['реализованный результат', result.realized],
    ['купонный доход', result.coupon],
    ['комиссии по сделкам', result.fees],
    ['расходы фонда', result.expenses],
    ['вознаграждение и комиссии от СЧА', result.charges],
    ['корректировка комиссий при выплате', result.correction],
    ['проценты по РЕПО', result.repo],
  ] as const;
  return (
    <>
      <details className="result-parts">
        <summary>Результат дня по частям: {formatMoney(result.total)} ₽</summary>
        <dl className="facts" aria-label="Стоимость и результат дня">
          <dt>стоимость портфеля</dt>
          <dd>{formatMoney(result.value)} ₽</dd>
          <dt>результат дня</dt>
          <dd>
            <strong>{formatMoney(result.total)} ₽</strong>
          </dd>
          {parts.map(([label, value]) => (
            <Fragment key={label}>
              <dt>{label}</dt>
              <dd>{formatMoney(value)} ₽</dd>
            </Fragment>
          ))}
          {Number(result.flows) !== 0 && (
            <>
              <dt>приток: паи и переводы</dt>
              <dd>{formatMoney(result.flows)} ₽</dd>
            </>
          )}
          {Number(result.receivable) !== 0 && (
            <>
              <dt>к получению по сделкам до расчётов</dt>
              <dd>{formatMoney(result.receivable)} ₽</dd>
            </>
          )}
          {Number(result.payable) !== 0 && (
            <>
              <dt>к уплате по сделкам до расчётов</dt>
              <dd>{formatMoney(result.payable)} ₽</dd>
            </>
          )}
        </dl>
      </details>
      <FlagNotes flags={result.flags} />
    </>
  );
}

/** Паи, требования, обязательства и расходы — если у портфеля они есть. */
function FundFiguresNote({ view }: { readonly view: Positions }) {
  const figures = [
    ['паи фонда', view.units, ' шт.'],
    ['требования к пайщикам', view.claims, ' ₽'],
    ['обязательства перед пайщиками', view.obligations, ' ₽'],
    ['начисленные расходы', view.expenses, ' ₽'],
  ] as const;
  const shown = figures.filter(([, value]) => Number(value) !== 0);
  if (shown.length === 0) {
    return null;
  }
  return (
    <dl className="facts" aria-label="Паи и расчёты с пайщиками">
      {shown.map(([label, value, unit]) => (
        <Fragment key={label}>
          <dt>{label}</dt>
          <dd>
            {unit === ' шт.' ? formatPrice(value) : formatMoney(value)}
            {unit}
          </dd>
        </Fragment>
      ))}
    </dl>
  );
}

function StatusNote({ state, date }: { readonly state: PortfolioState; readonly date: string }) {
  if (state.waiting_since !== null) {
    return (
      <p className="notice notice-warning">
        {state.code}: пересчитывается с {formatDate(state.waiting_since)} — пока показан прежний расчёт.
      </p>
    );
  }
  if (state.as_of === null) {
    return <p className="muted">{state.code}: расчёта ещё нет — сделок до этой даты не было.</p>;
  }
  if (state.as_of !== date) {
    return (
      <p className="muted">
        {state.code}: последний расчёт — на {formatDate(state.as_of)}.
      </p>
    );
  }
  return null;
}

function Movements({
  portfolios,
  instruments,
}: {
  readonly portfolios: readonly PortfolioState[];
  readonly instruments: readonly Instrument[];
}) {
  const rows = portfolios.flatMap((p) => p.movements.map((m, index) => ({ p, m, index })));
  if (rows.length === 0) {
    return null;
  }
  const secid = (id: string | null) => (id === null ? '—' : (instruments.find((i) => i.id === id)?.secid ?? id));
  return (
    <section className="positions-block">
      <h2>Движения дня</h2>
      <table className="data" aria-label="Движения дня">
        <thead>
          <tr>
            <th>Портфель</th>
            <th>Движение</th>
            <th>Бумага</th>
            <th className="num">Бумаги, шт.</th>
            <th className="num">Деньги, ₽</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ p, m, index }) => (
            <tr key={`${p.id}-${index}`}>
              <td>{p.code}</td>
              <td>{movementLabel(m)}</td>
              <td>{secid(m.instrument_id)}</td>
              <td className="num">{m.quantity ? formatQuantity(m.quantity) : '—'}</td>
              <td className="num">{formatMoney(m.cash)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
