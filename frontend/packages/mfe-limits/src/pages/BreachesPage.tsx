import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';
import { Fragment, useCallback, useState, type FormEvent } from 'react';

import type { Context } from '../App';
import type { Breach, BreachKind } from '../api';
import { FundSelect } from '../components/FundSelect';
import { breachLimit, breachValue, formatDate, formatNumber, formatTime, KIND_LABELS, STATE_LABELS } from '../format';

type Filter = 'open' | 'overdue' | 'eliminated' | 'all';

const FILTERS: readonly (readonly [Filter, string])[] = [
  ['open', 'открытые'],
  ['overdue', 'просроченные'],
  ['eliminated', 'устранённые'],
  ['all', 'все'],
];

const ROW_CLASS = { open: 'status-warning', overdue: 'status-breach', eliminated: undefined };

/**
 * Журнал нарушений (п. 8.6 политики): нарушение записывает проверка прошедшего дня,
 * вид подтверждает и срок продлевает риск-менеджмент.
 */
export function BreachesPage({ context }: { readonly context: Context }) {
  const { api, funds } = context;
  const [fundId, setFundId] = useState(funds[0]!.id);
  const [filter, setFilter] = useState<Filter>('open');
  const load = useCallback(() => api.breaches(fundId, filter === 'all' ? undefined : filter), [api, fundId, filter]);
  const [state, reload] = useLoad(load);

  return (
    <>
      <div className="filters">
        <FundSelect funds={funds} value={fundId} onChange={setFundId} />
        <label>
          Нарушения
          <select value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
            {FILTERS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <LoadedView state={state}>
        {(found) =>
          found.length === 0 ? (
            <p className="muted">Таких нарушений нет. Журнал пополняет проверка прошедшего дня.</p>
          ) : (
            <BreachTable breaches={found} context={context} onChanged={reload} />
          )
        }
      </LoadedView>
    </>
  );
}

export function BreachTable({
  breaches,
  context,
  onChanged,
}: {
  readonly breaches: readonly Breach[];
  readonly context?: Context;
  readonly onChanged?: () => void;
}) {
  return (
    <table className="data" aria-label="Журнал нарушений">
      <thead>
        <tr>
          <th>Лимит</th>
          <th>Группа</th>
          <th>Значение</th>
          <th>Нарушение</th>
          <th>Вид</th>
          <th>Срок</th>
          <th>Статус</th>
        </tr>
      </thead>
      <tbody>
        {breaches.map((breach) => (
          <BreachRow key={breach.id} breach={breach} context={context} onChanged={onChanged} />
        ))}
      </tbody>
    </table>
  );
}

function BreachRow({
  breach,
  context,
  onChanged,
}: {
  readonly breach: Breach;
  readonly context: Context | undefined;
  readonly onChanged: (() => void) | undefined;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Fragment>
      <tr className={ROW_CLASS[breach.state]}>
        <td>{breachLimit(breach)}</td>
        <td>{breach.name}</td>
        <td>{breachValue(breach)}</td>
        <td>
          с {formatDate(breach.first_day)}
          <div className="muted">выявлено {formatDate(breach.detected_on)}</div>
        </td>
        <td>
          {KIND_LABELS[breach.kind]}
          <div className="muted">{breach.kind_by ? `подтвердил ${breach.kind_by}` : 'предложение системы'}</div>
        </td>
        <td>
          {formatDate(breach.deadline)}
          {breach.extensions.length > 0 && <div className="muted">продлён</div>}
        </td>
        <td>
          {STATE_LABELS[breach.state]}
          {breach.eliminated_on && ` ${formatDate(breach.eliminated_on)}`}
          {context && (
            <div>
              <button type="button" className="link" aria-expanded={open} onClick={() => setOpen(!open)}>
                {open ? 'Скрыть' : 'Подробнее'}
              </button>
            </div>
          )}
        </td>
      </tr>
      {open && context && (
        <tr>
          <td colSpan={7}>
            <Details breach={breach} context={context} onChanged={onChanged!} />
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function Details({
  breach,
  context,
  onChanged,
}: {
  readonly breach: Breach;
  readonly context: Context;
  readonly onChanged: () => void;
}) {
  const unit = breach.check_type === 'share' ? '%' : '';
  return (
    <div role="group" aria-label={`Нарушение: ${breachLimit(breach)}, ${breach.name}`}>
      {breach.peak !== null && breach.peak !== breach.value && (
        <p>
          Наибольшее значение — {formatNumber(breach.peak)}
          {unit}.
        </p>
      )}
      {breach.suggested !== breach.kind && <p>Система предлагала: {KIND_LABELS[breach.suggested]}.</p>}
      {breach.notes.length > 0 && (
        <ul className="plain" aria-label="Комментарии">
          {breach.notes.map((n, index) => (
            <li key={`${n.at}-${index}`}>
              {formatTime(n.at)}, {n.by}: {n.text}
              {n.kind && ` — вид: ${KIND_LABELS[n.kind]}`}
            </li>
          ))}
        </ul>
      )}
      {breach.extensions.length > 0 && (
        <ul className="plain" aria-label="Продления">
          {breach.extensions.map((e, index) => (
            <li key={`${e.at}-${index}`}>
              до {formatDate(e.deadline)}, решение {e.decision}: {e.comment} ({e.by}, {formatTime(e.at)})
            </li>
          ))}
        </ul>
      )}
      {context.canEdit ? (
        <>
          <ReviewForm breach={breach} context={context} onChanged={onChanged} />
          {breach.state !== 'eliminated' && breach.kind === 'passive' && (
            <ExtensionForm breach={breach} context={context} onChanged={onChanged} />
          )}
        </>
      ) : (
        <p className="muted">Вид нарушения подтверждает и срок продлевает риск-менеджмент.</p>
      )}
    </div>
  );
}

function ReviewForm({
  breach,
  context,
  onChanged,
}: {
  readonly breach: Breach;
  readonly context: Context;
  readonly onChanged: () => void;
}) {
  const [kind, setKind] = useState<BreachKind>(breach.kind);
  const [comment, setComment] = useState('');
  const { run, error, busy } = useAction(onChanged);

  function submit(event: FormEvent) {
    event.preventDefault();
    // Записали — журнал заново, форма пустая: комментарий уже в истории.
    void run(() => context.api.review(breach.id, { kind, comment: comment.trim() })).then(
      (done) => done && setComment(''),
    );
  }

  return (
    <form onSubmit={submit} aria-label="Вид нарушения">
      <label>
        Вид
        <select value={kind} onChange={(e) => setKind(e.target.value as BreachKind)}>
          <option value="active">{KIND_LABELS.active} — из-за сделки УК</option>
          <option value="passive">{KIND_LABELS.passive} — без сделки</option>
        </select>
      </label>
      <label>
        Комментарий
        <input value={comment} onChange={(e) => setComment(e.target.value)} maxLength={500} required />
      </label>
      <button type="submit" disabled={busy || comment.trim().length < 3}>
        Записать
      </button>
      <ErrorNote message={error} />
    </form>
  );
}

function ExtensionForm({
  breach,
  context,
  onChanged,
}: {
  readonly breach: Breach;
  readonly context: Context;
  readonly onChanged: () => void;
}) {
  const [deadline, setDeadline] = useState(breach.deadline);
  const [decision, setDecision] = useState('');
  const [comment, setComment] = useState('');
  const { run, error, busy } = useAction(onChanged);

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(() =>
      context.api.extend(breach.id, { deadline, decision: decision.trim(), comment: comment.trim() }),
    ).then((done) => {
      if (done) {
        setDecision('');
        setComment('');
      }
    });
  }

  return (
    <form onSubmit={submit} aria-label="Продление срока">
      <p className="muted">
        Продлить срок пассивного нарушения может генеральный директор (п. 8.5): предел — в наборе лимитов.
      </p>
      <label>
        Новый срок
        <input type="date" value={deadline} onChange={(e) => setDeadline(e.target.value)} required />
      </label>
      <label>
        Решение — номер и дата
        <input value={decision} onChange={(e) => setDecision(e.target.value)} maxLength={200} required />
      </label>
      <label>
        Комментарий
        <input value={comment} onChange={(e) => setComment(e.target.value)} maxLength={500} required />
      </label>
      <button type="submit" disabled={busy || decision.trim().length < 3 || comment.trim().length < 3}>
        Продлить
      </button>
      <ErrorNote message={error} />
    </form>
  );
}
