import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';
import { useCallback, useState, type FormEvent } from 'react';

import type { Classifications, Classifier, IsoDate, RefdataApi, Subject, Target } from '../api';
import { formatDate, formatTime, TARGET_LABELS, todayInMoscow } from '../format';
import { CancelRecord } from './CancelRecord';

interface Found {
  /** Классификаторы того, к чему относится карточка: бумаги или эмитента. */
  readonly classifiers: readonly Classifier[];
  readonly classifications: Classifications;
}

/**
 * Классификаторы бумаги или эмитента: значение сегодня и история записей.
 * Ведёт риск-менеджмент; по значениям лимиты собирают классы политики.
 */
export function ClassesPanel({
  api,
  subject,
  target,
  canEdit,
  today = todayInMoscow(),
}: {
  readonly api: RefdataApi;
  /** Один и тот же объект между отрисовками — иначе данные будут загружаться снова и снова. */
  readonly subject: Subject;
  readonly target: Target;
  readonly canEdit: boolean;
  readonly today?: IsoDate;
}) {
  const load = useCallback(async (): Promise<Found> => {
    const [classifiers, classifications] = await Promise.all([api.classifiers(), api.classifications(subject)]);
    return { classifiers: classifiers.filter((c) => c.target === target), classifications };
  }, [api, subject, target]);
  const [state, reload] = useLoad(load);

  return (
    <section aria-label="Классификаторы">
      <h3>Классификаторы</h3>
      <LoadedView state={state}>
        {(found) =>
          found.classifiers.length === 0 ? (
            <p className="muted">
              Классификаторов {TARGET_LABELS[target]} пока нет. Их заводит риск-менеджмент на вкладке
              «Классификаторы».
            </p>
          ) : (
            <>
              <ClassesView found={found} api={api} canEdit={canEdit} onDone={reload} />
              {canEdit && (
                <ClassForm
                  api={api}
                  subject={subject}
                  classifiers={found.classifiers}
                  today={today}
                  onSaved={reload}
                />
              )}
            </>
          )
        }
      </LoadedView>
    </section>
  );
}

function ClassesView({
  found,
  api,
  canEdit,
  onDone,
}: {
  readonly found: Found;
  readonly api: RefdataApi;
  readonly canEdit: boolean;
  readonly onDone: () => void;
}) {
  const { now, records } = found.classifications;
  return (
    <>
      <table className="data" aria-label="Значения сейчас">
        <thead>
          <tr>
            <th>Классификатор</th>
            <th>Значение</th>
            <th>Действует</th>
          </tr>
        </thead>
        <tbody>
          {found.classifiers.map((c) => {
            const current = now.find((n) => n.classifier_id === c.id);
            return (
              <tr key={c.id}>
                <td>{c.name}</td>
                <td>{current ? current.value : <span className="muted">не задано</span>}</td>
                <td>{current ? since(current.since) : '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {records.length > 0 && (
        <table className="data" aria-label="История значений">
          <thead>
            <tr>
              <th>Действует</th>
              <th>Классификатор</th>
              <th>Значение</th>
              <th>Кто</th>
              <th>Комментарий</th>
              {canEdit && <th />}
            </tr>
          </thead>
          <tbody>
            {[...records].reverse().map((r) => (
              <tr key={r.id} className={r.cancelled_at ? 'cancelled' : undefined}>
                <td>{since(r.valid_from)}</td>
                <td>{r.classifier}</td>
                <td>
                  {r.value}
                  {r.cancelled_at && (
                    <div className="muted">
                      Отменена: {r.cancel_comment} ({r.cancelled_by}, {formatTime(r.cancelled_at)})
                    </div>
                  )}
                </td>
                <td>
                  {r.author}
                  <div className="muted">{formatTime(r.created_at)}</div>
                </td>
                <td>{r.comment ?? '—'}</td>
                {canEdit && (
                  <td>
                    {!r.cancelled_at && (
                      <CancelRecord
                        what={`«${r.classifier}: ${r.value}» ${since(r.valid_from)}`}
                        cancel={(comment) => api.cancelClassification(r.id, comment)}
                        onDone={onDone}
                      />
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function since(day: IsoDate | null): string {
  return day ? `с ${formatDate(day)}` : 'с начала обращения';
}

function ClassForm({
  api,
  subject,
  classifiers,
  today,
  onSaved,
}: {
  readonly api: RefdataApi;
  readonly subject: Subject;
  readonly classifiers: readonly Classifier[];
  readonly today: IsoDate;
  readonly onSaved: () => void;
}) {
  // У классификатора всегда есть хотя бы одно значение — так проверяет сервис.
  const firstValue = (id: string) => classifiers.find((c) => c.id === id)!.values[0]!.id;
  const [classifierId, setClassifierId] = useState(classifiers[0]!.id);
  const [valueId, setValueId] = useState(() => firstValue(classifierId));
  const [day, setDay] = useState('');
  const [comment, setComment] = useState('');
  const { run, error, busy } = useAction(onSaved);
  const values = classifiers.find((c) => c.id === classifierId)!.values;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const saved = await run(() =>
      api.classify(subject, {
        classifier_id: classifierId,
        value_id: valueId,
        valid_from: day || null,
        comment: comment.trim() || null,
      }),
    );
    if (saved) {
      setComment('');
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} aria-label="Новое значение классификатора">
      <h4>Новое значение</h4>
      <label>
        Классификатор
        <select
          value={classifierId}
          onChange={(e) => {
            setClassifierId(e.target.value);
            setValueId(firstValue(e.target.value));
          }}
        >
          {classifiers.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Значение
        <select value={valueId} onChange={(e) => setValueId(e.target.value)}>
          {values.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Действует с
        <input type="date" value={day} max={today} onChange={(e) => setDay(e.target.value)} />
      </label>
      <p className="muted">Без даты — с начала обращения: так задают факты вроде очерёдности.</p>
      <label>
        Комментарий
        <input value={comment} onChange={(e) => setComment(e.target.value)} maxLength={500} />
      </label>
      <button type="submit" disabled={busy}>
        Записать
      </button>
      <ErrorNote message={error} />
    </form>
  );
}
