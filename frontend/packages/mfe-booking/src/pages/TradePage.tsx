import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';
import { useCallback, useState, type FormEvent } from 'react';
import { Link } from 'react-router';

import type { Context } from '../App';
import type { Trade } from '../api';
import { TradeForm } from '../components/TradeForm';
import {
  decimalInput,
  FIELD_LABELS,
  formatDate,
  formatMoney,
  formatPrice,
  formatQuantity,
  formatTime,
  STATUS_LABELS,
  tradeLabel,
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
  // День сделки закрыт — ни правки, ни подтверждения до повторного открытия (решение 127).
  const closed = trade.closed_through !== null && trade.trade_date <= trade.closed_through;
  // Подтверждённую сделку правит и отменяет только сопровождение (решение 95).
  const changeable =
    !closed && trade.status !== 'cancelled' && (trade.status !== 'confirmed' || context.isOperations);
  const done = () => {
    setMode('view');
    onChanged();
  };

  return (
    <article>
      <h2>
        {tradeLabel(trade)} <span className="code">{trade.secid}</span> — {trade.short_name}
      </h2>
      <p>
        <span className="badge">{STATUS_LABELS[trade.status]}</span> версия {trade.number}
      </p>
      <dl className="facts">
        <dt>Портфель</dt>
        <dd>{trade.portfolio_code}</dd>
        {trade.instrument_kind !== 'ksu' && (
          <>
            <dt>Количество</dt>
            <dd>{formatQuantity(trade.quantity)} шт.</dd>
            <dt>Цена</dt>
            <dd>
              {formatPrice(trade.price)}
              {trade.instrument_kind === 'bond' ? '% от номинала' : ' ₽ за пай'}
            </dd>
          </>
        )}
        {trade.instrument_kind === 'bond' && (
          <>
            <dt>Номинал на дату расчётов</dt>
            <dd>{formatPrice(trade.face_value)} ₽</dd>
            <dt>НКД на бумагу</dt>
            <dd>{formatPrice(trade.accrued)} ₽</dd>
          </>
        )}
        <dt>{trade.kind === 'repo' ? 'Сумма первой части' : 'Сумма'}</dt>
        <dd>{formatMoney(trade.amount)} ₽</dd>
        {trade.kind === 'repo' && (
          <>
            <dt>Ставка РЕПО</dt>
            <dd>{formatPrice(trade.repo_rate)}% годовых</dd>
            <dt>Вторая часть</dt>
            <dd>
              {formatDate(trade.second_date)}, {formatMoney(trade.second_amount)} ₽
            </dd>
          </>
        )}
        <dt>Комиссия</dt>
        <dd>{formatMoney(trade.fee)} ₽</dd>
        <dt>Заключена</dt>
        <dd>{formatTime(trade.trade_at)}</dd>
        <dt>{trade.kind === 'repo' ? 'Первая часть' : 'Расчёты'}</dt>
        <dd>{formatDate(trade.settle_date)}</dd>
        <dt>Место</dt>
        <dd>
          {VENUE_LABELS[trade.venue]}
          {trade.counterparty && `, ${trade.counterparty}`}
        </dd>
        <dt>Номер сделки у брокера</dt>
        <dd>{trade.external_id ?? '—'}</dd>
      </dl>
      {closed && (
        <p className="notice">
          День сделки закрыт: фонд закрыт по {formatDate(trade.closed_through)}. Исправить, отменить или
          подтвердить сделку можно после повторного открытия дня — его открывает администратор.
        </p>
      )}
      <p>
        {context.isOperations && trade.status === 'entered' && !closed && (
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
      {trade.kind === 'repo' && trade.status !== 'cancelled' && context.isOperations && (
        <ContributionForm trade={trade} context={context} />
      )}
      <Versions trade={trade} />
    </article>
  );
}

/** Компенсационный взнос по отчёту брокера (решение 113): деньгами или бумагами сделки. */
function ContributionForm({ trade, context }: { readonly trade: Trade; readonly context: Context }) {
  const [day, setDay] = useState(trade.settle_date);
  const [direction, setDirection] = useState<'in' | 'out'>('out');
  const [form, setForm] = useState<'money' | 'bonds'>('money');
  const [value, setValue] = useState('');
  const [saved, setSaved] = useState(false);
  const { run, error, busy } = useAction(() => setSaved(true));

  function submit(event: FormEvent) {
    event.preventDefault();
    setSaved(false);
    void run(() =>
      context.api.enterOperation({
        kind: 'repo_contribution',
        trade_id: trade.id,
        op_date: day,
        direction,
        amount: form === 'money' ? decimalInput(value) : null,
        quantity: form === 'bonds' ? Number(value) : null,
        comment: null,
      }),
    );
  }

  return (
    <form onSubmit={submit} aria-label="Компенсационный взнос">
      <h3>Компенсационный взнос</h3>
      <p className="muted">
        Если взносы по РЕПО проходят по счёту фонда — введите взнос по отчёту брокера. Фонд пересчитается с первой
        части: проценты поделятся по ночам заново.
      </p>
      <div className="fields">
        <label>
          Дата взноса
          <input type="date" value={day} required onChange={(e) => setDay(e.target.value)} />
        </label>
        <label>
          Кто платит
          <select value={direction} onChange={(e) => setDirection(e.target.value as 'in' | 'out')}>
            <option value="out">фонд платит НКЦ</option>
            <option value="in">НКЦ платит фонду</option>
          </select>
        </label>
        <label>
          Чем
          <select value={form} onChange={(e) => setForm(e.target.value as 'money' | 'bonds')}>
            <option value="money">деньгами</option>
            {trade.instrument_id !== null && <option value="bonds">бумагами</option>}
          </select>
        </label>
        <label>
          {form === 'money' ? 'Сумма, ₽' : 'Количество, шт.'}
          <input
            inputMode={form === 'money' ? 'decimal' : 'numeric'}
            value={value}
            required
            onChange={(e) => setValue(e.target.value)}
          />
        </label>
      </div>
      <button type="submit" disabled={busy}>
        Ввести взнос
      </button>
      {saved && (
        <p className="notice" role="status">
          Взнос введён — он виден в «Операциях фонда».
        </p>
      )}
      <ErrorNote message={error} />
    </form>
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
