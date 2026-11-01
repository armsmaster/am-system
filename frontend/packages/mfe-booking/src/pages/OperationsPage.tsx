import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';
import { useCallback, useState } from 'react';

import type { Context } from '../App';
import type { Operation } from '../api';
import { useSelection } from '../components/Selection';
import { OperationForm } from '../components/OperationForm';
import {
  EXPENSE_LABELS,
  FEE_LABELS,
  formatDate,
  formatMoney,
  formatPrice,
  formatQuantity,
  moscowToday,
  OPERATION_LABELS,
} from '../format';

/**
 * Операции фонда и переводы портфеля: выдача и погашение паёв, расходы,
 * переводы денег и бумаг. Проводит и отмечает расчёт сопровождение.
 */
export function OperationsPage({ context }: { readonly context: Context }) {
  const { portfolioId, update } = useSelection(context.portfolios);
  const load = useCallback(() => context.api.operations(portfolioId), [context.api, portfolioId]);
  const [state, reload] = useLoad(load);
  const [adding, setAdding] = useState(false);
  const [saved, setSaved] = useState<Operation | null>(null);

  if (context.portfolios.length === 0) {
    return <p className="muted">Нет доступных портфелей: доступ выдаёт администратор.</p>;
  }
  return (
    <>
      <form className="inline" aria-label="Портфель" onSubmit={(e) => e.preventDefault()}>
        <label>
          Портфель
          <select value={portfolioId} onChange={(e) => update({ portfolio: e.target.value })}>
            {context.portfolios.map((p) => (
              <option key={p.id} value={p.id}>
                {p.code} — {p.name}
              </option>
            ))}
          </select>
        </label>
      </form>
      {context.isOperations &&
        (adding ? (
          <OperationForm
            context={context}
            onSaved={(operation) => {
              setSaved(operation);
              setAdding(false);
              reload();
            }}
            onCancel={() => setAdding(false)}
          />
        ) : (
          <p>
            <button type="button" onClick={() => setAdding(true)}>
              Новая операция
            </button>
          </p>
        ))}
      {saved && (
        <>
          <p className="notice">
            Проведено: {OPERATION_LABELS[saved.kind]} от {formatDate(saved.op_date)}.
          </p>
          {saved.warnings.map((w) => (
            <p key={w} className="notice notice-warning">
              {w}
            </p>
          ))}
        </>
      )}
      <LoadedView state={state}>
        {(operations) =>
          operations.length === 0 ? (
            <p className="muted">Операций нет.</p>
          ) : (
            <table className="data" aria-label="Операции">
              <thead>
                <tr>
                  <th>Дата</th>
                  <th>Операция</th>
                  <th>Портфель</th>
                  <th className="num">Паи или бумаги</th>
                  <th className="num">Сумма, ₽</th>
                  <th>Расчёт по паям</th>
                  <th>Статус</th>
                  {context.isOperations && <th>Действия</th>}
                </tr>
              </thead>
              <tbody>
                {operations.map((o) => (
                  <OperationRow key={o.id} operation={o} context={context} onChanged={reload} />
                ))}
              </tbody>
            </table>
          )
        }
      </LoadedView>
    </>
  );
}

function OperationRow({
  operation: o,
  context,
  onChanged,
}: {
  readonly operation: Operation;
  readonly context: Context;
  readonly onChanged: () => void;
}) {
  const isUnits = o.kind === 'unit_issue' || o.kind === 'unit_redemption';
  const what = [
    OPERATION_LABELS[o.kind],
    o.expense_kind && EXPENSE_LABELS[o.expense_kind],
    o.fee_kind && `${FEE_LABELS[o.fee_kind]} по ${formatDate(o.period_end)}`,
    o.direction && (o.direction === 'in' ? 'НКЦ — фонду' : 'фонд — НКЦ'),
    o.secid,
  ].filter(Boolean);
  const settlement = !isUnits
    ? '—'
    : o.settled_date
      ? `отмечен ${formatDate(o.settled_date)}`
      : `план ${formatDate(o.planned_date)}`;
  return (
    <tr className={o.status === 'cancelled' ? 'muted' : undefined}>
      <td>{formatDate(o.op_date)}</td>
      <td>{what.join(', ')}</td>
      <td>{o.receiver_code ? `${o.portfolio_code} → ${o.receiver_code}` : o.portfolio_code}</td>
      <td className="num">
        {o.units !== null ? formatPrice(o.units) : o.quantity !== null ? formatQuantity(o.quantity) : '—'}
      </td>
      <td className="num">{formatMoney(o.amount)}</td>
      <td>{settlement}</td>
      <td>{o.status === 'active' ? 'действует' : 'отменена'}</td>
      {context.isOperations && (
        <td>
          {o.status === 'active' && (
            <Actions operation={o} context={context} canSettle={isUnits && !o.settled_date} onChanged={onChanged} />
          )}
        </td>
      )}
    </tr>
  );
}

/** Отметка расчёта по паям (решение 42) и отмена — новые версии операции. */
function Actions({
  operation,
  context,
  canSettle,
  onChanged,
}: {
  readonly operation: Operation;
  readonly context: Context;
  readonly canSettle: boolean;
  readonly onChanged: () => void;
}) {
  const [settled, setSettled] = useState(operation.planned_date ?? moscowToday());
  const [comment, setComment] = useState('');
  const { run, error, busy } = useAction(onChanged);
  return (
    <details>
      <summary>Изменить</summary>
      {canSettle && (
        <form
          className="inline"
          aria-label="Отметка расчёта"
          onSubmit={(e) => {
            e.preventDefault();
            void run(() => context.api.settleOperation(operation.id, settled));
          }}
        >
          <label>
            Дата расчёта
            <input type="date" value={settled} required onChange={(e) => setSettled(e.target.value)} />
          </label>
          <button type="submit" disabled={busy}>
            Отметить расчёт
          </button>
        </form>
      )}
      <form
        className="inline"
        aria-label="Отмена операции"
        onSubmit={(e) => {
          e.preventDefault();
          void run(() => context.api.cancelOperation(operation.id, comment.trim()));
        }}
      >
        <label>
          Причина отмены
          <input value={comment} required minLength={3} onChange={(e) => setComment(e.target.value)} />
        </label>
        <button type="submit" disabled={busy}>
          Отменить операцию
        </button>
      </form>
      <ErrorNote message={error} />
    </details>
  );
}
