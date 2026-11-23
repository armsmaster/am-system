import { FilterChips, LoadedView, useLoad, type Choice } from '@am/ui';
import { useCallback, useState } from 'react';
import { useSearchParams } from 'react-router';

import type { Context } from '../App';
import type { Account, CouponForecast, Ladder, LadderDay, LadderMovement } from '../api';
import {
  forecastBasis,
  formatDate,
  formatMoney,
  formatQuantity,
  monthStart,
  moscowToday,
  movementLabel,
  plural,
  shiftDay,
  shortMoney,
  weekday,
} from '../format';

/** Начало плана — не раньше чем за 93 дня до сегодня (решение 161). */
const MAX_PAST_DAYS = 93;

type Start = 'today' | 'week' | 'month' | 'custom';

const START_LABELS: Readonly<Record<Start, string>> = {
  today: 'С сегодня',
  week: 'Неделя назад',
  month: 'С начала месяца',
  custom: 'С даты',
};

/**
 * План по расчётам по счёту (design/05, решение 161): деньги и бумаги на счёте на
 * конец каждой даты расчётов с выбранной даты до горизонта. Прошлые дни — факт,
 * сегодня подсвечено, дальше — план. Дефицит — красным: к расчётам не хватит денег
 * или бумаг, нужно РЕПО или перенос.
 */
export function PlanPage({ context }: { readonly context: Context }) {
  const load = useCallback(() => context.api.accounts(), [context.api]);
  const [state] = useLoad(load);
  return (
    <LoadedView state={state}>
      {(accounts) =>
        accounts.length === 0 ? (
          <p className="muted">Нет доступных счетов: доступ выдаёт администратор.</p>
        ) : (
          <AccountPlan context={context} accounts={accounts} />
        )
      }
    </LoadedView>
  );
}

function AccountPlan({ context, accounts }: { readonly context: Context; readonly accounts: readonly Account[] }) {
  const [params, setParams] = useSearchParams();
  const [picking, setPicking] = useState(false);
  const today = moscowToday();
  // Счетов хотя бы один — пустой список страница не показывает.
  const accountId = params.get('account') ?? accounts[0]!.id;
  const from = params.get('from') ?? undefined;
  const load = useCallback(() => context.api.ladder(accountId, from), [context.api, accountId, from]);
  const [state] = useLoad(load);
  const starts: Record<Exclude<Start, 'custom'>, string | undefined> = {
    today: undefined,
    week: shiftDay(today, -7),
    month: monthStart(today),
  };
  const matched = (Object.keys(starts) as Exclude<Start, 'custom'>[]).find((key) => starts[key] === from);
  const start: Start = picking || matched === undefined ? 'custom' : matched;
  const choices: Choice<Start>[] = (Object.keys(START_LABELS) as Start[]).map((value) => ({
    value,
    label: START_LABELS[value],
  }));
  const show = (changes: { account?: string; from?: string | undefined }) => {
    const next: Record<string, string> = { account: changes.account ?? accountId };
    const since = 'from' in changes ? changes.from : from;
    if (since !== undefined) {
      next.from = since;
    }
    setParams(next);
  };

  function choose(next: Start) {
    setPicking(next === 'custom');
    if (next !== 'custom') {
      show({ from: starts[next] });
    }
  }

  return (
    <>
      <div className="toolbar">
        <select aria-label="Счёт" value={accountId} onChange={(e) => show({ account: e.target.value })}>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.code} — {a.name}
            </option>
          ))}
        </select>
        <FilterChips label="Начало плана" choices={choices} value={start} onChange={choose} />
        {start === 'custom' && (
          <input
            type="date"
            aria-label="С даты"
            value={from ?? today}
            min={shiftDay(today, -MAX_PAST_DAYS)}
            max={today}
            onChange={(e) => e.target.value && show({ from: e.target.value })}
          />
        )}
      </div>
      <LoadedView state={state}>{(ladder) => <PlanView ladder={ladder} />}</LoadedView>
    </>
  );
}

/** Дефицит называем в день, когда он появился или вырос: дальше он тянется, пока его не перекроют. */
function deficits(ladder: Ladder, secid: (id: string | null) => string | null): string[] {
  return ladder.days.flatMap((d, index) => {
    const previous = ladder.days[index - 1];
    const held = (id: string) => previous?.securities.find((s) => s.instrument_id === id)?.quantity ?? 0;
    return [
      ...(Number(d.cash) < Math.min(0, Number(previous?.cash ?? 0))
        ? [`Не хватает ${formatMoney(d.cash.slice(1))} ₽ к расчётам ${formatDate(d.date)}.`]
        : []),
      ...d.securities
        .filter((s) => s.quantity < Math.min(0, held(s.instrument_id)))
        .map((s) => `Не хватает ${formatQuantity(-s.quantity)} ${secid(s.instrument_id)} к расчётам ${formatDate(d.date)}.`),
    ];
  });
}

function short(day: LadderDay): boolean {
  return Number(day.cash) < 0 || day.securities.some((s) => s.quantity < 0);
}

