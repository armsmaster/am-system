import { ErrorNote, useAction } from '@am/ui';
import { useState, type FormEvent } from 'react';

import type { Context } from '../App';
import type { ExpenseKind, FeeKind, Operation, OperationInput } from '../api';
import { decimalInput, EXPENSE_LABELS, FEE_LABELS, moscowToday, OPERATION_LABELS } from '../format';
import {
  GROUP_HINTS,
  GROUP_KINDS,
  GROUP_LABELS,
  GROUP_OF,
  KIND_SHORT,
  SUBMIT_LABELS,
  type FormGroup,
  type FormKind,
} from '../operations';
import { OperationHints } from './OperationHints';

export interface Draft {
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

const GROUPS = Object.keys(GROUP_KINDS) as FormGroup[];
const EXPENSE_KINDS = Object.keys(EXPENSE_LABELS) as ExpenseKind[];
const FEE_KINDS = Object.keys(FEE_LABELS) as FeeKind[];

/** Последний день прошлого месяца: вознаграждение обычно платят за прошлый месяц. */
function lastMonthEnd(today: string): string {
  const first = new Date(`${today.slice(0, 8)}01T00:00:00Z`);
  first.setUTCDate(0);
  return first.toISOString().slice(0, 10);
}

/** Число из базы — как его вводят: без лишних нулей после запятой. */
function plain(value: string | null): string {
  return value === null ? '' : String(Number(value)).replace('.', ',');
}

function blank(today: string, fundId: string, physical: readonly { id: string }[]): Draft {
  return {
    kind: 'unit_issue',
    fund_id: fundId,
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
  };
}

/** Исправление: форма заполнена значениями операции. */
function fromOperation(operation: Operation, base: Draft): Draft {
  const transfer = GROUP_OF[operation.kind] === 'transfers';
  return {
    ...base,
    kind: operation.kind as FormKind,
    fund_id: operation.fund_id,
    portfolio_id: transfer ? operation.portfolio_id : base.portfolio_id,
    receiver_id: operation.receiver_id ?? base.receiver_id,
    paying_id: GROUP_OF[operation.kind] === 'expenses' ? operation.portfolio_id : '',
    op_date: operation.op_date,
    units: plain(operation.units),
    amount: plain(operation.amount),
    planned_date: operation.planned_date ?? base.planned_date,
    expense_kind: operation.expense_kind ?? base.expense_kind,
    fee_kind: operation.fee_kind ?? base.fee_kind,
    period_end: operation.period_end ?? base.period_end,
    instrument_id: operation.instrument_id ?? '',
    quantity: operation.quantity === null ? '' : String(operation.quantity),
    comment: '',
  };
}

export function body(draft: Draft): OperationInput {
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
 * Операция фонда или перевод (решение 175): сначала группа и вид, потом поля.
 * Паи и вознаграждение проводятся в казначейский портфель фонда (решение 35), расход —
 * в портфель, с чьего счёта платят (решение 122); переводят внутри фонда (решение 37).
 * С ``initial`` — исправление: вид и фонд прежние, причина обязательна.
 */
export function OperationForm({
  context,
  defaultFund,
  operations,
  initial,
  onSaved,
  onCancel,
}: {
  readonly context: Context;
  /** Фонд, открытый на странице. */
  readonly defaultFund?: string | undefined;
  /** Операции фонда — для подсказки по невыплаченным расходам. */
  readonly operations: readonly Operation[];
  readonly initial?: Operation | undefined;
  readonly onSaved: (operation: Operation) => void;
  readonly onCancel: () => void;
}) {
  const funds = context.portfolios.filter((p) => p.is_fund);
  const physical = context.portfolios.filter((p) => p.kind === 'physical');
  const [draft, setDraft] = useState<Draft>(() => {
    const base = blank(moscowToday(), defaultFund ?? funds[0]?.id ?? '', physical);
    return initial ? fromOperation(initial, base) : base;
  });
  const { run, error, busy } = useAction(() => undefined);
  const group = GROUP_OF[draft.kind] as FormGroup;
  const amending = initial !== undefined;

  function set<K extends keyof Draft>(field: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    let saved: Operation | undefined;
    const ok = await run(async () => {
      saved = initial
        ? await context.api.amendOperation(initial.id, body(draft))
        : await context.api.enterOperation(body(draft));
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
    <form className="drawer-form operation-form" onSubmit={(e) => void submit(e)} aria-label={amending ? 'Исправление операции' : 'Новая операция'}>
      {amending ? (
        <p className="hint">
          Исправление: {OPERATION_LABELS[draft.kind]}. Вид и фонд не меняются — для этого операцию отменяют и проводят
          новую.
        </p>
      ) : (
        <>
          <fieldset className="option-cards operation-groups">
            <legend>Что проводим</legend>
            {GROUPS.map((value) => (
              <label key={value} className="option-card">
                <input
                  type="radio"
                  name="group"
                  checked={group === value}
                  onChange={() => set('kind', GROUP_KINDS[value][0]!)}
                />
                <span>
                  {GROUP_LABELS[value]}
                  <span className="hint">{GROUP_HINTS[value]}</span>
                </span>
              </label>
            ))}
          </fieldset>
          {GROUP_KINDS[group].length > 1 && (
            <fieldset className="segmented-field">
              <legend>Операция</legend>
              <div className="segmented-choice">
                {GROUP_KINDS[group].map((kind) => (
                  <label key={kind}>
                    <input type="radio" name="kind" checked={draft.kind === kind} onChange={() => set('kind', kind)} />
                    <span>{KIND_SHORT[kind]}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          )}
        </>
      )}
      {group !== 'transfers' && (
        <label>
          Фонд
          <select value={draft.fund_id} required disabled={amending} onChange={(e) => set('fund_id', e.target.value)}>
            {funds.map((p) => (
              <option key={p.id} value={p.id}>
                {p.code} — {p.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {group === 'transfers' && (
        <>
          {portfolioSelect('Откуда', 'portfolio_id')}
          {portfolioSelect('Куда', 'receiver_id')}
        </>
      )}
      <label>
        {group === 'units' ? 'Дата операции' : 'Дата'}
        <input type="date" value={draft.op_date} required onChange={(e) => set('op_date', e.target.value)} />
      </label>
      {group === 'units' && (
        <>
          <label>
            Плановая дата расчёта
            <input type="date" value={draft.planned_date} required onChange={(e) => set('planned_date', e.target.value)} />
          </label>
          <label>
            Паи, шт.
            <input inputMode="decimal" value={draft.units} required onChange={(e) => set('units', e.target.value)} />
          </label>
        </>
      )}
      {group === 'expenses' && (
        <>
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
        </>
      )}
      {group === 'fees' && (
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
            <input type="date" value={draft.period_end} required onChange={(e) => set('period_end', e.target.value)} />
          </label>
        </>
      )}
      {draft.kind === 'security_transfer' ? (
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
      <OperationHints context={context} draft={draft} operations={operations} except={initial ?? null} />
      <label>
        {amending ? 'Почему исправляете' : 'Комментарий'}
        <input
          value={draft.comment}
          required={amending}
          minLength={amending ? 3 : undefined}
          onChange={(e) => set('comment', e.target.value)}
        />
      </label>
      <ErrorNote message={error} />
      <div className="form-actions">
        <button type="submit" className="primary" disabled={busy}>
          {amending ? 'Сохранить исправление' : SUBMIT_LABELS[draft.kind]}
        </button>
        <button type="button" onClick={onCancel}>
          Отмена
        </button>
      </div>
    </form>
  );
}
