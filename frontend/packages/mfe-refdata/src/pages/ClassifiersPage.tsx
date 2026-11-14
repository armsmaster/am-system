import { Drawer, ErrorNote, FilterChips, LoadedView, useAction, useLoad, type Choice } from '@am/ui';
import { useCallback, useState, type ClipboardEvent, type FormEvent, type KeyboardEvent } from 'react';

import type { Classifier, ClassValue, RefdataApi, Target } from '../api';
import { Glyph } from '../components/Glyph';
import { formatTime, TARGET_LABELS } from '../format';

type Filter = 'all' | Target;

const FILTERS: readonly Choice<Filter>[] = [
  { value: 'all', label: 'Все' },
  { value: 'instrument', label: 'Бумаги' },
  { value: 'issuer', label: 'Эмитенты' },
];

const TARGET_GLYPHS = { instrument: 'file', issuer: 'building' } as const;

/**
 * Классификаторы — признаки бумаг и эмитентов, которых нет в ISS: очерёдность,
 * секьюритизация, банк ли эмитент. Из них лимиты собирают классы политики.
 * Список — карточками со значениями-чипами; завести и изменить — в выдвижной панели.
 */
export function ClassifiersPage({ api, canEdit }: { readonly api: RefdataApi; readonly canEdit: boolean }) {
  const load = useCallback(() => api.classifiers(), [api]);
  const [state, reload] = useLoad(load);

  return (
    <section aria-label="Классификаторы">
      <LoadedView state={state}>
        {(items) => <Catalog items={items} api={api} canEdit={canEdit} onChanged={reload} />}
      </LoadedView>
      <p className="muted note">
        Значения у бумаг и эмитентов задают в их карточках, с датой начала действия. Классификатор описывает факт
        о бумаге, а не класс конкретной политики: классы собирают фильтры лимитов.
        {!canEdit && ' Классификаторы ведёт риск-менеджмент.'}
      </p>
    </section>
  );
}

function Catalog({
  items,
  api,
  canEdit,
  onChanged,
}: {
  readonly items: readonly Classifier[];
  readonly api: RefdataApi;
  readonly canEdit: boolean;
  readonly onChanged: () => void;
}) {
  const [filter, setFilter] = useState<Filter>('all');
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  // Изменённый классификатор приходит с перезагрузкой — панель показывает свежие данные.
  const editing = items.find((c) => c.id === editingId);
  const count = (value: Filter) => (value === 'all' ? items.length : items.filter((c) => c.target === value).length);
  const choices = FILTERS.map((f) => ({ ...f, count: count(f.value) }));
  const shown = items.filter((c) => filter === 'all' || c.target === filter);
  const created = useCallback(() => {
    setCreating(false);
    onChanged();
  }, [onChanged]);

  return (
    <>
      <div className="toolbar">
        {items.length > 0 && <FilterChips label="К чему относится" choices={choices} value={filter} onChange={setFilter} />}
        {canEdit && (
          <button type="button" className="primary toolbar-end" onClick={() => setCreating(true)}>
            <Glyph name="plus" /> Новый классификатор
          </button>
        )}
      </div>
      {items.length === 0 ? (
        <p className="muted">
          Классификаторов пока нет.
          {canEdit && ' Заведите первый: например, «Очерёдность» для бумаг или «Сектор» для эмитентов.'}
        </p>
      ) : shown.length === 0 ? (
        <p className="muted">Таких классификаторов нет.</p>
      ) : (
        <ul className="classifier-list" aria-label="Список классификаторов">
          {shown.map((c) => (
            <ClassifierCard key={c.id} classifier={c} canEdit={canEdit} onEdit={() => setEditingId(c.id)} />
          ))}
        </ul>
      )}
      <Drawer title="Новый классификатор" open={creating} onClose={() => setCreating(false)}>
        <CreateForm api={api} onCreated={created} />
      </Drawer>
      {editing && (
        <Drawer title={editing.name} open onClose={() => setEditingId(null)}>
          <EditClassifier key={editing.id} classifier={editing} api={api} onChanged={onChanged} />
        </Drawer>
      )}
    </>
  );
}

function ClassifierCard({
  classifier,
  canEdit,
  onEdit,
}: {
  readonly classifier: Classifier;
  readonly canEdit: boolean;
  readonly onEdit: () => void;
}) {
  return (
    <li className="classifier-card" aria-label={classifier.name}>
      <div className="classifier-head">
        <h3>{classifier.name}</h3>
        <TargetBadge target={classifier.target} />
        {canEdit && (
          <button
            type="button"
            className="icon-button"
            aria-label={`Изменить «${classifier.name}»`}
            title="Изменить"
            onClick={onEdit}
          >
            <Glyph name="pencil" size={18} />
          </button>
        )}
      </div>
      {classifier.description && <p className="subline">{classifier.description}</p>}
      <ul className="chip-list" aria-label={`Значения: ${classifier.name}`}>
        {classifier.values.map((v) => (
          <li key={v.id} className="value-chip">
            {v.name}
          </li>
        ))}
      </ul>
    </li>
  );
}