function PlanView({ ladder }: { readonly ladder: Ladder }) {
  const secid = (id: string | null) =>
    id === null ? null : (ladder.instruments.find((i) => i.instrument_id === id)?.secid ?? id);
  const found = deficits(ladder, secid);
  const ahead = ladder.days.filter((d) => d.date >= ladder.today);
  // Деньги сегодня — на конец сегодняшнего дня или последнего дня до него.
  const now = [...ladder.days].reverse().find((d) => d.date <= ladder.today);
  const lowest = ahead.reduce<LadderDay | undefined>(
    (low, d) => (low === undefined || Number(d.cash) < Number(low.cash) ? d : low),
    undefined,
  );
  const horizon = ladder.days.at(-1)?.date;
  return (
    <>
      <p className="muted">
        Портфели счёта: {ladder.portfolios.map((p) => p.code).join(', ')}. Прогноз от расчёта на{' '}
        {formatDate(ladder.as_of)}.
      </p>
      {ladder.waiting_since && (
        <p className="notice notice-warning">
          Фонд пересчитывается с {formatDate(ladder.waiting_since)} — план по прежнему расчёту.
        </p>
      )}
      <dl className="tiles" aria-label="Итоги плана">
        <div className="tile">
          <dt>Деньги сегодня</dt>
          <dd>{now ? shortMoney(now.cash) : '—'}</dd>
        </div>
        <div className="tile">
          <dt>Минимум до {horizon ? formatDate(horizon).slice(0, 5) : '—'}</dt>
          <dd className={lowest && Number(lowest.cash) < 0 ? 'danger' : undefined}>
            {lowest ? shortMoney(lowest.cash) : '—'}
            {lowest && <div className="subline">на {formatDate(lowest.date).slice(0, 5)}</div>}
          </dd>
        </div>
        <div className="tile">
          <dt>Дефицитов</dt>
          <dd className={found.length ? 'danger' : undefined}>{found.length}</dd>
        </div>
      </dl>
      {ladder.days.some((d) => d.movements.some((m) => m.kind === 'payment_unknown')) && (
        <p className="muted">
          Сумма купона не объявлена — в плане её нет. Купон флоатера получит прогноз по текущей ставке, если в
          справочнике заполнены параметры флоатера: индекс, маржа, капитализация.
        </p>
      )}
      {found.length > 0 && (
        <div role="alert" className="notice notice-error">
          {found.map((text) => (
            <p key={text}>{text} Нужно РЕПО или перенос.</p>
          ))}
        </div>
      )}
      <table className="data plan-table" aria-label="План по расчётам">
        <thead>
          <tr>
            <th>Дата</th>
            <th>Движения</th>
            <th className="num">Деньги на конец дня, ₽</th>
          </tr>
        </thead>
        <tbody>
          {ladder.days.map((d) => (
            <PlanRow key={d.date} day={d} ladder={ladder} secid={secid} />
          ))}
        </tbody>
      </table>
      <Securities ladder={ladder} />
    </>
  );
}

/** Бумага на счёте на конец дня; нет строки — ноль. */
function held(day: LadderDay | undefined, instrumentId: string): number {
  return day?.securities.find((s) => s.instrument_id === instrumentId)?.quantity ?? 0;
}

/** Сколько бумаг пришло и ушло за день — по каждой бумаге, всеми портфелями счёта. */
function moved(day: LadderDay): Map<string, number> {
  const found = new Map<string, number>();
  for (const m of day.movements) {
    if (m.instrument_id !== null && m.quantity) {
      found.set(m.instrument_id, (found.get(m.instrument_id) ?? 0) + m.quantity);
    }
  }
  return found;
}

function PlanRow({
  day: d,
  ladder,
  secid,
}: {
  readonly day: LadderDay;
  readonly ladder: Ladder;
  readonly secid: (id: string | null) => string | null;
}) {
  const past = d.date < ladder.today;
  const current = d.date === ladder.today;
  const stage = !d.trading ? 'выходной' : past ? 'факт' : current ? 'сегодня' : 'план';
  const classes = [past ? 'past' : null, current ? 'next' : null, short(d) ? 'deficit' : null].filter(Boolean);
  // Движение только бумагами (перевод) — меткой бумаги; деньгами — меткой денег.
  const money = d.movements.filter((m) => !(m.quantity && !Number(m.cash)));
  const securities = [...moved(d)];
  return (
    <tr className={classes.join(' ') || undefined}>
      <td>
        {formatDate(d.date).slice(0, 5)} {weekday(d.date)}
        <div className="subline">{stage}</div>
      </td>
      <td>
        {money.length === 0 && securities.length === 0 ? (
          <span className="muted">—</span>
        ) : (
          <>
            {money.map((m, index) => (
              <MovementChip
                key={index}
                movement={m}
                secid={secid}
                forecast={ladder.forecasts.find((f) => f.instrument_id === m.instrument_id && f.date === d.date)}
              />
            ))}
            {securities.map(([id, quantity]) => (
              <SecurityChip key={id} secid={secid(id)} quantity={quantity} balance={held(d, id)} />
            ))}
          </>
        )}
      </td>
      <td className={`num${d.cash.startsWith('-') ? ' danger' : ''}`}>{formatMoney(d.cash)}</td>
    </tr>
  );
}

