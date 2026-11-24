import { Drawer, FilterChips, LoadedView, useLoad, type Choice } from '@am/ui';
import { useCallback, useState, type KeyboardEvent } from 'react';
import { useSearchParams } from 'react-router';

import type { Context } from '../App';
import type { Account, Decimal, Ladder, LadderDay } from '../api';
import { PlanChart } from '../components/PlanChart';
import { PlanDay, signedMoney } from '../components/PlanDay';
import {
  formatDate,
  formatMoney,
  formatQuantity,
  monthStart,
  moscowToday,
  plural,
  shiftDay,
  shortMoney,
  sumMoney,
  weekday,
} from '../format';
import {
  held,
  isShort,
  moved,
  outlook,
  planRows,
  securityMarks,
  summarize,
  type DaySummary,
  type PlanItem,
} from '../plan';

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
      <LoadedView state={state}>{(ladder) => <PlanView ladder={ladder} basePath={context.basePath} />}</LoadedView>
    </>
  );
}

/** Дефицит называем в день, когда он появился или вырос: дальше он тянется, пока его не перекроют. */
function deficits(ladder: Ladder, secid: (id: string) => string): string[] {
  return ladder.days.flatMap((d, index) => {
    const previous = ladder.days[index - 1];
    return [
      ...(Number(d.cash) < Math.min(0, Number(previous?.cash ?? 0))
        ? [`Не хватает ${formatMoney(d.cash.slice(1))} ₽ к расчётам ${formatDate(d.date)}.`]
        : []),
      ...d.securities
        .filter((s) => s.quantity < Math.min(0, held(previous, s.instrument_id)))
        .map((s) => `Не хватает ${formatQuantity(-s.quantity)} ${secid(s.instrument_id)} к расчётам ${formatDate(d.date)}.`),
    ];
  });
}

/** Больше строк в ячейке — остальные одной строкой «ещё N»; все — в панели дня. */
const ITEMS_IN_CELL = 5;

function shortDate(iso: string): string {
  return formatDate(iso).slice(0, 5);
}

function PlanView({ ladder, basePath }: { readonly ladder: Ladder; readonly basePath: string }) {
  const [selected, setSelected] = useState<string | null>(null);
  // Бумаги нет в списке плана — показываем её номер, а не пустоту.
  const known = (id: string) => ladder.instruments.find((i) => i.instrument_id === id);
  const secid = (id: string) => known(id)?.secid ?? id;
  const name = (id: string) => known(id)?.short_name ?? id;
  const found = deficits(ladder, secid);
  const summaries = new Map(ladder.days.map((d) => [d.date, summarize(d, ladder)]));
  const opened = ladder.days.find((d) => d.date === selected);
  const open = (day: LadderDay) => setSelected(day.date);
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
      <Tiles ladder={ladder} name={name} />
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
      {ladder.days.length > 0 && <PlanChart ladder={ladder} summaries={summaries} onOpen={open} />}
      <div className="plan-scroll">
        <table className="data plan-table" aria-label="План по расчётам">
          <thead>
            <tr>
              <th>Дата</th>
              <th>Поступило, ₽</th>
              <th>Списано, ₽</th>
              <th className="num">Деньги на конец дня, ₽</th>
              <th>Бумаги</th>
            </tr>
          </thead>
          <tbody>
            {planRows(ladder).map((row) =>
              row.kind === 'quiet' ? (
                <tr key={row.first.date} className={`quiet-row plan-quiet${isShort(row.last) ? ' deficit' : ''}`}>
                  <td colSpan={3}>
                    {row.first === row.last
                      ? `${shortDate(row.first.date)} ${weekday(row.first.date)}`
                      : `${shortDate(row.first.date)}–${shortDate(row.last.date)}`}{' '}
                    · без движений
                  </td>
                  <td className={`num plan-balance${Number(row.last.cash) < 0 ? ' danger' : ''}`}>
                    {formatMoney(row.last.cash)}
                  </td>
                  <td>
                    <SecurityMarks day={row.last} name={name} />
                  </td>
                </tr>
              ) : (
                <PlanRow
                  key={row.day.date}
                  day={row.day}
                  summary={summaries.get(row.day.date)!}
                  ladder={ladder}
                  name={name}
                  onOpen={open}
                />
              ),
            )}
          </tbody>
        </table>
      </div>
      <Securities ladder={ladder} />
      {opened && (
        <Drawer
          title={`Движения ${shortDate(opened.date)} · ${stageOf(opened, ladder)}`}
          open
          onClose={() => setSelected(null)}
          wide
        >
          <PlanDay day={opened} summary={summaries.get(opened.date)!} ladder={ladder} basePath={basePath} />
        </Drawer>
      )}
    </>
  );
}

