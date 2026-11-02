import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';
import { useCallback, useState, type FormEvent } from 'react';

import type { Classifier, RefdataApi, Target } from '../api';
import { TARGET_LABELS } from '../format';

/**
 * Классификаторы — признаки бумаг и эмитентов, которых нет в ISS: очерёдность,
 * секьюритизация, банк ли эмитент. Из них лимиты собирают классы политики.
 */
export function ClassifiersPage({ api, canEdit }: { readonly api: RefdataApi; readonly canEdit: boolean }) {
  const load = useCallback(() => api.classifiers(), [api]);
  const [state, reload] = useLoad(load);

  return (
    <div className="split">
      <section aria-label="Классификаторы">
        <h2>Классификаторы</h2>
        <p className="muted">
          Значения у бумаг и эмитентов задают в их карточках, с датой начала действия. Классификатор описывает
          факт о бумаге, а не класс конкретной политики: классы собирают фильтры лимитов.
        </p>
        <LoadedView state={state}>
          {(items) =>
            items.length === 0 ? (
              <p className="muted">Классификаторов пока нет.</p>
            ) : (
              <ul className="plain">
                {items.map((c) => (
                  <ClassifierCard key={c.id} classifier={c} api={api} canEdit={canEdit} onDone={reload} />
                ))}
              </ul>
            )
          }
        </LoadedView>
      </section>
      {canEdit ? <CreateForm api={api} onDone={reload} /> : <p className="muted">Классификаторы ведёт риск-менеджмент.</p>}
    </div>
  );
}

function ClassifierCard({
  classifier,
  api,
  canEdit,
  onDone,
}: {
  readonly classifier: Classifier;
  readonly api: RefdataApi;
  readonly canEdit: boolean;
  readonly onDone: () => void;
}) {
  return (
    <li aria-label={classifier.name}>
      <h3>
        {classifier.name} <span className="muted">· {TARGET_LABELS[classifier.target]}</span>
      </h3>
      {classifier.description && <p>{classifier.description}</p>}
      <p>Значения: {classifier.values.map((v) => v.name).join(', ')}</p>
      {canEdit && (
        <>
          <RenameForm classifier={classifier} api={api} onDone={onDone} />
          <ValueForm classifier={classifier} api={api} onDone={onDone} />
        </>
      )}
    </li>
  );
}

function RenameForm({
  classifier,
  api,
  onDone,
}: {
  readonly classifier: Classifier;
  readonly api: RefdataApi;
  readonly onDone: () => void;
}) {
  const [name, setName] = useState(classifier.name);
  const [description, setDescription] = useState(classifier.description ?? '');
  const { run, error, busy } = useAction(onDone);

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(() => api.updateClassifier(classifier.id, { name: name.trim(), description: description.trim() || null }));
  }

  return (
    <form onSubmit={submit} aria-label={`Название: ${classifier.name}`}>
      <label>
        Название
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} required />
      </label>
      <label>
        Описание
        <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} />
      </label>
      <button type="submit" disabled={busy}>
        Сохранить
      </button>
      <ErrorNote message={error} />
    </form>
  );
}

/** Новое значение или новое название существующего. */
function ValueForm({
  classifier,
  api,
  onDone,
}: {
  readonly classifier: Classifier;
  readonly api: RefdataApi;
  readonly onDone: () => void;
}) {
  const [valueId, setValueId] = useState('');
  const [name, setName] = useState('');
  const { run, error, busy } = useAction(onDone);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const text = name.trim();
    const saved = await run(() =>
      valueId ? api.renameValue(classifier.id, valueId, text) : api.addValue(classifier.id, text),
    );
    if (saved) {
      setName('');
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} aria-label={`Значения: ${classifier.name}`}>
      <label>
        Что сделать
        <select value={valueId} onChange={(e) => setValueId(e.target.value)}>
          <option value="">добавить значение</option>
          {classifier.values.map((v) => (
            <option key={v.id} value={v.id}>
              переименовать «{v.name}»
            </option>
          ))}
        </select>
      </label>
      <label>
        Значение
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} required />
      </label>
      <button type="submit" disabled={busy}>
        {valueId ? 'Переименовать' : 'Добавить'}
      </button>
      <ErrorNote message={error} />
    </form>
  );
}

function CreateForm({ api, onDone }: { readonly api: RefdataApi; readonly onDone: () => void }) {
  const [name, setName] = useState('');
  const [target, setTarget] = useState<Target>('instrument');
  const [values, setValues] = useState('');
  const [description, setDescription] = useState('');
  const { run, error, busy } = useAction(onDone);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const saved = await run(() =>
      api.createClassifier({
        name: name.trim(),
        target,
        description: description.trim() || null,
        values: values
          .split('\n')
          .map((v) => v.trim())
          .filter(Boolean),
      }),
    );
    if (saved) {
      setName('');
      setValues('');
      setDescription('');
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} aria-label="Новый классификатор">
      <h3>Новый классификатор</h3>
      <label>
        Название
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="например, Очерёдность"
          maxLength={100}
          required
        />
      </label>
      <label>
        К чему относится
        <select value={target} onChange={(e) => setTarget(e.target.value as Target)}>
          <option value="instrument">к бумаге</option>
          <option value="issuer">к эмитенту</option>
        </select>
      </label>
      <label>
        Значения — по одному в строке
        <textarea
          value={values}
          onChange={(e) => setValues(e.target.value)}
          placeholder={'старший долг\nсубординированная'}
          rows={4}
          required
        />
      </label>
      <label>
        Описание
        <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} />
      </label>
      <button type="submit" disabled={busy}>
        Завести
      </button>
      <ErrorNote message={error} />
    </form>
  );
}
