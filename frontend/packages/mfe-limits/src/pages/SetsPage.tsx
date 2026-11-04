import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';
import { useCallback, useMemo, useState, type FormEvent } from 'react';

import type { Context } from '../App';
import type { LimitSet, SetBody } from '../api';
import { FundSelect } from '../components/FundSelect';
import { SetView } from '../components/SetView';
import { formatDate, formatTime, type Names } from '../format';

/** Набор лимитов фонда: версии с датами начала действия и новая версия. */
export function SetsPage({ context }: { readonly context: Context }) {
  const { api, funds } = context;
  const [fundId, setFundId] = useState(funds[0]!.id);
  const load = useCallback(() => api.sets(fundId), [api, fundId]);
  const [state, reload] = useLoad(load);

  return (
    <>
      <div className="filters">
        <FundSelect funds={funds} value={fundId} onChange={setFundId} />
      </div>
      <LoadedView state={state}>
        {(versions) => (
          // Новая версия — список заново: выбрана последняя, форма пустая.
          <Versions key={versions.length} versions={versions} fundId={fundId} context={context} onSaved={reload} />
        )}
      </LoadedView>
    </>
  );
}

function Versions({
  versions,
  fundId,
  context,
  onSaved,
}: {
  readonly versions: readonly LimitSet[];
  readonly fundId: string;
  readonly context: Context;
  readonly onSaved: () => void;
}) {
  const latest = versions[versions.length - 1];
  const [selected, setSelected] = useState(latest?.version);
  const shown = versions.find((v) => v.version === selected);

  return (
    <div className="split">
      <section aria-label="Версии набора">
        {versions.length === 0 ? (
          <p className="muted">Набора лимитов у фонда ещё нет.</p>
        ) : (
          <table className="data">
            <thead>
              <tr>
                <th>Версия</th>
                <th>Действует с</th>
                <th>Кто и почему</th>
              </tr>
            </thead>
            <tbody>
              {[...versions].reverse().map((v) => (
                <tr key={v.version} className={v.version === selected ? 'selected' : undefined}>
                  <td>
                    <button
                      type="button"
                      className="link"
                      aria-label={`Показать версию ${v.version}`}
                      onClick={() => setSelected(v.version)}
                    >
                      {v.version}
                    </button>
                  </td>
                  <td>{formatDate(v.valid_from)}</td>
                  <td>
                    {v.comment}
                    <div className="muted">
                      {v.author}, {formatTime(v.created_at)}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {shown && <SetView body={shown.body} names={context.names} />}
      </section>
      {context.canEdit ? (
        <SetEditor fundId={fundId} base={latest?.body} context={context} onSaved={onSaved} />
      ) : (
        <p className="muted">Набор лимитов ведёт риск-менеджмент: каждое изменение — новая версия с датой.</p>
      )}
    </div>
  );
}

/**
 * Новая версия набора — документом JSON: лимиты, проверки, фильтры (design/09).
 * Рядом — тот же набор словами: так видно, что именно сохранится.
 */
function SetEditor({
  fundId,
  base,
  context,
  onSaved,
}: {
  readonly fundId: string;
  readonly base: SetBody | undefined;
  readonly context: Context;
  readonly onSaved: () => void;
}) {
  const [text, setText] = useState(() => (base ? JSON.stringify(base, null, 2) : ''));
  const [day, setDay] = useState(context.today);
  const [comment, setComment] = useState('');
  const { run, error, busy } = useAction(onSaved);
  const parsed = useMemo(() => parse(text), [text]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (parsed.body) {
      const body = parsed.body;
      void run(() => context.api.saveSet(fundId, { valid_from: day, comment: comment.trim(), body }));
    }
  }

  return (
    <form onSubmit={submit} aria-label="Новая версия набора">
      <h3>Новая версия</h3>
      <label>
        Набор — документ JSON
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={18} spellCheck={false} />
      </label>
      {parsed.error && <p className="notice notice-error">{parsed.error}</p>}
      <label>
        Действует с
        <input type="date" value={day} onChange={(e) => setDay(e.target.value)} required />
      </label>
      <label>
        Что и почему меняем
        <input value={comment} onChange={(e) => setComment(e.target.value)} maxLength={500} required />
      </label>
      <button type="submit" disabled={busy || !parsed.body || comment.trim().length < 3}>
        Сохранить версию
      </button>
      <ErrorNote message={error} />
      {parsed.body && <Preview body={parsed.body} names={context.names} />}
    </form>
  );
}

function Preview({ body, names }: { readonly body: SetBody; readonly names: Names }) {
  return (
    <details open>
      <summary>Как сохранится</summary>
      <SetView body={body} names={names} />
    </details>
  );
}

/** Разбор JSON до сохранения: проверку по справочнику сделает сервис. */
function parse(text: string): { readonly body?: SetBody; readonly error?: string } {
  if (!text.trim()) {
    return { error: 'Вставьте набор — документ JSON.' };
  }
  try {
    const found = JSON.parse(text) as Partial<SetBody>;
    if (!found.document || !found.settings || !Array.isArray(found.limits)) {
      return { error: 'В наборе нужны document, settings и limits.' };
    }
    return { body: found as SetBody };
  } catch (failure) {
    return { error: `Это не JSON: ${(failure as Error).message}` };
  }
}
