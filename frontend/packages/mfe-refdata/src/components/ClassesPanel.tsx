import { Drawer, ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';
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
 * Классификаторы бумаги или эмитента: значения сегодня — плитками, ниже история записей.
 * Ведёт риск-менеджмент; по значениям лимиты собирают классы политики. Новое значение —
 * в выдвижной панели.
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
  const [adding, setAdding] = useState(false);
  const saved = useCallback(() => {
    setAdding(false);
    reload();
  }, [reload]);

  return (
    <section aria-label="Классификаторы" className="panel">
      <LoadedView state={state}>
        {(found) => (
          <>
            <div className="panel-head">
              <h3>Классификаторы</h3>
              {canEdit && found.classifiers.length > 0 && (
                <button type="button" className="primary" onClick={() => setAdding(true)}>
                  Задать значение
                </button>
              )}
            </div>
            {found.classifiers.length === 0 ? (
              <p className="muted">
                Классификаторов {TARGET_LABELS[target]} пока нет. Их заводит риск-менеджмент на вкладке
                «Классификаторы».
              </p>
            ) : (
              <>
                <ClassesView found={found} api={api} canEdit={canEdit} onDone={reload} />
                <Drawer title="Новое значение классификатора" open={adding} onClose={() => setAdding(false)}>
                  <ClassForm
                    api={api}
                    subject={subject}
                    classifiers={found.classifiers}
                    today={today}
                    onSaved={saved}
                  />
                </Drawer>
              </>
            )}
          </>
        )}
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
      {/* Те же карточки, что у рейтингов, но с фиолетовым акцентом; незаданное — серое. */}
      <dl className="fact-cards" aria-label="Значения сейчас">
        {found.classifiers.map((c) => {
          const current = now.find((n) => n.classifier_id === c.id);
          return (
            <div className={current ? 'fact-card classifier' : 'fact-card classifier unset'} key={c.id}>
              <dt className="fact-label">{c.name}</dt>
              <dd>
                <span className="fact-value">{current ? current.value : 'не задано'}</span>
                {current && <span className="fact-meta">{since(current.since)}</span>}
              </dd>
            </div>
          );
        })}
      </dl>
      {records.length > 0 && (
        <>
          <h4 className="section-title">История записей</h4>
          <table className="data" aria-label="История значений">
            <thead>
              <tr>
                <th className="hide-sm">Действует</th>
                <th>Запись</th>
                {canEdit && <th />}
              </tr>
            </thead>
            <tbody>
              {[...records].reverse().map((r) => (
                <tr key={r.id} className={r.cancelled_at ? 'cancelled' : undefined}>
                  <td className="hide-sm">{since(r.valid_from)}</td>
                  <td>
                    <span className="record-main">
                      {r.classifier}: {r.value}
                    </span>
                    {/* На телефоне колонки дат нет — дата рядом с записью. */}
                    <span className="show-sm"> </span>
                    <span className="show-sm muted record-when">· {since(r.valid_from)}</span>
                    <div className="subline">
                      {r.author}, {formatTime(r.created_at)}
                      {r.comment && ` · ${r.comment}`}
                    </div>
                    {r.cancelled_at && (
                      <div className="subline">
                        Отменена: {r.cancel_comment} ({r.cancelled_by}, {formatTime(r.cancelled_at)})
                      </div>
                    )}
                  </td>
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
        </>
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