/** Деньги сегодня, сколько можно разместить до горизонта, дефициты денег и бумаг. */
function Tiles({ ladder, name }: { readonly ladder: Ladder; readonly name: (id: string) => string }) {
  const { now, lowest, horizon, cashShort, securitiesShort } = outlook(ladder);
  const deepest = cashShort.reduce<LadderDay | undefined>(
    (low, d) => (low === undefined || Number(d.cash) < Number(low.cash) ? d : low),
    undefined,
  );
  const [firstShort] = securitiesShort;
  return (
    <dl className="tiles plan-tiles" aria-label="Итоги плана">
      <div className="tile">
        <dt>Деньги сегодня</dt>
        <dd>{now ? shortMoney(now.cash) : '—'}</dd>
      </div>
      <div className="tile">
        <dt>Можно разместить до {horizon ? shortDate(horizon) : '—'}</dt>
        <dd>
          {lowest ? (Number(lowest.cash) > 0 ? shortMoney(lowest.cash) : '0 ₽') : '—'}
          {lowest && <div className="subline">минимум остатка — {shortDate(lowest.date)}</div>}
        </dd>
      </div>
      <div className="tile">
        <dt>Дефицит денег</dt>
        {deepest ? (
          <dd className="danger">
            {shortMoney(deepest.cash)}
            <div className="subline">
              {cashShort.length === 1
                ? shortDate(deepest.date)
                : `${shortDate(cashShort[0]!.date)}–${shortDate(cashShort.at(-1)!.date)} · ${plural(cashShort.length, 'день', 'дня', 'дней')}`}
            </div>
          </dd>
        ) : (
          <dd>нет</dd>
        )}
      </div>
      <div className="tile">
        <dt>Дефицит бумаг</dt>
        {firstShort ? (
          <dd className="danger">
            {plural(securitiesShort.length, 'бумага', 'бумаги', 'бумаг')}
            <div className="subline">
              {name(firstShort.id)} с {shortDate(firstShort.from)}
              {securitiesShort.length > 1 ? ' и другие' : ''}
            </div>
          </dd>
        ) : (
          <dd>нет</dd>
        )}
      </div>
    </dl>
  );
}

function stageOf(day: LadderDay, ladder: Ladder): string {
  if (!day.trading) {
    return 'выходной';
  }
  if (day.date === ladder.today) {
    return 'сегодня';
  }
  return day.date < ladder.today ? 'факт' : 'план';
}

function PlanRow({
  day: d,
  summary,
  ladder,
  name,
  onOpen,
}: {
  readonly day: LadderDay;
  readonly summary: DaySummary;
  readonly ladder: Ladder;
  readonly name: (id: string) => string;
  readonly onOpen: (day: LadderDay) => void;
}) {
  const classes = [
    'link-row',
    d.date < ladder.today ? 'past' : null,
    d.date === ladder.today ? 'next' : null,
    isShort(d) ? 'deficit' : null,
  ];
  const key = (event: KeyboardEvent) => {
    if (event.key === 'Enter') {
      onOpen(d);
    }
  };
  return (
    <tr className={classes.filter(Boolean).join(' ')} tabIndex={0} onClick={() => onOpen(d)} onKeyDown={key}>
      <td className="plan-date">
        {shortDate(d.date)} {weekday(d.date)}
        <div className="subline">{stageOf(d, ladder)}</div>
      </td>
      <td>
        <MoneyCell items={summary.incoming} total={summary.inflow} />
      </td>
      <td>
        <MoneyCell items={summary.outgoing} total={summary.outflow} />
      </td>
      <td className={`num plan-balance${Number(d.cash) < 0 ? ' danger' : ''}`}>
        {formatMoney(d.cash)}
        {Number(d.cash) < 0 && <div className="subline">не хватает</div>}
      </td>
      <td>
        <SecurityMarks day={d} name={name} />
      </td>
    </tr>
  );
}

/** Бумаги дня: что пришло или ушло и сколько стало; не хватает — красной меткой. */
function SecurityMarks({ day, name }: { readonly day: LadderDay; readonly name: (id: string) => string }) {
  return (
    <>
      {securityMarks(day).map((mark) => (
        <span key={mark.id} className={`mv mv-security${mark.balance < 0 ? ' mv-short' : ''}`}>
          {name(mark.id)} {mark.quantity !== 0 && `${mark.quantity > 0 ? '+' : ''}${formatQuantity(mark.quantity)} `}→{' '}
          {mark.balance < 0 ? `не хватает ${formatQuantity(-mark.balance)}` : formatQuantity(mark.balance)} шт.
        </span>
      ))}
    </>
  );
}

/** Итог ячейки сверху, под ним — каждое движение своей строкой в рамке. */
function MoneyCell({ items, total }: { readonly items: readonly PlanItem[]; readonly total: Decimal }) {
  if (items.length === 0) {
    return <span className="muted">—</span>;
  }
  const shown = items.slice(0, ITEMS_IN_CELL);
  const rest = items.slice(ITEMS_IN_CELL);
  const amount = (value: Decimal) => formatMoney(value.replace('-', ''));
  return (
    <>
      <div className={`plan-total${Number(total) > 0 ? ' up' : ''}`}>{signedMoney(total)}</div>
      <ul className="plan-items">
        {shown.map((item) => (
          <li key={item.key} className="plan-item">
            <span className="plan-item-label">
              <span className={`plan-tag plan-tag-${item.category}`}>{item.tag}</span>
              {item.label}
              {item.note && <small>{item.note}</small>}
            </span>
            <span className="plan-item-cash">{amount(item.cash)}</span>
          </li>
        ))}
        {rest.length > 0 && (
          <li className="plan-item plan-more">
            <span className="plan-item-label">ещё {plural(rest.length, 'движение', 'движения', 'движений')}</span>
            <span className="plan-item-cash">{amount(sumMoney(rest.map((i) => i.cash)))}</span>
          </li>
        )}
      </ul>
    </>
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
