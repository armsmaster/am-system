import { Drawer, ErrorNote, FilterChips, LoadedView, useAction, useLoad, type Choice } from '@am/ui';
import { useCallback, useMemo, useState, type FormEvent } from 'react';

import type { CalendarDay, IsoDate, RefdataApi } from '../api';
import {
  dayAndWeekday,
  isWeekend,
  longDate,
  monthWeeks,
  MONTHS,
  plural,
  shortDate,
  skippedHolidays,
  tradingDaysInMonth,
  WEEK_HEADER,
  yearOf,
  yearStats,
  type Cell,
} from '../calendar';
import { Glyph } from '../components/Glyph';
import { formatDate, todayInMoscow, weekday } from '../format';

const FIRST_YEAR = 2000;
const LAST_YEAR = 2100;

type Kind = 'regular' | 'weekend' | 'holiday' | 'working';

const KIND_LABELS: Readonly<Record<Kind, string>> = {
  regular: 'торговый день',
  weekend: 'выходной',
  holiday: 'праздник, торгов нет',
  working: 'рабочий выходной, торги есть',
};

function kindOf(weekend: boolean, exception: CalendarDay | undefined): Kind {
  if (exception) {
    return exception.is_trading ? 'working' : 'holiday';
  }
  return weekend ? 'weekend' : 'regular';
}

/**
 * Торговый календарь. По умолчанию биржа работает с понедельника по пятницу;
 * справочник хранит только исключения — праздники в будни и рабочие выходные.
 * Год — сеткой месяцев; сопровождение ставит и убирает исключение щелчком по дню.
 */
export function CalendarPage({
  api,
  canEdit,
  today = todayInMoscow(),
}: {
  readonly api: RefdataApi;
  readonly canEdit: boolean;
  readonly today?: IsoDate;
}) {
  const current = yearOf(today);
  const [year, setYear] = useState(current);
  const load = useCallback(() => api.calendar(year), [api, year]);
  const [state, reload] = useLoad(load);
  const days = state.status === 'ready' ? state.data : null;
  const exceptions = useMemo(() => new Map((days ?? []).map((d) => [d.day, d])), [days]);
  const [opened, setOpened] = useState<IsoDate | null>(null);
  // Смотрят все; день открывает только сопровождение.
  const open = canEdit ? setOpened : undefined;
  const saved = useCallback(() => {
    setOpened(null);
    reload();
  }, [reload]);

  return (
    <section aria-label="Торговый календарь" className="calendar">
      <div className="toolbar calendar-toolbar">
        <YearSwitch year={year} current={current} onChange={setYear} />
        <Legend />
      </div>
      <div className="calendar-layout">
        <div className="calendar-main">
          <LoadedView state={state}>
            {() => <YearGrid year={year} exceptions={exceptions} today={today} onOpen={open} />}
          </LoadedView>
        </div>
        {/* Калькулятор от года не зависит — он виден, пока год ещё грузится. */}
        <div className="calendar-side">
          <ShiftForm api={api} today={today} />
          {days && <Exceptions year={year} days={days} onOpen={open} />}
        </div>
      </div>
      {opened && (
        <Drawer title={longDate(opened)} open onClose={() => setOpened(null)}>
          <DayForm key={opened} day={opened} exception={exceptions.get(opened)} api={api} onSaved={saved} />
        </Drawer>
      )}
      <p className="muted note">
        Торговые дни — с понедельника по пятницу; справочник хранит исключения: праздники, когда биржа закрыта, и
        выходные, когда она работает. Сопровождение заносит их раз в год по календарю Московской биржи
        {canEdit ? ' — щелчком по дню.' : '.'}
      </p>
    </section>
  );
}

function YearSwitch({
  year,
  current,
  onChange,
}: {
  readonly year: number;
  readonly current: number;
  readonly onChange: (year: number) => void;
}) {
  return (
    <div className="year-switch">
      <button
        type="button"
        className="icon-button"
        aria-label="Предыдущий год"
        disabled={year <= FIRST_YEAR}
        onClick={() => onChange(year - 1)}
      >
        <Glyph name="chevronLeft" size={20} />
      </button>
      <h2 className="year-title">{year}</h2>
      <button
        type="button"
        className="icon-button"
        aria-label="Следующий год"
        disabled={year >= LAST_YEAR}
        onClick={() => onChange(year + 1)}
      >
        <Glyph name="chevronRight" size={20} />
      </button>
      {year !== current && (
        <button type="button" className="link-button" onClick={() => onChange(current)}>
          к {current} году
        </button>
      )}
    </div>
  );
}

function Legend() {
  return (
    <ul className="cal-legend" aria-label="Обозначения">
      <li>
        <span className="cal-swatch holiday" /> праздник
      </li>
      <li>
        <span className="cal-swatch working" /> рабочий выходной
      </li>
      <li>
        <span className="cal-swatch weekend" /> выходной
      </li>
      <li>
        <span className="cal-swatch today" /> сегодня
      </li>
    </ul>
  );
}

