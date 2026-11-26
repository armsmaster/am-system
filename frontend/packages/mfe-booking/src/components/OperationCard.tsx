import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';
import { useCallback, useState, type FormEvent } from 'react';

import type { Context } from '../App';
import type { Operation } from '../api';
import {
  EXPENSE_LABELS,
  FEE_LABELS,
  formatDate,
  formatMoney,
  formatPrice,
  formatQuantity,
  formatTime,
  moscowToday,
  OPERATION_LABELS,
} from '../format';
import { GROUP_OF, OPERATION_FIELDS } from '../operations';
import { OperationForm } from './OperationForm';
import { KindPill, SettlementPill } from './OperationPills';

/** Операция в панели справа: поля, история версий, исправление, расчёт, отмена (решение 175). */
export function OperationCard({
  id,
  context,
  operations,
  onChanged,
}: {
  readonly id: string;
  readonly context: Context;
  readonly operations: readonly Operation[];
  readonly onChanged: () => void;
}) {
  const load = useCallback(() => context.api.operation(id), [context.api, id]);
  const [state, reload] = useLoad(load);
  const changed = useCallback(() => {
    reload();
    onChanged();
  }, [reload, onChanged]);
  return (
    <LoadedView state={state}>
      {(operation) => (
        <Card operation={operation} context={context} operations={operations} onChanged={changed} />
      )}
    </LoadedView>
  );
}

function Card({
  operation: o,
  context,
  operations,
  onChanged,
}: {
  readonly operation: Operation;
  readonly context: Context;
  readonly operations: readonly Operation[];
  readonly onChanged: () => void;
}) {
  const [mode, setMode] = useState<'view' | 'amend' | 'settle' | 'cancel'>('view');
  const today = moscowToday();
  const active = o.status === 'active';
  const group = GROUP_OF[o.kind];
  const done = () => {
    setMode('view');
    onChanged();
  };

  return (
    <article className="operation-card">
      <p className="trade-badges">
        <KindPill operation={o} />
        <SettlementPill operation={o} today={today} />
        {!active && <span className="pill status-cancelled">отменена</span>}
        <span className="muted">версия {o.number}</span>
      </p>
      <dl className="facts">
        <dt>Дата</dt>
        <dd>{formatDate(o.op_date)}</dd>
        <dt>{group === 'transfers' ? 'Откуда → куда' : 'Портфель'}</dt>
        <dd>{o.receiver_code ? `${o.portfolio_code} → ${o.receiver_code}` : o.portfolio_code}</dd>
        {o.units !== null && (
          <>
            <dt>Паи</dt>
            <dd>{formatPrice(o.units)} шт.</dd>
            <dt>Цена за пай</dt>
            <dd>{formatMoney((Number(o.amount) / Number(o.units)).toFixed(2))} ₽</dd>
            <dt>Расчёт</dt>
            <dd>
              {o.settled_date ? `подтверждён ${formatDate(o.settled_date)}` : `план ${formatDate(o.planned_date)}`}
            </dd>
          </>
        )}
        {o.expense_kind && (
          <>
            <dt>Вид расхода</dt>
            <dd>{EXPENSE_LABELS[o.expense_kind]}</dd>
          </>
        )}
        {o.fee_kind && (
          <>
            <dt>Вознаграждение</dt>
            <dd>
              {FEE_LABELS[o.fee_kind]} по {formatDate(o.period_end)}
            </dd>
          </>
        )}
        {o.direction && (
          <>
            <dt>Направление</dt>
            <dd>{o.direction === 'in' ? 'НКЦ — фонду' : 'фонд — НКЦ'}</dd>
          </>
        )}
        {o.secid && (
          <>
            <dt>Бумага</dt>
            <dd>
              {o.short_name} <span className="mono">{o.secid}</span>
            </dd>
          </>
        )}
        {o.quantity !== null && (
          <>
            <dt>Количество</dt>
            <dd>{formatQuantity(o.quantity)} шт.</dd>
          </>
        )}
        {o.amount !== null && (
          <>
            <dt>Сумма</dt>
            <dd>{formatMoney(o.amount)} ₽</dd>
          </>
        )}
        <dt>Ввёл</dt>
        <dd>
          {o.author}, {formatTime(o.created_at)}
        </dd>
      </dl>
      {context.isOperations && active && mode === 'view' && (
        <p className="trade-actions">
          {group !== 'repo' && (
            <button type="button" onClick={() => setMode('amend')}>
              Исправить
            </button>
          )}
          {group === 'units' && o.settled_date === null && (
            <button type="button" onClick={() => setMode('settle')}>
              Подтвердить расчёт
            </button>
          )}
          <button type="button" onClick={() => setMode('cancel')}>
            Отменить операцию
          </button>
        </p>
      )}
      {mode === 'amend' && (
        <OperationForm
          context={context}
          operations={operations}
          initial={o}
          onSaved={done}
          onCancel={() => setMode('view')}
        />
      )}
      {mode === 'settle' && (
        <SettleForm operation={o} context={context} onDone={done} onBack={() => setMode('view')} />
      )}
      {mode === 'cancel' && (
        <CancelForm operation={o} context={context} onDone={done} onBack={() => setMode('view')} />
      )}
      <Versions operation={o} />
    </article>
  );
}

