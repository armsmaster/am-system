import { Drawer, ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';
import { useCallback, useState, type FormEvent } from 'react';

import type { IsoDate, RatingAction, RatingRecord, Ratings, RatingScale, RefdataApi, Subject } from '../api';
import { formatDate, formatTime, RATING_ACTION_LABELS, todayInMoscow } from '../format';
import { CancelRecord } from './CancelRecord';

/**
 * Рейтинги бумаги или эмитента: действующие — карточками агентств, ниже вся история
 * записей. Рейтинги ведёт риск-менеджмент — по ним лимиты относят облигацию
 * к рейтинговой группе. Новая запись — в выдвижной панели.
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
  const [adding, setAdding] = useState(false);
  const saved = useCallback(() => {
    setAdding(false);
    reload();
  }, [reload]);

  return (
    <section aria-label="Рейтинги" className="panel">
      <div className="panel-head">
        <h3>Рейтинги</h3>
        {canEdit && (
          <button type="button" className="primary" onClick={() => setAdding(true)}>
            Добавить запись
          </button>
        )}
      </div>
      <LoadedView state={state}>
        {(ratings) => <RatingsView ratings={ratings} api={api} canEdit={canEdit} onDone={reload} />}
      </LoadedView>
      <Drawer title="Новая запись о рейтинге" open={adding} onClose={() => setAdding(false)}>
        <RatingForm api={api} subject={subject} today={today} onSaved={saved} />
      </Drawer>
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
        <>
          <h4 className="section-title">История записей</h4>
          <table className="data" aria-label="История рейтингов">
            <thead>
              <tr>
                <th className="hide-sm">С даты</th>
                <th>Запись</th>
                {canEdit && <th />}
              </tr>
            </thead>
            <tbody>
              {[...ratings.records].reverse().map((record) => (
                <tr key={record.id} className={record.cancelled_at ? 'cancelled' : undefined}>
                  <td className="hide-sm">{formatDate(record.valid_from)}</td>
                  <td>
                    <span className="record-main">{recordText(record)}</span>
                    {/* На телефоне колонки дат нет — дата рядом с записью. */}
                    <span className="show-sm"> </span>
                    <span className="show-sm muted record-when">· с {formatDate(record.valid_from)}</span>
                    <div className="subline">
                      {record.author}, {formatTime(record.created_at)}
                      {record.comment && ` · ${record.comment}`}
                    </div>
                    {record.cancelled_at && (
                      <div className="subline">
                        Отменена: {record.cancel_comment} ({record.cancelled_by}, {formatTime(record.cancelled_at)})
                      </div>
                    )}
                  </td>
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
        </>
      )}
    </>
  );
}

/** Что действует сегодня: карточка на агентство — рейтинг крупно, ступень общей шкалы бейджем. */
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
    return <p className="notice">Рейтинга нет: агентства его не присваивали или отозвали.</p>;
  }
  return (
    <ul className="fact-cards" aria-label="Действующие рейтинги">
      {ratings.now.ratings.map((r) => (
        <li key={r.agency} className="fact-card">
          <span className="fact-label">{r.agency_name}</span>
          <span className="fact-value">{r.rating}</span>
          <span className="fact-meta">
            общая шкала <span className="badge">{r.grade}</span>
          </span>
          <span className="fact-meta">с {formatDate(r.since)}</span>
        </li>
      ))}
    </ul>
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