function YearGrid({
  year,
  exceptions,
  today,
  onOpen,
}: {
  readonly year: number;
  readonly exceptions: ReadonlyMap<IsoDate, CalendarDay>;
  readonly today: IsoDate;
  readonly onOpen: ((day: IsoDate) => void) | undefined;
}) {
  const stats = yearStats(year, exceptions);
  return (
    <>
      <dl className="calendar-stats" aria-label={`Сводка ${year}`}>
        <div>
          <dt>Торговых дней</dt>
          <dd>{stats.trading}</dd>
        </div>
        <div className="holiday">
          <dt>Праздников в будни</dt>
          <dd>{stats.holidays}</dd>
        </div>
        <div className="working">
          <dt>Рабочих выходных</dt>
          <dd>{stats.workingWeekends}</dd>
        </div>
      </dl>
      <div className="calendar-year">
        {MONTHS.map((name, month) => (
          <Month key={name} year={year} month={month} exceptions={exceptions} today={today} onOpen={onOpen} />
        ))}
      </div>
    </>
  );
}

function Month({
  year,
  month,
  exceptions,
  today,
  onOpen,
}: {
  readonly year: number;
  readonly month: number;
  readonly exceptions: ReadonlyMap<IsoDate, CalendarDay>;
  readonly today: IsoDate;
  readonly onOpen: ((day: IsoDate) => void) | undefined;
}) {
  const trading = tradingDaysInMonth(year, month, exceptions);
  return (
    <div className="cal-month">
      <div className="cal-month-head">
        <h3>{MONTHS[month]}</h3>
        <span>
          {trading} {plural(trading, 'торговый день', 'торговых дня', 'торговых дней')}
        </span>
      </div>
      <table className="cal-grid" aria-label={`${MONTHS[month]} ${year}`}>
        <thead>
          <tr>
            {WEEK_HEADER.map((name, i) => (
              <th key={name} scope="col" className={i >= 5 ? 'weekend' : undefined}>
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {monthWeeks(year, month, exceptions).map((week, w) => (
            <tr key={w}>
              {week.map((cell, i) => (
                <td key={i}>{cell && <Day cell={cell} today={today} onOpen={onOpen} />}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Day({
  cell,
  today,
  onOpen,
}: {
  readonly cell: Cell;
  readonly today: IsoDate;
  readonly onOpen: ((day: IsoDate) => void) | undefined;
}) {
  const kind = kindOf(cell.weekend, cell.exception);
  const className = cell.day === today ? `cal-day ${kind} today` : `cal-day ${kind}`;
  const comment = cell.exception?.comment;
  const label = `${dayAndWeekday(cell.day)} — ${KIND_LABELS[kind]}${comment ? `: ${comment}` : ''}`;
  // Подсказка при наведении — только у исключений: у обычных дней она лишь мешала бы.
  const title = cell.exception ? label : undefined;
  if (!onOpen) {
    return (
      <span className={className} title={title}>
        {cell.date}
      </span>
    );
  }
  return (
    <button type="button" className={className} aria-label={label} title={title} onClick={() => onOpen(cell.day)}>
      {cell.date}
    </button>
  );
}

function Exceptions({
  year,
  days,
  onOpen,
}: {
  readonly year: number;
  readonly days: readonly CalendarDay[];
  readonly onOpen: ((day: IsoDate) => void) | undefined;
}) {
  return (
    <section className="calendar-card" aria-label={`Исключения ${year}`}>
      <h3>Исключения {year}</h3>
      {days.length === 0 ? (
        <p className="muted">Исключений за {year} год нет — торги с понедельника по пятницу.</p>
      ) : (
        <ul className="exception-list">
          {days.map((d) => {
            const kind = d.is_trading ? 'рабочий выходной' : 'праздник';
            const content = (
              <>
                <span className="exception-date">
                  {shortDate(d.day)}, {weekday(d.day)}
                </span>
                <span className={d.is_trading ? 'exception-mark working' : 'exception-mark holiday'} aria-hidden="true" />
                <span className="exception-text">
                  {d.comment ?? kind}
                  {d.comment && <span className="subline">{kind}</span>}
                </span>
              </>
            );
            return (
              <li key={d.day}>
                {onOpen ? (
                  <button type="button" className="exception-row" onClick={() => onOpen(d.day)}>
                    {content}
                  </button>
                ) : (
                  <div className="exception-row">{content}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/** Панель дня: исключение или обычный день — для будня и выходного свои варианты. */
function DayForm({
  day,
  exception,
  api,
  onSaved,
}: {
  readonly day: IsoDate;
  readonly exception: CalendarDay | undefined;
  readonly api: RefdataApi;
  readonly onSaved: () => void;
}) {
  const weekend = isWeekend(day);
  // Исключение «по смыслу»: праздник в будни или рабочий выходной. Праздник в субботу ничего не меняет.
  const fits = exception?.is_trading === weekend;
  const [choice, setChoice] = useState<'regular' | 'exception'>(exception && !fits ? 'regular' : 'exception');
  const [comment, setComment] = useState(fits ? (exception?.comment ?? '') : '');
  const { run, error, busy } = useAction(onSaved);
  const options = weekend
    ? {
        regular: ['Обычный выходной', 'Торгов нет, исключение не нужно'],
        exception: ['Рабочий выходной — торги есть', 'Биржа работает в этот выходной'],
      }
    : {
        regular: ['Обычный торговый день', 'Торги есть, исключение не нужно'],
        exception: ['Праздник — торгов нет', 'Биржа закрыта в этот будний день'],
      };
  const now = KIND_LABELS[kindOf(weekend, exception)];

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(() =>
      choice === 'exception'
        ? api.setDay(day, { is_trading: weekend, comment: comment.trim() || null })
        : api.clearDay(day),
    );
  }

  return (
    <form onSubmit={submit} aria-label="Исключение календаря" className="drawer-form">
      <p className="day-now">
        Сейчас: {now}
        {exception?.comment && ` · ${exception.comment}`}
      </p>
      <fieldset className="option-cards">
        <legend>Что в этот день</legend>
        {(['exception', 'regular'] as const).map((value) => (
          <label key={value} className="option-card">
            <input type="radio" name="kind" checked={choice === value} onChange={() => setChoice(value)} />
            <span>
              {options[value][0]}
              <span className="hint">{options[value][1]}</span>
            </span>
          </label>
        ))}
      </fieldset>
      {choice === 'exception' && (
        <label>
          Комментарий
          <input
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder={weekend ? 'Перенос рабочего дня' : 'День народного единства'}
            maxLength={200}
          />
        </label>
      )}
      {choice === 'exception' ? (
        <button type="submit" className="primary" disabled={busy}>
          Сохранить
        </button>
      ) : exception ? (
        <button type="submit" disabled={busy}>
          Убрать исключение
        </button>
      ) : (
        <p className="hint">Исключение не нужно: день и так {weekend ? 'выходной' : 'торговый'}.</p>
      )}
      <ErrorNote message={error} />
    </form>
  );
}

const STEPS: readonly Choice<string>[] = ['1', '2', '3', '5'].map((n) => ({ value: n, label: `Т+${n}` }));

interface Shift {
  readonly date: IsoDate;
  readonly skipped: readonly CalendarDay[];
}

/** Дата через N торговых дней — например, дата расчётов Т+1. Считает сразу, без кнопки. */
function ShiftForm({ api, today }: { readonly api: RefdataApi; readonly today: IsoDate }) {
  const [from, setFrom] = useState(today);
  const [count, setCount] = useState('1');
  const n = Number(count);
  const valid = from !== '' && count !== '' && Number.isInteger(n) && n >= 0 && n <= 30;
  const load = useCallback(async (): Promise<Shift | null> => {
    if (!valid) {
      return null;
    }
    const { date } = await api.addTradingDays(from, n);
    // Какие праздники пропущены — по исключениям обоих годов, если расчёт перешёл через Новый год.
    const years = [...new Set([yearOf(from), yearOf(date)])];
    const lists = await Promise.all(years.map((y) => api.calendar(y)));
    return { date, skipped: skippedHolidays(from, date, lists.flat()) };
  }, [api, from, n, valid]);
  const [state] = useLoad(load);

  return (
    <form onSubmit={(e) => e.preventDefault()} aria-label="Калькулятор торговых дней" className="calculator">
      <h3>Калькулятор торговых дней</h3>
      <div className="calc-row">
        <label>
          От даты
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label>
          Торговых дней
          <input type="number" min={0} max={30} value={count} onChange={(e) => setCount(e.target.value)} />
        </label>
      </div>
      <FilterChips label="Сколько торговых дней" choices={STEPS} value={count} onChange={setCount} />
      {valid ? (
        <LoadedView state={state}>
          {(shift) =>
            shift && (
              <div role="status" className="calc-answer">
                <span className="calc-caption">Т+{n} →</span>
                <p className="calc-result">
                  {yearOf(shift.date) === yearOf(from) ? dayAndWeekday(shift.date) : longDate(shift.date)}
                </p>
                {shift.skipped.length > 0 && (
                  <p className="subline">
                    пропущены праздники:{' '}
                    {shift.skipped
                      .map((d) => `${formatDate(d.day).slice(0, 5)}${d.comment ? ` — ${d.comment}` : ''}`)
                      .join(', ')}
                  </p>
                )}
              </div>
            )
          }
        </LoadedView>
      ) : (
        <p className="hint">Укажите дату и число торговых дней от 0 до 30.</p>
      )}
    </form>
  );
}
