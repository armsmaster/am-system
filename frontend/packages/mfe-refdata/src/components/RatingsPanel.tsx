import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';
import { useCallback, useState, type FormEvent } from 'react';

import type { IsoDate, RatingAction, RatingRecord, Ratings, RatingScale, RefdataApi, Subject } from '../api';
import { formatDate, formatTime, RATING_ACTION_LABELS, todayInMoscow } from '../format';
import { CancelRecord } from './CancelRecord';

/**
 * Рейтинги бумаги или эмитента: что действует сегодня и вся история записей.
 * Рейтинги ведёт риск-менеджмент — по ним лимиты относят облигацию к рейтинговой группе.
 */
export function RatingsPanel({
  api,
  subject,
  canEdit,
  today = todayInMoscow(),
}: {
  readonly api: RefdataApi;
  /** Один и тот же объект между отрисовками — иначе рейтинги будут загружаться снова и снова. */
  readonly subject: Subject;
  readonly canEdit: boolean;
  readonly today?: IsoDate;
}) {
  const load = useCallback(() => api.ratings(subject), [api, subject]);
  const [state, reload] = useLoad(load);

  return (
    <section aria-label="Рейтинги">
      <h3>Рейтинги</h3>
      <LoadedView state={state}>
        {(ratings) => <RatingsView ratings={ratings} api={api} canEdit={canEdit} onDone={reload} />}
      </LoadedView>
      {canEdit && <RatingForm api={api} subject={subject} today={today} onSaved={reload} />}
    </section>
  );
}

function RatingsView({
  ratings,
  api,
  canEdit,
  onDone,
}: {
  readonly ratings: Ratings;
  readonly api: RefdataApi;
  readonly canEdit: boolean;
  readonly onDone: () => void;
}) {
  return (
    <>
      <Now ratings={ratings} />
      {ratings.records.length > 0 && (
        <table className="data" aria-label="История рейтингов">
          <thead>
            <tr>
              <th>С даты</th>
              <th>Запись</th>
              <th>Кто</th>
              <th>Комментарий</th>
              {canEdit && <th />}
            </tr>
          </thead>
          <tbody>
            {[...ratings.records].reverse().map((record) => (
              <tr key={record.id} className={record.cancelled_at ? 'cancelled' : undefined}>
                <td>{formatDate(record.valid_from)}</td>
                <td>
                  {recordText(record)}
                  {record.cancelled_at && (
                    <div className="muted">
                      Отменена: {record.cancel_comment} ({record.cancelled_by}, {formatTime(record.cancelled_at)})
                    </div>
                  )}
                </td>
                <td>
                  {record.author}
                  <div className="muted">{formatTime(record.created_at)}</div>
                </td>
                <td>{record.comment ?? '—'}</td>
                {canEdit && (
                  <td>
                    {!record.cancelled_at && (
                      <CancelRecord
                        what={`запись от ${formatDate(record.valid_from)}: ${recordText(record)}`}
                        cancel={(comment) => api.cancelRating(record.id, comment)}
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

function Now({ ratings }: { readonly ratings: Ratings }) {
  if (ratings.now.status === 'unknown') {
    return (
      <p className="notice notice-warning">
        Рейтинг ещё не вводили. Пока его нет, лимиты покажут «нет данных». Нет рейтинга ни у одного агентства —
        отметьте «рейтинга нет».
      </p>
    );
  }
  if (ratings.now.status === 'unrated') {
    return <p>Рейтинга нет: агентства его не присваивали или отозвали.</p>;
  }
  return (
    <table className="data" aria-label="Действующие рейтинги">
      <thead>
        <tr>
          <th>Агентство</th>
          <th>Рейтинг</th>
          <th>Общая шкала</th>
          <th>С даты</th>
        </tr>
      </thead>
      <tbody>
        {ratings.now.ratings.map((r) => (
          <tr key={r.agency}>
            <td>{r.agency_name}</td>
            <td className="code">{r.rating}</td>
            <td>{r.grade}</td>
            <td>{formatDate(r.since)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function recordText(record: RatingRecord): string {
  if (record.action === 'assigned') {
    return `${record.agency_name} ${record.rating}`;
  }
  if (record.action === 'withdrawn') {
    return `${record.agency_name}: рейтинг отозван`;
  }
  return 'рейтинга нет ни у одного агентства';
}

function RatingForm({
  api,
  subject,
  today,
  onSaved,
}: {
  readonly api: RefdataApi;
  readonly subject: Subject;
  readonly today: IsoDate;
  readonly onSaved: () => void;
}) {
  const load = useCallback(() => api.ratingScale(), [api]);
  const [scale] = useLoad(load);
  return (
    <LoadedView state={scale}>
      {(found) => <RatingFields api={api} subject={subject} today={today} scale={found} onSaved={onSaved} />}
    </LoadedView>
  );
}

function RatingFields({
  api,
  subject,
  today,
  scale,
  onSaved,
}: {
  readonly api: RefdataApi;
  readonly subject: Subject;
  readonly today: IsoDate;
  readonly scale: RatingScale;
  readonly onSaved: () => void;
}) {
  const [action, setAction] = useState<RatingAction>('assigned');
  const [agency, setAgency] = useState(scale.agencies[0]!.code);
  const ratings = scale.agencies.find((a) => a.code === agency)!.ratings;
  const [rating, setRating] = useState(ratings[0]!);
  const [day, setDay] = useState(today);
  const [comment, setComment] = useState('');
  const { run, error, busy } = useAction(onSaved);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const saved = await run(() =>
      api.addRating(subject, {
        action,
        agency: action === 'unrated' ? null : agency,
        rating: action === 'assigned' ? rating : null,
        valid_from: day,
        comment: comment.trim() || null,
      }),
    );
    if (saved) {
      setComment('');
    }
  }

  function chooseAgency(next: string) {
    // Та же ступень в записи другого агентства: AA-(RU) у АКРА → ruAA- у Эксперт РА.
    const step = ratings.indexOf(rating);
    setAgency(next);
    setRating(scale.agencies.find((a) => a.code === next)!.ratings[step]!);
  }

  return (
    <form onSubmit={(e) => void submit(e)} aria-label="Новая запись о рейтинге">
      <h4>Новая запись</h4>
      <label>
        Что произошло
        <select value={action} onChange={(e) => setAction(e.target.value as RatingAction)}>
          {(Object.keys(RATING_ACTION_LABELS) as RatingAction[]).map((a) => (
            <option key={a} value={a}>
              {RATING_ACTION_LABELS[a]}
            </option>
          ))}
        </select>
      </label>
      {action !== 'unrated' && (
        <label>
          Агентство
          <select value={agency} onChange={(e) => chooseAgency(e.target.value)}>
            {scale.agencies.map((a) => (
              <option key={a.code} value={a.code}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {action === 'assigned' && (
        <label>
          Рейтинг
          <select value={rating} onChange={(e) => setRating(e.target.value)}>
            {ratings.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        С даты
        <input type="date" value={day} max={today} onChange={(e) => setDay(e.target.value)} required />
      </label>
      <label>
        Комментарий
        <input
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="например, пресс-релиз агентства"
          maxLength={500}
        />
      </label>
      <button type="submit" disabled={busy}>
        Записать
      </button>
      <ErrorNote message={error} />
    </form>
  );
}