function TargetBadge({ target }: { readonly target: Target }) {
  return (
    <span className="target-badge">
      <Glyph name={TARGET_GLYPHS[target]} size={14} /> {TARGET_LABELS[target]}
    </span>
  );
}

/** Разбирает введённое или вставленное: значения через перевод строки или точку с запятой. */
function splitValues(text: string): string[] {
  return text
    .split(/[\n;]/)
    .map((v) => v.trim())
    .filter(Boolean);
}

/** Добавляет значения к списку; повтор без учёта регистра — ошибка с его названием. */
function appendValues(values: readonly string[], added: readonly string[]): string[] | string {
  const next = [...values];
  for (const value of added) {
    if (next.some((v) => v.toLowerCase() === value.toLowerCase())) {
      return `«${value}» уже есть в списке.`;
    }
    next.push(value);
  }
  return next;
}

function CreateForm({ api, onCreated }: { readonly api: RefdataApi; readonly onCreated: () => void }) {
  const [name, setName] = useState('');
  const [target, setTarget] = useState<Target>('instrument');
  const [values, setValues] = useState<readonly string[]>([]);
  const [draft, setDraft] = useState('');
  const [description, setDescription] = useState('');
  const [nameProblem, setNameProblem] = useState<string | null>(null);
  const [valuesProblem, setValuesProblem] = useState<string | null>(null);
  const { run, error, busy } = useAction(onCreated);

  /** Переносит значения из поля в список чипов; повтор оставляет поле как есть. */
  function take(text: string): readonly string[] | null {
    const next = appendValues(values, splitValues(text));
    if (typeof next === 'string') {
      setValuesProblem(next);
      return null;
    }
    setValues(next);
    setDraft('');
    setValuesProblem(null);
    return next;
  }

  function onDraftKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter') {
      event.preventDefault();
      take(draft);
    }
  }

  // Вставили список — сразу раскладываем по чипам; одно значение вставляется в поле как обычно.
  function onDraftPaste(event: ClipboardEvent<HTMLInputElement>) {
    const text = event.clipboardData.getData('text/plain');
    if (/[\n;]/.test(text)) {
      event.preventDefault();
      take(draft + text);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    // Недописанное в поле значение тоже берём — не терять же его.
    const all = take(draft);
    const title = name.trim();
    setNameProblem(title ? null : 'Введите название.');
    if (all === null) {
      return;
    }
    if (all.length === 0) {
      setValuesProblem('Добавьте хотя бы одно значение.');
      return;
    }
    if (!title) {
      return;
    }
    await run(() => api.createClassifier({ name: title, target, description: description.trim() || null, values: all }));
  }

  return (
    <form onSubmit={(e) => void submit(e)} aria-label="Новый классификатор" className="drawer-form">
      <label>
        Название
        <input
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setNameProblem(null);
          }}
          placeholder="Очерёдность"
          maxLength={100}
        />
      </label>
      {nameProblem && <p className="field-error">{nameProblem}</p>}
      <fieldset className="segmented-field">
        <legend>К чему относится</legend>
        <div className="segmented-choice">
          {(['instrument', 'issuer'] as const).map((value) => (
            <label key={value}>
              <input
                type="radio"
                name="target"
                value={value}
                checked={target === value}
                onChange={() => setTarget(value)}
              />
              <span>
                <Glyph name={TARGET_GLYPHS[value]} /> {value === 'instrument' ? 'Бумага' : 'Эмитент'}
              </span>
            </label>
          ))}
        </div>
        <p className="hint">После создания не меняется.</p>
      </fieldset>
      <fieldset className="values-field">
        <legend>Значения</legend>
        {values.length === 0 ? (
          <p className="hint">Пока ни одного — нужно хотя бы одно.</p>
        ) : (
          <ul className="chip-list" aria-label="Значения нового классификатора">
            {values.map((v) => (
              <li key={v} className="value-chip">
                {v}
                <button
                  type="button"
                  className="chip-remove"
                  aria-label={`Убрать «${v}»`}
                  onClick={() => setValues(values.filter((x) => x !== v))}
                >
                  <Glyph name="x" size={14} />
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="chip-input">
          <input
            aria-label="Новое значение"
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setValuesProblem(null);
            }}
            onKeyDown={onDraftKey}
            onPaste={onDraftPaste}
            placeholder="старший долг — Enter"
            maxLength={100}
          />
          <button type="button" className="icon-button" aria-label="Добавить значение" onClick={() => take(draft)}>
            <Glyph name="plus" size={18} />
          </button>
        </div>
        {valuesProblem ? (
          <p className="field-error">{valuesProblem}</p>
        ) : (
          <p className="hint">Можно вставить список — по значению в строке.</p>
        )}
      </fieldset>
      <label>
        <span>
          Описание <span className="optional">· необязательно</span>
        </span>
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="п. 2 политики: старший или субординированный долг"
          maxLength={500}
        />
      </label>
      <button type="submit" className="primary" disabled={busy}>
        Завести
      </button>
      <ErrorNote message={error} />
    </form>
  );
}

function EditClassifier({
  classifier,
  api,
  onChanged,
}: {
  readonly classifier: Classifier;
  readonly api: RefdataApi;
  readonly onChanged: () => void;
}) {
  return (
    <div className="drawer-form">
      <DetailsForm classifier={classifier} api={api} onSaved={onChanged} />
      <h3 className="section-title">Значения</h3>
      <ul className="value-list" aria-label="Значения">
        {classifier.values.map((v) => (
          <ValueRow key={v.id} classifierId={classifier.id} value={v} api={api} onRenamed={onChanged} />
        ))}
      </ul>
      <AddValue classifierId={classifier.id} api={api} onAdded={onChanged} />
      <p className="hint">Значения не удаляют: ими уже размечены бумаги и эмитенты. Ошиблись — переименуйте.</p>
      <p className="hint drawer-meta">
        <TargetBadge target={classifier.target} /> завёл {classifier.created_by}, {formatTime(classifier.created_at)}
      </p>
    </div>
  );
}

/** Название и описание — одной кнопкой «Сохранить». */
function DetailsForm({
  classifier,
  api,
  onSaved,
}: {
  readonly classifier: Classifier;
  readonly api: RefdataApi;
  readonly onSaved: () => void;
}) {
  const [name, setName] = useState(classifier.name);
  const [description, setDescription] = useState(classifier.description ?? '');
  const { run, error, busy } = useAction(onSaved);

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(() => api.updateClassifier(classifier.id, { name: name.trim(), description: description.trim() || null }));
  }

  return (
    <form onSubmit={submit} aria-label="Название и описание" className="plain-form">
      <label>
        Название
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} required />
      </label>
      <label>
        Описание
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Зачем классификатор и откуда значения"
          maxLength={500}
        />
      </label>
      <button type="submit" disabled={busy}>
        Сохранить
      </button>
      <ErrorNote message={error} />
    </form>
  );
}

