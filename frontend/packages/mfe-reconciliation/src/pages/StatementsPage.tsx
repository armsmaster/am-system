import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';
import { useCallback, useState } from 'react';

import type { Context } from '../App';
import type { Batch, MovementKind, Received, Statement } from '../api';
import {
  BY_SECURITY,
  BY_TRADE,
  decimalInput,
  formatDate,
  formatMoney,
  formatTime,
  moscowToday,
  MOVEMENT_LABELS,
  movementTitle,
  previousDay,
} from '../format';

interface Row {
  readonly kind: MovementKind;
  readonly amount: string;
  readonly trade: string;
  readonly security: string;
  readonly comment: string;
}

const EMPTY_ROW: Row = { kind: 'other', amount: '', trade: '', security: '', comment: '' };

const RESULTS: Readonly<Record<Received['result'], string>> = {
  created: 'Выписка принята.',
  replaced: 'Выписка принята и заменила прежнюю за этот день.',
  unchanged: 'Такая выписка уже есть — ничего не изменилось.',
};

/** Выписки по счёту за день: история и ввод вручную (решение 47). */
export function StatementsPage({ context }: { readonly context: Context }) {
  const { api, accounts, isOperations } = context;
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? '');
  const [date, setDate] = useState(() => previousDay(moscowToday()));
  const load = useCallback(() => api.statements(accountId, date), [api, accountId, date]);
  const [state, reload] = useLoad(load);

  if (!accounts.length) {
    return <p className="muted">Нет счетов, к которым у вас есть доступ.</p>;
  }
  return (
    <>
      <div className="toolbar">
        <label>
          Счёт
          <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.code} — {a.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          День
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
      </div>
      <LoadedView state={state}>{(found) => <History statements={found} context={context} />}</LoadedView>
      {isOperations && <StatementForm key={`${accountId}-${date}`} accountId={accountId} date={date} context={context} onDone={reload} />}
    </>
  );
}

function History({ statements, context }: { readonly statements: readonly Statement[]; readonly context: Context }) {
  const [batch, setBatch] = useState<Batch | null>(null);
  const { run, error } = useAction(() => undefined);

  if (!statements.length) {
    return <p className="muted">Выписок за этот день нет.</p>;
  }
  return (
    <>
      <ErrorNote message={error} />
      {statements.map((s, index) => (
        <article key={s.id} aria-label={index === 0 ? 'Действующая выписка' : 'Прежняя выписка'}>
          <h2>{index === 0 ? 'Действующая выписка' : 'Прежняя выписка'}</h2>
          <p className="muted">
            {s.source === 'manual' ? 'введена вручную' : `источник ${s.source}`}, {s.received_by},{' '}
            {formatTime(s.received_at)}
          </p>
          <p>
            Остаток на начало {formatMoney(s.opening_cash)} ₽, на конец {formatMoney(s.closing_cash)} ₽. Сделок:{' '}
            {s.trades.length}.
          </p>
          <ul>
            {s.movements.map((m, i) => (
              <li key={i}>
                {movementTitle(m.kind, m.code, m.trade)}: {formatMoney(m.amount)} ₽{m.comment && ` — ${m.comment}`}
              </li>
            ))}
          </ul>
          <button type="button" onClick={() => void run(async () => setBatch(await context.api.batch(s.batch_id!)))}>
            Пакет, как пришёл
          </button>
        </article>
      ))}
      {batch && (
        <pre aria-label="Пакет выписок">
          {JSON.stringify(batch.payload, null, 2)}
        </pre>
      )}
    </>
  );
}

function StatementForm({
  accountId,
  date,
  context,
  onDone,
}: {
  readonly accountId: string;
  readonly date: string;
  readonly context: Context;
  readonly onDone: () => void;
}) {
  const [opening, setOpening] = useState('');
  const [closing, setClosing] = useState('');
  const [rows, setRows] = useState<readonly Row[]>([]);
  const [result, setResult] = useState<string | null>(null);
  const { run, error, busy } = useAction(onDone);
  const change = (index: number, patch: Partial<Row>) =>
    setRows(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  const submit = () =>
    run(async () => {
      const received = await context.api.enter({
        account_id: accountId,
        date,
        opening_cash: decimalInput(opening),
        closing_cash: decimalInput(closing),
        movements: rows.map((row) => ({
          kind: row.kind,
          amount: decimalInput(row.amount),
          // Поле, которого у вида нет, не уходит, даже если его заполнили до смены вида.
          trade: BY_TRADE.has(row.kind) || BY_SECURITY.has(row.kind) ? row.trade.trim() || null : null,
          security: BY_SECURITY.has(row.kind) ? row.security.trim() || null : null,
          comment: row.comment.trim() || null,
        })),
      });
      setResult(RESULTS[received.result]);
    });

  return (
    <form
      aria-label="Ввод выписки"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <h2>Ввести выписку за {formatDate(date)}</h2>
      <p className="muted">
        По выписке банка или отчёту брокера. Остаток на начало плюс движения должны дать остаток на конец. День без
        движений — остатки одинаковые, движений нет.
      </p>
      <label>
        Остаток на начало, ₽
        <input value={opening} onChange={(e) => setOpening(e.target.value)} inputMode="decimal" />
      </label>
      <label>
        Остаток на конец, ₽
        <input value={closing} onChange={(e) => setClosing(e.target.value)} inputMode="decimal" />
      </label>
      {rows.map((row, index) => (
        <fieldset key={index} aria-label={`Движение ${index + 1}`}>
          <label>
            Вид
            <select value={row.kind} onChange={(e) => change(index, { kind: e.target.value as MovementKind })}>
              {Object.entries(MOVEMENT_LABELS).map(([kind, label]) => (
                <option key={kind} value={kind}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Сумма, ₽ (минус — ушли)
            <input value={row.amount} onChange={(e) => change(index, { amount: e.target.value })} inputMode="decimal" />
          </label>
          {(BY_TRADE.has(row.kind) || BY_SECURITY.has(row.kind)) && (
            <label>
              Номер сделки
              <input value={row.trade} onChange={(e) => change(index, { trade: e.target.value })} />
            </label>
          )}
          {BY_SECURITY.has(row.kind) && (
            <label>
              Бумага
              <input value={row.security} onChange={(e) => change(index, { security: e.target.value })} />
            </label>
          )}
          <label>
            Комментарий
            <input value={row.comment} onChange={(e) => change(index, { comment: e.target.value })} />
          </label>
          <button type="button" onClick={() => setRows(rows.filter((_, i) => i !== index))}>
            Убрать движение
          </button>
        </fieldset>
      ))}
      <button type="button" onClick={() => setRows([...rows, EMPTY_ROW])}>
        Добавить движение
      </button>
      <ErrorNote message={error} />
      {result && <p className="notice">{result}</p>}
      <button type="submit" disabled={busy || !opening.trim() || !closing.trim()}>
        Ввести выписку
      </button>
    </form>
  );
}
