import { errorMessage } from '@am/platform';
import { Drawer, FilterChips, LoadedView, useLoad, type Choice } from '@am/ui';
import { useCallback, useState, type FormEvent, type KeyboardEvent } from 'react';
import { useSearchParams } from 'react-router';

import type { Context } from '../App';
import type { Operation, Portfolio } from '../api';
import { OperationCard } from '../components/OperationCard';
import { OperationForm } from '../components/OperationForm';
import { KindPill, SettlementPill } from '../components/OperationPills';
import {
  dayTitle,
  EXPENSE_LABELS,
  FEE_LABELS,
  formatDate,
  formatMoney,
  formatPrice,
  formatQuantity,
  moscowToday,
  OPERATION_LABELS,
  shortMoney,
} from '../format';
import {
  GROUP_LABELS,
  GROUP_OF,
  isPending,
  matches,
  OP_PERIOD_LABELS,
  opRanges,
  opTotals,
  type OpFilter,
  type OpGroup,
  type OpPeriod,
} from '../operations';

type Panel = { readonly kind: 'new' } | { readonly kind: 'settle' } | { readonly kind: 'card'; readonly id: string };

const GROUPS: readonly OpGroup[] = ['units', 'expenses', 'fees', 'transfers', 'repo'];

/** Портфель и период — в адресе страницы, как в «Сделках»; по умолчанию — первый фонд и месяц. */
function useFilter(portfolios: readonly Portfolio[]) {
  const [params, setParams] = useSearchParams();
  const today = moscowToday();
  const fallback = portfolios.find((p) => p.is_fund) ?? portfolios[0];
  const portfolioId = params.get('portfolio') ?? fallback?.id ?? '';
  const [monthFrom, monthTill] = opRanges(today).month;
  const from = params.get('from') ?? monthFrom;
  const till = params.get('till') ?? monthTill;
  const update = (changes: { portfolio?: string; from?: string; till?: string }) =>
    setParams({
      portfolio: changes.portfolio ?? portfolioId,
      from: changes.from ?? from,
      till: changes.till ?? till,
    });
  return { today, portfolioId, from, till, update };
}

/**
 * Операции фонда и переводы (решение 175): фильтры и итоги периода, список по дням.
 * Строка открывает операцию в панели справа; там же — ввод, исправление, отметка
 * расчёта и отмена. Проводит и меняет операции сопровождение.
 */
