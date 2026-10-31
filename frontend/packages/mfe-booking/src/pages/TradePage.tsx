import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';
import { useCallback, useState, type FormEvent } from 'react';
import { Link } from 'react-router';

import type { Context } from '../App';
import type { Trade } from '../api';
import { TradeForm } from '../components/TradeForm';
import {
  FIELD_LABELS,
  formatDate,
  formatMoney,
  formatPrice,
  formatQuantity,
  formatTime,
  SIDE_LABELS,
  STATUS_LABELS,
  VENUE_LABELS,
} from '../format';

/** Карточка сделки: поля действующей версии, история версий, подтверждение, правка, отмена. */
export function TradePage({ id, context }: { readonly id: string; readonly context: Context }) {
  const load = useCallback(() => context.api.trade(id), [context.api, id]);
  const [state, reload] = useLoad(load);

  return (
    <>
      <p>
        <Link to={context.basePath}>← Сделки за день</Link>
      </p>
      <LoadedView state={state}>
        {(trade) => <TradeCard trade={trade} context={context} onChanged={reload} />}
      </LoadedView>
    </>
  );
}

function TradeCard({
  trade,
  context,
  onChanged,
}: {
  readonly trade: Trade;
  readonly context: Context;
  readonly onChanged: () => void;
}) {
  const [mode, setMode] = useState<'view' | 'amend' | 'cancel'>('view');
  const confirm = useAction(onChanged);
  // Подтверждённую сделку правит и отменяет только сопровождение (решение 95).
  const changeable =
    trade.status !== 'cancelled' && (trade.status !== 'confirmed' || context.isOperations);
  const done = () => {
    setMode('view');
    onChanged();
  };

  return (
    <article>
      <h2>
        {SIDE_LABELS[trade.side]} <span className="code">{trade.secid}</span> — {trade.short_name}
      </h2>
      <p>
        <span className="badge">{STATUS_LABELS[trade.status]}</span> версия {trade.number}
      </p>
      <dl className="facts">
        <dt>Портфель</dt>
        <dd>{trade.portfolio_code}</dd>
        <dt>Количество</dt>
        <dd>{formatQuantity(trade.quantity)} шт.</dd>
        <dt>Цена</dt>
        <dd>
          {formatPrice(trade.price)}
          {trade.instrument_kind === 'bond' ? '% от номинала' : ' ₽ за пай'}
        </dd>
        {trade.instrument_kind === 'bond' && (
          <>
            <dt>Номинал на дату расчётов</dt>
            <dd>{formatPrice(trade.face_value)} ₽</dd>
            <dt>НКД на бумагу</dt>
            <dd>{formatPrice(trade.accrued)} ₽</dd>
          </>
        )}
        <dt>Сумма</dt>
        <dd>{formatMoney(trade.amount)} ₽</dd>
        <dt>Комиссия</dt>
        <dd>{formatMoney(trade.fee)} ₽</dd>
        <dt>Заключена</dt>
        <dd>{formatTime(trade.trade_at)}</dd>
        <dt>Расчёты</dt>
        <dd>{formatDate(trade.settle_date)}</dd>
        <dt>Место</dt>
        <dd>
          {VENUE_LABELS[trade.venue]}
          {trade.counterparty && `, ${trade.counterparty}`}
        </dd>
        <dt>Номер сделки у брокера</dt>
        <dd>{trade.external_id ?? '—'}</dd>
      </dl>
      <p>
        {context.isOperations && trade.status === 'entered' && (
          <button
            type="button"
            disabled={confirm.busy}
            onClick={() => void confirm.run(() => context.api.confirm(trade.id, ''))}
          >
            Подтвердить
          </button>
        )}{' '}
        {changeable && mode === 'view' && (
          <>
            <button type="button" onClick={() => setMode('amend')}>
              Исправить
            </button>{' '}
            <button type="button" onClick={() => setMode('cancel')}>
              Отменить сделку
            </button>
          </>
        )}
      </p>
      <ErrorNote message={confirm.error} />
      {mode === 'amend' && (
        <TradeForm context={context} initial={trade} onSaved={done} onCancel={() => setMode('view')} />
      )}
      {mode === 'cancel' && (
        <CancelForm trade={trade} context={context} onDone={done} onBack={() => setMode('view')} />
      )}
      <Versions trade={trade} />
    </article>
  );
}

function CancelForm({
  trade,
  context,
  onDone,
  onBack,
}: {
  readonly trade: Trade;
  readonly context: Context;
  readonly onDone: () => void;
  readonly onBack: () => void;
}) {
  const [comment, setComment] = useState('');
  const { run, error, busy } = useAction(onDone);

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(() => context.api.cancel(trade.id, comment.trim()));
  }

  return (
    <form onSubmit={submit} aria-label="Отмена сделки">
      <p className="muted">
        Отменённая сделка остаётся в истории, но не участвует в позициях и деньгах.
      </p>
      <label>
        Почему отменяется
        <input value={comment} minLength={3} required onChange={(e) => setComment(e.target.value)} />
      </label>
      <button type="submit" disabled={busy}>
        Отменить сделку
      </button>{' '}
      <button type="button" onClick={onBack}>
        Назад
      </button>
      <ErrorNote message={error} />
    </form>
  );
}

function Versions({ trade }: { readonly trade: Trade }) {
  return (
    <table className="data" aria-label="Версии сделки">
      <thead>
        <tr>
          <th>Версия</th>
          <th>Статус</th>
          <th>Что изменилось</th>
          <th>Кто и почему</th>
        </tr>
      </thead>
      <tbody>
        {[...trade.versions].reverse().map((v) => (
          <tr key={v.number}>
            <td>
              {v.number}
              <div className="muted">{formatTime(v.created_at)}</div>
            </td>
            <td>{STATUS_LABELS[v.status]}</td>
            <td>{v.changed.map((field) => FIELD_LABELS[field] ?? field).join(', ') || '—'}</td>
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
