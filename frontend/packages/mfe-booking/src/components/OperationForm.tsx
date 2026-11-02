import { ErrorNote, useAction } from '@am/ui';
import { useState, type FormEvent } from 'react';

import type { Context } from '../App';
import type { ExpenseKind, FeeKind, Operation, OperationInput, OperationKind } from '../api';
import { decimalInput, EXPENSE_LABELS, FEE_LABELS, moscowToday, OPERATION_LABELS } from '../format';

/** Взнос по РЕПО вводят из карточки сделки — в этой форме его нет. */
type FormKind = Exclude<OperationKind, 'repo_contribution'>;

interface Draft {
  kind: FormKind;
  fund_id: string;
  portfolio_id: string;
  receiver_id: string;
  paying_id: string;
  op_date: string;
  units: string;
  amount: string;
  planned_date: string;
  expense_kind: ExpenseKind;
  fee_kind: FeeKind;
  period_end: string;
  instrument_id: string;
  quantity: string;
  comment: string;
}

const KINDS = (Object.keys(OPERATION_LABELS) as OperationKind[]).filter(
  (kind): kind is FormKind => kind !== 'repo_contribution',
);
const EXPENSE_KINDS = Object.keys(EXPENSE_LABELS) as ExpenseKind[];
const FEE_KINDS = Object.keys(FEE_LABELS) as FeeKind[];

/** Последний день прошлого месяца: вознаграждение обычно платят за прошлый месяц. */
function lastMonthEnd(today: string): string {
  const first = new Date(`${today.slice(0, 8)}01T00:00:00Z`);
  first.setUTCDate(0);
  return first.toISOString().slice(0, 10);
}

function group(kind: FormKind): 'units' | 'expense' | 'fee' | 'cash' | 'security' {
  if (kind === 'unit_issue' || kind === 'unit_redemption') {
    return 'units';
  }
  if (kind === 'fee_payment') {
    return 'fee';
  }
  if (kind === 'expense_accrual' || kind === 'expense_payment') {
    return 'expense';
  }
  return kind === 'cash_transfer' ? 'cash' : 'security';
}

function body(draft: Draft): OperationInput {
  const common = { op_date: draft.op_date, comment: draft.comment.trim() || null };
  const { kind } = draft;
  if (kind === 'unit_issue' || kind === 'unit_redemption') {
    return {
      ...common,
      kind,
      fund_id: draft.fund_id,
      units: decimalInput(draft.units),
      amount: decimalInput(draft.amount),
      planned_date: draft.planned_date,
    };
  }
  if (kind === 'expense_accrual' || kind === 'expense_payment') {
    return {
      ...common,
      kind,
      fund_id: draft.fund_id,
      portfolio_id: draft.paying_id || null,
      expense_kind: draft.expense_kind,
      amount: decimalInput(draft.amount),
    };
  }
  if (kind === 'fee_payment') {
    return {
      ...common,
      kind,
      fund_id: draft.fund_id,
      fee_kind: draft.fee_kind,
      period_end: draft.period_end,
      amount: decimalInput(draft.amount),
    };
  }
  if (kind === 'cash_transfer') {
    return {
      ...common,
      kind,
      portfolio_id: draft.portfolio_id,
      receiver_id: draft.receiver_id,
      amount: decimalInput(draft.amount),
    };
  }
  return {
    ...common,
    kind,
    portfolio_id: draft.portfolio_id,
    receiver_id: draft.receiver_id,
    instrument_id: draft.instrument_id,
    quantity: Number(draft.quantity),
  };
}

/**
 * Операция фонда или перевод. Паи и вознаграждение проводятся в казначейский
 * портфель фонда (решение 35), расход — в портфель, с чьего счёта платят, по умолчанию
 * тоже в казначейский (решение 122); переводят только между портфелями одного фонда
 * (решение 37).
 */
