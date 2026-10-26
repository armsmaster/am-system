import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';
import { useCallback, useState, type FormEvent } from 'react';

import type { CalendarDay, IsoDate, RefdataApi } from '../api';
import { formatDate, todayInMoscow, weekday } from '../format';

const FIRST_YEAR = 2000;
const LAST_YEAR = 2100;

/**
 * Торговый календарь. По умолчанию биржа работает с понедельника по пятницу;
 * здесь хранятся только исключения — праздники и рабочие выходные.
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
  const [year, setYear] = useState(Number(today.slice(0, 4)));
  const load = useCallback(() => api.calendar(year), [api, year]);
  const [state, reload] = useLoad(load);

  // Сохранили день другого года — показываем тот год.
  const showDay = useCallback(
    (day: IsoDate) => {
      setYear(Number(day.slice(0, 4)));
      reload();
    },
    [reload],
  );

  return (
    <>
      <p className="muted">
        Торговые дни — с понедельника по пятницу. Здесь — исключения: праздники, когда биржа
        закрыта, и выходные, когда она работает. Исключения сопровождение заносит раз в год, по
        календарю Московской биржи.
      </p>
      <div className="split">
        <section aria-label="Исключения календаря">
          <h2 className="year">
            <button
              type="button"
              aria-label="Предыдущий год"
              disabled={year <= FIRST_YEAR}
              onClick={() => setYear(year - 1)}
            >
              ←
            </button>
            {year}
            <button
              type="button"
              aria-label="Следующий год"
              disabled={year >= LAST_YEAR}
              onClick={() => setYear(year + 1)}
            >
              →
            </button>
          </h2>
          <LoadedView state={state}>
            {(days) => (
              <DaysTable days={days} year={year} api={api} canEdit={canEdit} onDone={reload} />
            )}
          </LoadedView>
        </section>
        <div>
          {canEdit && <DayForm api={api} today={today} onSaved={showDay} />}
          <ShiftForm api={api} today={today} />
        </div>
      </div>
    </>
  );
}

function dayLabel(day: CalendarDay): string {
  return day.is_trading ? 'рабочий выходной — торги есть' : 'праздник — торгов нет';
}

function DaysTable({
  days,
  year,
  api,
  canEdit,
  onDone,
}: {
  readonly days: readonly CalendarDay[];
  readonly year: number;
  readonly api: RefdataApi;
  readonly canEdit: boolean;
  readonly onDone: () => void;
}) {
  const { run, error, busy } = useAction(onDone);

  if (days.length === 0) {
    return (
      <p className="muted">Исключений за {year} год нет — торги с понедельника по пятницу.</p>
    );
  }
  return (
    <>
      <table className="data">
        <thead>
          <tr>
            <th>Дата</th>
            <th>День</th>
            <th>Комментарий</th>
            {canEdit && <th />}
          </tr>
        </thead>
        <tbody>
          {days.map((d) => (
            <tr key={d.day}>
              <td>
                {formatDate(d.day)}, {weekday(d.day)}
              </td>
              <td>{dayLabel(d)}</td>
              <td>{d.comment ?? '—'}</td>
              {canEdit && (
                <td>
                  <button
                    type="button"
                    disabled={busy}
                    aria-label={`Удалить исключение ${formatDate(d.day)}`}
                    onClick={() => void run(() => api.clearDay(d.day))}
                  >
                    Удалить
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      <ErrorNote message={error} />
    </>
  );
}

function DayForm({
  api,
  today,
  onSaved,
}: {
  readonly api: RefdataApi;
  readonly today: IsoDate;
  readonly onSaved: (day: IsoDate) => void;
}) {
  const [day, setDay] = useState(today);
  const [isTrading, setIsTrading] = useState(false);
  const [comment, setComment] = useState('');
  const onDone = useCallback(() => onSaved(day), [onSaved, day]);
  const { run, error, busy } = useAction(onDone);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const saved = await run(() =>
      api.setDay(day, { is_trading: isTrading, comment: comment.trim() || null }),
    );
    if (saved) {
      setComment('');
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} aria-label="Исключение календаря">
      <h3>Добавить или изменить исключение</h3>
      <label>
        Дата
        <input type="date" value={day} onChange={(e) => setDay(e.target.value)} required />
      </label>
      <label>
        Что в этот день
        <select
          value={isTrading ? 'trading' : 'holiday'}
          onChange={(e) => setIsTrading(e.target.value === 'trading')}
        >
          <option value="holiday">праздник — торгов нет</option>
          <option value="trading">рабочий выходной — торги есть</option>
        </select>
      </label>
      <label>
        Комментарий
        <input
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="например, День народного единства"
          maxLength={200}
        />
      </label>
      <button type="submit" disabled={busy}>
        Сохранить
      </button>
      <ErrorNote message={error} />
    </form>
  );
}

const nothing = () => undefined;

/** Дата через N торговых дней — например, дата расчётов T+1. */
function ShiftForm({ api, today }: { readonly api: RefdataApi; readonly today: IsoDate }) {
  const [day, setDay] = useState(today);
  const [days, setDays] = useState('1');
  const [result, setResult] = useState<IsoDate | null>(null);
  const { run, error, busy } = useAction(nothing);

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(async () => setResult((await api.addTradingDays(day, Number(days))).date));
  }

  return (
    <form onSubmit={submit} aria-label="Дата через N торговых дней">
      <h3>Дата через N торговых дней</h3>
      <label>
        От даты
        <input
          type="date"
          value={day}
          onChange={(e) => {
            setDay(e.target.value);
            setResult(null);
          }}
          required
        />
      </label>
      <label>
        Торговых дней
        <input
          type="number"
          min={0}
          max={30}
          value={days}
          onChange={(e) => {
            setDays(e.target.value);
            setResult(null);
          }}
          required
        />
      </label>
      <button type="submit" disabled={busy}>
        Посчитать
      </button>
      {result && (
        <p role="status">
          {formatDate(result)}, {weekday(result)}
        </p>
      )}
      <ErrorNote message={error} />
    </form>
  );
}