export function OperationsPage({ context }: { readonly context: Context }) {
  const { today, portfolioId, from, till, update } = useFilter(context.portfolios);
  const load = useCallback(() => context.api.operations(portfolioId), [context.api, portfolioId]);
  const [state, reload] = useLoad(load);
  const [filter, setFilter] = useState<OpFilter>('all');
  const [picking, setPicking] = useState(false);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [saved, setSaved] = useState<Operation | null>(null);

  if (context.portfolios.length === 0) {
    return <p className="muted">Нет доступных портфелей: доступ выдаёт администратор.</p>;
  }
  const selected = context.portfolios.find((p) => p.id === portfolioId);
  const fund = selected?.is_fund ? selected.id : selected?.fund_id ?? undefined;
  const quick = opRanges(today);
  const matched = (Object.keys(quick) as Exclude<OpPeriod, 'custom'>[]).find(
    (key) => quick[key][0] === from && quick[key][1] === till,
  );
  const period: OpPeriod = picking || matched === undefined ? 'custom' : matched;
  const periods: Choice<OpPeriod>[] = (Object.keys(OP_PERIOD_LABELS) as OpPeriod[]).map((value) => ({
    value,
    label: OP_PERIOD_LABELS[value],
  }));
  const choosePeriod = (next: OpPeriod) => {
    setPicking(next === 'custom');
    if (next !== 'custom') {
      const [since, last] = quick[next];
      update({ from: since, till: last });
    }
  };
  const close = () => setPanel(null);

  return (
    <>
      <div className="toolbar trades-toolbar">
        <select aria-label="Портфель" value={portfolioId} onChange={(e) => update({ portfolio: e.target.value })}>
          {context.portfolios.map((p) => (
            <option key={p.id} value={p.id}>
              {p.code} — {p.name}
            </option>
          ))}
        </select>
        <FilterChips label="Период" choices={periods} value={period} onChange={choosePeriod} />
        {period === 'custom' && (
          <span className="period-dates">
            <input
              type="date"
              aria-label="С даты"
              value={from}
              max={till}
              onChange={(e) => e.target.value && update({ from: e.target.value })}
            />
            —
            <input
              type="date"
              aria-label="По дату"
              value={till}
              min={from}
              onChange={(e) => e.target.value && update({ till: e.target.value })}
            />
          </span>
        )}
        {context.isOperations && (
          <span className="toolbar-end">
            <button type="button" className="primary" onClick={() => setPanel({ kind: 'new' })}>
              Новая операция
            </button>
          </span>
        )}
      </div>
      {saved && <SavedNote operation={saved} />}
      <LoadedView state={state}>
        {(all) => {
          const inPeriod = all.filter((o) => o.op_date >= from && o.op_date <= till);
          // Ждущие расчёта видны всегда: просроченный расчёт не прячется за периодом.
          const pendingAll = all.filter((o) => isPending(o, today));
          const shown = (filter === 'pending' ? pendingAll : inPeriod).filter((o) => matches(o, filter, today));
          const count = (group: OpGroup) => inPeriod.filter((o) => GROUP_OF[o.kind] === group).length;
          const chips: Choice<OpFilter>[] = [
            { value: 'all', label: 'Все', count: inPeriod.length },
            ...GROUPS.filter((g) => g !== 'repo' || count('repo') > 0).map((g) => ({
              value: g,
              label: GROUP_LABELS[g],
              count: count(g),
            })),
            { value: 'pending', label: 'Ждут расчёта', count: pendingAll.length },
          ];
          return (
            <>
              <Totals operations={inPeriod} pending={pendingAll} today={today} />
              <div className="toolbar">
                <FilterChips label="Вид операции" choices={chips} value={filter} onChange={setFilter} />
                {context.isOperations && pendingAll.length > 0 && (
                  <button type="button" className="toolbar-end" onClick={() => setPanel({ kind: 'settle' })}>
                    Подтвердить расчёт…
                  </button>
                )}
              </div>
              <OperationsTable
                operations={shown}
                today={today}
                empty={filter === 'all' ? 'Операций за этот период нет.' : 'Таких операций за этот период нет.'}
                onOpen={(o) => setPanel({ kind: 'card', id: o.id })}
              />
              <Drawer title="Новая операция" open={panel?.kind === 'new'} onClose={close}>
                <OperationForm
                  context={context}
                  defaultFund={fund}
                  operations={all}
                  onSaved={(operation) => {
                    setSaved(operation);
                    close();
                    reload();
                  }}
                  onCancel={close}
                />
              </Drawer>
              <Drawer title="Подтвердить расчёт по паям" open={panel?.kind === 'settle'} onClose={close}>
                <BulkSettle context={context} operations={pendingAll} today={today} onDone={reload} />
              </Drawer>
              {panel?.kind === 'card' && (
                <Drawer title="Операция" open onClose={close}>
                  <OperationCard key={panel.id} id={panel.id} context={context} operations={all} onChanged={reload} />
                </Drawer>
              )}
            </>
          );
        }}
      </LoadedView>
    </>
  );
}

function SavedNote({ operation }: { readonly operation: Operation }) {
  return (
    <div role="status">
      <p className="notice">
        Проведено: {OPERATION_LABELS[operation.kind]} от {formatDate(operation.op_date)}.
      </p>
      {operation.warnings.map((w) => (
        <p key={w} className="notice notice-warning">
          {w}
        </p>
      ))}
    </div>
  );
}

/** Итоги периода без отменённых; «К расчёту» — все ждущие, какой бы ни была дата. */
function Totals({
  operations,
  pending,
  today,
}: {
  readonly operations: readonly Operation[];
  readonly pending: readonly Operation[];
  readonly today: string;
}) {
  const totals = opTotals(operations, today);
  const waiting = opTotals(pending, today);
  return (
    <dl className="tiles trade-totals" aria-label="Итоги периода">
      <div className="tile">
        <dt>Выдано паёв</dt>
        <dd>{shortMoney(totals.issued)}</dd>
      </div>
      <div className="tile">
        <dt>Погашено паёв</dt>
        <dd>{shortMoney(totals.redeemed)}</dd>
      </div>
      <div className="tile">
        <dt>Выплачено расходов и вознаграждения</dt>
        <dd>{shortMoney(totals.paid)}</dd>
      </div>
      <div className={`tile${waiting.pending > 0 ? ' tile-warning' : ''}`}>
        <dt>Ждут расчёта</dt>
        <dd>
          {waiting.pending}
          {waiting.pending > 0 && <div className="subline">{shortMoney(waiting.pendingAmount)}</div>}
        </dd>
      </div>
    </dl>
  );
}