function SettleForm({
  operation,
  context,
  onDone,
  onBack,
}: {
  readonly operation: Operation;
  readonly context: Context;
  readonly onDone: () => void;
  readonly onBack: () => void;
}) {
  const [day, setDay] = useState(operation.planned_date ?? moscowToday());
  const { run, error, busy } = useAction(onDone);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void run(() => context.api.settleOperation(operation.id, day));
  };
  return (
    <form className="drawer-form" aria-label="Подтверждение расчёта" onSubmit={submit}>
      <label>
        Дата расчёта
        <input type="date" value={day} required onChange={(e) => setDay(e.target.value)} />
      </label>
      <ErrorNote message={error} />
      <div className="form-actions">
        <button type="submit" className="primary" disabled={busy}>
          Подтвердить расчёт
        </button>
        <button type="button" onClick={onBack}>
          Назад
        </button>
      </div>
    </form>
  );
}

function CancelForm({
  operation,
  context,
  onDone,
  onBack,
}: {
  readonly operation: Operation;
  readonly context: Context;
  readonly onDone: () => void;
  readonly onBack: () => void;
}) {
  const [reason, setReason] = useState('');
  const { run, error, busy } = useAction(onDone);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void run(() => context.api.cancelOperation(operation.id, reason.trim()));
  };
  return (
    <form className="drawer-form" aria-label="Отмена операции" onSubmit={submit}>
      <p className="hint">
        Операция останется в истории со статусом «отменена»; фонд пересчитается с {formatDate(operation.op_date)}.
      </p>
      <label>
        Причина отмены
        <input value={reason} required minLength={3} onChange={(e) => setReason(e.target.value)} />
      </label>
      <ErrorNote message={error} />
      <div className="form-actions">
        <button type="submit" className="primary danger-button" disabled={busy}>
          Отменить операцию
        </button>
        <button type="button" onClick={onBack}>
          Назад
        </button>
      </div>
    </form>
  );
}

function Versions({ operation }: { readonly operation: Operation }) {
  return (
    <table className="data" aria-label="Версии операции">
      <thead>
        <tr>
          <th>Версия</th>
          <th>Что изменилось</th>
          <th>Кто и почему</th>
        </tr>
      </thead>
      <tbody>
        {[...operation.versions].reverse().map((v) => (
          <tr key={v.number}>
            <td>
              {v.number}
              <div className="muted">{formatTime(v.created_at)}</div>
            </td>
            <td>
              {v.number === 1
                ? `проведена: ${OPERATION_LABELS[operation.kind]}`
                : v.changed.map((field) => OPERATION_FIELDS[field] ?? field).join(', ') || '—'}
            </td>
            <td>
              {v.author}
              {v.comment && <div className="muted">{v.comment}</div>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