export function OperationForm({
  context,
  onSaved,
  onCancel,
}: {
  readonly context: Context;
  readonly onSaved: (operation: Operation) => void;
  readonly onCancel: () => void;
}) {
  const funds = context.portfolios.filter((p) => p.is_fund);
  const physical = context.portfolios.filter((p) => p.kind === 'physical');
  const today = moscowToday();
  const [draft, setDraft] = useState<Draft>({
    kind: 'unit_issue',
    fund_id: funds[0]?.id ?? '',
    portfolio_id: physical[0]?.id ?? '',
    receiver_id: physical[1]?.id ?? '',
    paying_id: '',
    op_date: today,
    units: '',
    amount: '',
    planned_date: today,
    expense_kind: 'specdep',
    fee_kind: 'management',
    period_end: lastMonthEnd(today),
    instrument_id: '',
    quantity: '',
    comment: '',
  });
  const { run, error, busy } = useAction(() => undefined);
  const kind = group(draft.kind);

  function set<K extends keyof Draft>(field: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    let saved: Operation | undefined;
    const ok = await run(async () => {
      saved = await context.api.enterOperation(body(draft));
    });
    if (ok && saved) {
      onSaved(saved);
    }
  }

  const portfolioSelect = (label: string, field: 'portfolio_id' | 'receiver_id') => (
    <label>
      {label}
      <select value={draft[field]} required onChange={(e) => set(field, e.target.value)}>
        <option value="">— выберите —</option>
        {physical.map((p) => (
          <option key={p.id} value={p.id}>
            {p.code}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <form onSubmit={(e) => void submit(e)} aria-label="Новая операция">
      <h3>Новая операция</h3>
      <div className="fields">
        <label>
          Операция
          <select value={draft.kind} onChange={(e) => set('kind', e.target.value as FormKind)}>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {OPERATION_LABELS[k]}
              </option>
            ))}
          </select>
        </label>
        {(kind === 'units' || kind === 'expense' || kind === 'fee') && (
          <label>
            Фонд
            <select value={draft.fund_id} required onChange={(e) => set('fund_id', e.target.value)}>
              {funds.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.code} — {p.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {(kind === 'cash' || kind === 'security') && (
          <>
            {portfolioSelect('Откуда', 'portfolio_id')}
            {portfolioSelect('Куда', 'receiver_id')}
          </>
        )}
        <label>
          Дата
          <input type="date" value={draft.op_date} required onChange={(e) => set('op_date', e.target.value)} />
        </label>
        {kind === 'units' && (
          <>
            <label>
              Паи, шт.
              <input inputMode="decimal" value={draft.units} required onChange={(e) => set('units', e.target.value)} />
            </label>
            <label>
              Плановая дата расчёта
              <input
                type="date"
                value={draft.planned_date}
                required
                onChange={(e) => set('planned_date', e.target.value)}
              />
            </label>
          </>
        )}
        {kind === 'expense' && (
          <label>
            С чьего счёта платят
            <select value={draft.paying_id} onChange={(e) => set('paying_id', e.target.value)}>
              <option value="">казначейский портфель фонда</option>
              {physical
                .filter((p) => p.fund_id === draft.fund_id)
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.code}
                  </option>
                ))}
            </select>
          </label>
        )}
        {kind === 'expense' && (
          <label>
            Вид расхода
            <select value={draft.expense_kind} onChange={(e) => set('expense_kind', e.target.value as ExpenseKind)}>
              {EXPENSE_KINDS.map((k) => (
                <option key={k} value={k}>
                  {EXPENSE_LABELS[k]}
                </option>
              ))}
            </select>
          </label>
        )}
        {kind === 'fee' && (
          <>
            <label>
              Вид комиссии
              <select value={draft.fee_kind} onChange={(e) => set('fee_kind', e.target.value as FeeKind)}>
                {FEE_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {FEE_LABELS[k]}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Оплачено по
              <input
                type="date"
                value={draft.period_end}
                required
                onChange={(e) => set('period_end', e.target.value)}
              />
            </label>
          </>
        )}
        {kind === 'security' ? (
          <>
            <label>
              Бумага
              <select value={draft.instrument_id} required onChange={(e) => set('instrument_id', e.target.value)}>
                <option value="">— выберите —</option>
                {context.instruments.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.secid} — {i.short_name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Количество, шт.
              <input inputMode="numeric" value={draft.quantity} required onChange={(e) => set('quantity', e.target.value)} />
            </label>
          </>
        ) : (
          <label>
            Сумма, ₽
            <input inputMode="decimal" value={draft.amount} required onChange={(e) => set('amount', e.target.value)} />
          </label>
        )}
        <label>
          Комментарий
          <input value={draft.comment} onChange={(e) => set('comment', e.target.value)} />
        </label>
      </div>
      <button type="submit" disabled={busy}>
        Провести операцию
      </button>{' '}
      <button type="button" onClick={onCancel}>
        Отмена
      </button>
      <ErrorNote message={error} />
    </form>
  );
}