/** Строка значения: чип и карандаш; карандаш превращает строку в поле — Enter сохраняет, Escape отменяет. */
function ValueRow({
  classifierId,
  value,
  api,
  onRenamed,
}: {
  readonly classifierId: string;
  readonly value: ClassValue;
  readonly api: RefdataApi;
  readonly onRenamed: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(value.name);
  const { run, error, busy } = useAction(onRenamed);

  function cancel() {
    setName(value.name);
    setEditing(false);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await run(() => api.renameValue(classifierId, value.id, name.trim()))) {
      setEditing(false);
    }
  }

  function onKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Escape') {
      // Escape отменяет переименование, а не закрывает всю панель.
      event.stopPropagation();
      cancel();
    }
  }

  if (!editing) {
    return (
      <li className="value-row">
        <span className="value-chip">{value.name}</span>
        <button
          type="button"
          className="icon-button"
          aria-label={`Переименовать «${value.name}»`}
          title="Переименовать"
          onClick={() => setEditing(true)}
        >
          <Glyph name="pencil" />
        </button>
      </li>
    );
  }
  return (
    <li className="value-row">
      <form onSubmit={(e) => void submit(e)} aria-label={`Переименовать «${value.name}»`} className="inline-form">
        <input
          aria-label="Новое название значения"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={onKey}
          maxLength={100}
          required
          autoFocus
        />
        <button type="submit" className="icon-button ok" aria-label="Сохранить название" disabled={busy}>
          <Glyph name="check" size={18} />
        </button>
        <button type="button" className="icon-button" aria-label="Не переименовывать" onClick={cancel}>
          <Glyph name="x" size={18} />
        </button>
      </form>
      <ErrorNote message={error} />
    </li>
  );
}

/** Новое значение встаёт в конец списка; Enter добавляет. */
function AddValue({
  classifierId,
  api,
  onAdded,
}: {
  readonly classifierId: string;
  readonly api: RefdataApi;
  readonly onAdded: () => void;
}) {
  const [name, setName] = useState('');
  const { run, error, busy } = useAction(onAdded);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await run(() => api.addValue(classifierId, name.trim()))) {
      setName('');
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} aria-label="Новое значение" className="chip-input">
      <input
        aria-label="Добавить значение"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Добавить значение — Enter"
        maxLength={100}
        required
      />
      <button type="submit" className="icon-button" aria-label="Добавить" disabled={busy}>
        <Glyph name="plus" size={18} />
      </button>
      <ErrorNote message={error} />
    </form>
  );
}