function MovementChip({
  movement: m,
  secid,
  forecast,
}: {
  readonly movement: LadderMovement;
  readonly secid: (id: string | null) => string | null;
  readonly forecast: CouponForecast | undefined;
}) {
  const cash = m.cash.replace(/[-0.]/g, '') ? `${m.cash.startsWith('-') ? '' : '+'}${formatMoney(m.cash)} ₽` : null;
  const parts = [
    `${m.portfolio_code}: ${movementLabel(m)}`,
    secid(m.instrument_id),
    cash,
    m.kind === 'coupon_forecast' && forecast ? `(${forecastBasis(forecast)})` : null,
  ];
  const direction = m.cash.startsWith('-') ? 'out' : cash ? 'in' : 'none';
  return <span className={`mv mv-${direction}`}>{parts.filter(Boolean).join(' ')}</span>;
}

/** Бумаги дня: сколько пришло или ушло и сколько стало; не хватает — красным. */
function SecurityChip({
  secid,
  quantity,
  balance,
}: {
  readonly secid: string | null;
  readonly quantity: number;
  readonly balance: number;
}) {
  const after = balance < 0 ? `не хватает ${formatQuantity(-balance)} шт.` : `${formatQuantity(balance)} шт.`;
  return (
    <span className={`mv mv-security${balance < 0 ? ' mv-short' : ''}`}>
      {secid} {quantity > 0 ? '+' : ''}
      {formatQuantity(quantity)} → {after}
    </span>
  );
}

interface SecurityLine {
  readonly id: string;
  readonly secid: string;
  readonly shortName: string;
  readonly now: number | null;
  readonly moves: readonly (readonly [string, number])[];
  readonly low: number;
  readonly lowDate: string | undefined;
}

/**
 * Бумаги в плане: сколько сейчас, когда и сколько придёт или уйдёт, минимум с датой.
 * Бумаги без движений и дефицита свёрнуты: в портфеле их десятки.
 */
function Securities({ ladder }: { readonly ladder: Ladder }) {
  const [all, setAll] = useState(false);
  const now = [...ladder.days].reverse().find((d) => d.date <= ladder.today);
  const ahead = ladder.days.filter((d) => d.date >= ladder.today);
  const lines: SecurityLine[] = ladder.instruments.map((i) => {
    const moves = ladder.days.flatMap((d) => {
      const quantity = moved(d).get(i.instrument_id);
      return quantity ? [[d.date, quantity] as const] : [];
    });
    const lowest = ahead.reduce<LadderDay | undefined>(
      (low, d) => (low === undefined || held(d, i.instrument_id) < held(low, i.instrument_id) ? d : low),
      undefined,
    );
    return {
      id: i.instrument_id,
      secid: i.secid,
      shortName: i.short_name,
      now: now ? held(now, i.instrument_id) : null,
      moves,
      low: held(lowest, i.instrument_id),
      lowDate: lowest?.date,
    };
  });
  const active = lines.filter((l) => l.moves.length > 0 || l.low < 0);
  const quiet = lines.length - active.length;
  if (lines.length === 0) {
    return null;
  }
  return (
    <>
      <h3 className="plan-subhead">Бумаги в плане</h3>
      <table className="data plan-securities" aria-label="Бумаги в плане">
        <thead>
          <tr>
            <th>Бумага</th>
            <th className="num">Сейчас, шт.</th>
            <th>Движения</th>
            <th className="num">Минимум, шт.</th>
          </tr>
        </thead>
        <tbody>
          {(all ? lines : active).map((l) => (
            <tr key={l.id}>
              <td>
                <span className="tag">{l.shortName}</span>
                <div className="mono">{l.secid}</div>
              </td>
              <td className="num">{l.now === null ? '—' : formatQuantity(l.now)}</td>
              <td>
                {l.moves.length === 0 ? (
                  <span className="muted">—</span>
                ) : (
                  l.moves.map(([date, quantity]) => (
                    <span key={date} className={`mv mv-${quantity > 0 ? 'in' : 'out'}`}>
                      {formatDate(date).slice(0, 5)} {quantity > 0 ? '+' : ''}
                      {formatQuantity(quantity)}
                    </span>
                  ))
                )}
              </td>
              <td className={`num${l.low < 0 ? ' danger' : ''}`}>
                {formatQuantity(l.low)}
                {l.lowDate && (
                  <div className="subline">
                    {l.low < 0 ? `${formatDate(l.lowDate).slice(0, 5)} · дефицит` : `на ${formatDate(l.lowDate).slice(0, 5)}`}
                  </div>
                )}
              </td>
            </tr>
          ))}
          {quiet > 0 && (
            <tr className="quiet-row">
              <td colSpan={4}>
                <button type="button" className="link-button" onClick={() => setAll(!all)}>
                  {all
                    ? 'Свернуть бумаги без движений'
                    : `Ещё ${plural(quiet, 'бумага', 'бумаги', 'бумаг')} без движений в плане — показать`}
                </button>
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}