function OperationsTable({
  operations,
  today,
  empty,
  onOpen,
}: {
  readonly operations: readonly Operation[];
  readonly today: string;
  readonly empty: string;
  readonly onOpen: (operation: Operation) => void;
}) {
  if (operations.length === 0) {
    return <p className="muted">{empty}</p>;
  }
  const latest = [...operations].sort((a, b) => b.op_date.localeCompare(a.op_date));
  const dates = [...new Set(latest.map((o) => o.op_date))];
  return (
    <table className="data operations-table" aria-label="Операции">
      <thead>
        <tr>
          <th>Операция</th>
          <th>Портфель</th>
          <th className="num">Паи или бумаги</th>
          <th className="num">Сумма, ₽</th>
          <th>Расчёт</th>
        </tr>
      </thead>
      <tbody>
        {dates.map((date) => [
          <tr key={date} className="day-row">
            <td colSpan={5}>{dayTitle(date)}</td>
          </tr>,
          ...latest
            .filter((o) => o.op_date === date)
            .map((o) => <OperationRow key={o.id} operation={o} today={today} onOpen={onOpen} />),
        ])}
      </tbody>
    </table>
  );
}

/** Подробности под видом: вид расхода, период вознаграждения, бумага, цена за пай. */
function details(o: Operation): string {
  const parts = [
    o.expense_kind && EXPENSE_LABELS[o.expense_kind],
    o.fee_kind && `${FEE_LABELS[o.fee_kind]} по ${formatDate(o.period_end)}`,
    o.direction && (o.direction === 'in' ? 'НКЦ — фонду' : 'фонд — НКЦ'),
    o.secid,
    o.units !== null && `${formatMoney((Number(o.amount) / Number(o.units)).toFixed(2))} ₽ за пай`,
    o.comment,
  ];
  return parts.filter(Boolean).join(' · ');
}

function OperationRow({
  operation: o,
  today,
  onOpen,
}: {
  readonly operation: Operation;
  readonly today: string;
  readonly onOpen: (operation: Operation) => void;
}) {
  const open = () => onOpen(o);
  const key = (event: KeyboardEvent) => {
    if (event.key === 'Enter') {
      open();
    }
  };
  return (
    <tr className={`link-row${o.status === 'cancelled' ? ' cancelled' : ''}`} tabIndex={0} onClick={open} onKeyDown={key}>
      <td>
        <KindPill operation={o} />
        {o.status === 'cancelled' && <span className="pill status-cancelled">отменена</span>}
        {details(o) && <div className="subline">{details(o)}</div>}
      </td>
      <td>{o.receiver_code ? `${o.portfolio_code} → ${o.receiver_code}` : o.portfolio_code}</td>
      <td className="num">
        {o.units !== null ? formatPrice(o.units) : o.quantity !== null ? formatQuantity(o.quantity) : '—'}
      </td>
      <td className="num">{formatMoney(o.amount)}</td>
      <td>
        <SettlementPill operation={o} today={today} />
      </td>
    </tr>
  );
}

/** Несколько расчётов сразу — например, когда регистратор прислал подтверждение. */
function BulkSettle({
  context,
  operations,
  today,
  onDone,
}: {
  readonly context: Context;
  readonly operations: readonly Operation[];
  readonly today: string;
  readonly onDone: () => void;
}) {
  // Список — на момент открытия: подтверждённые остаются с результатом, а не исчезают.
  const [list] = useState(operations);
  const [day, setDay] = useState(today);
  const [chosen, setChosen] = useState<ReadonlySet<string>>(() => new Set(operations.map((o) => o.id)));
  const [results, setResults] = useState<Readonly<Record<string, string>>>({});
  const [busy, setBusy] = useState(false);
  const toggle = (id: string) => {
    const next = new Set(chosen);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    setChosen(next);
  };
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    const outcome: Record<string, string> = {};
    for (const o of list.filter((op) => chosen.has(op.id))) {
      try {
        await context.api.settleOperation(o.id, day);
        outcome[o.id] = 'подтверждён';
      } catch (error) {
        outcome[o.id] = errorMessage(error);
      }
    }
    setResults(outcome);
    setBusy(false);
    onDone();
  }
  return (
    <form className="drawer-form" aria-label="Подтверждение расчёта" onSubmit={(e) => void submit(e)}>
      <label>
        Дата расчёта
        <input type="date" value={day} required onChange={(e) => setDay(e.target.value)} />
      </label>
      <fieldset className="values-field">
        <legend>Операции</legend>
        {list.map((o) => (
          <label key={o.id} className="checkbox">
            <input type="checkbox" checked={chosen.has(o.id)} onChange={() => toggle(o.id)} />
            <span>
              {OPERATION_LABELS[o.kind]} от {formatDate(o.op_date)}: {formatPrice(o.units)} паёв, {formatMoney(o.amount)} ₽
              {results[o.id] && <span className={results[o.id] === 'подтверждён' ? 'bulk-ok' : 'bulk-error'}> — {results[o.id]}</span>}
            </span>
          </label>
        ))}
      </fieldset>
      <button type="submit" className="primary" disabled={busy || chosen.size === 0}>
        Подтвердить расчёт: {chosen.size}
      </button>
    </form>
  );
}
