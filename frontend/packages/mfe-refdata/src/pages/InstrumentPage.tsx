import { ErrorNote, LoadedView, useAction, useLoad } from '@am/ui';
import { useCallback, useState } from 'react';
import { Link } from 'react-router';

import type { Instrument, RefdataApi, VersionHeader } from '../api';
import { EditForm } from '../components/EditForm';
import { VersionDetails } from '../components/VersionDetails';
import { FIELD_LABELS, formatDate, formatTime, KIND_LABELS, SOURCE_LABELS } from '../format';

interface Props {
  readonly api: RefdataApi;
  readonly id: string;
  readonly basePath: string;
  readonly canEdit: boolean;
}

/** Карточка бумаги: предупреждения, версии, параметры и графики, правка. */
export function InstrumentPage({ api, id, basePath, canEdit }: Props) {
  const load = useCallback(() => api.instrument(id), [api, id]);
  const [state, reload] = useLoad(load);

  return (
    <>
      <p>
        <Link to={basePath}>← Все бумаги</Link>
      </p>
      <LoadedView state={state}>
        {(instrument) => (
          // Новая версия — карточка заново: выбрана последняя версия, форма правки пустая.
          <InstrumentCard
            key={instrument.current.number}
            instrument={instrument}
            api={api}
            canEdit={canEdit}
            onChanged={reload}
          />
        )}
      </LoadedView>
    </>
  );
}

function InstrumentCard({
  instrument,
  api,
  canEdit,
  onChanged,
}: {
  readonly instrument: Instrument;
  readonly api: RefdataApi;
  readonly canEdit: boolean;
  readonly onChanged: () => void;
}) {
  const [selected, setSelected] = useState(instrument.current.number);
  const loadVersion = useCallback(
    () =>
      selected === instrument.current.number
        ? Promise.resolve(instrument.current)
        : api.version(instrument.id, selected),
    [api, instrument, selected],
  );
  const [version] = useLoad(loadVersion);

  return (
    <article>
      <h2>
        <span className="code">{instrument.secid}</span> — {instrument.short_name}
      </h2>
      <p className="muted">
        {[instrument.name, instrument.isin, KIND_LABELS[instrument.kind]].filter(Boolean).join(' · ')}
      </p>
      {instrument.alerts.length > 0 && (
        <ul className="plain alerts" aria-label="Предупреждения">
          {instrument.alerts.map((alert, index) => (
            <li key={`${alert.code}-${index}`} className="notice notice-warning">
              {alert.message}
            </li>
          ))}
        </ul>
      )}

      <div className="split">
        <section aria-label="Версии">
          <h3>Версии</h3>
          <VersionsTable
            versions={instrument.versions}
            selected={selected}
            onSelect={setSelected}
          />
          <SyncPanel instrument={instrument} api={api} canEdit={canEdit} onChanged={onChanged} />
        </section>
        {canEdit ? (
          <EditForm instrument={instrument} api={api} onDone={onChanged} />
        ) : (
          <section aria-label="Правка">
            <p className="muted">
              Править бумагу может сопровождение. Каждая правка — новая версия с датой начала
              действия и комментарием.
            </p>
          </section>
        )}
      </div>

      <LoadedView state={version}>
        {(shown) => <VersionDetails version={shown} kind={instrument.kind} />}
      </LoadedView>
    </article>
  );
}

function VersionsTable({
  versions,
  selected,
  onSelect,
}: {
  readonly versions: readonly VersionHeader[];
  readonly selected: number;
  readonly onSelect: (number: number) => void;
}) {
  // Новые сверху: обычно нужна последняя.
  const rows = [...versions].reverse();
  return (
    <table className="data">
      <thead>
        <tr>
          <th>№</th>
          <th>Действует с</th>
          <th>Источник</th>
          <th>Что и почему</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((v) => (
          <tr key={v.number} className={v.number === selected ? 'selected' : undefined}>
            <td>
              <button
                type="button"
                className="link"
                aria-label={`Показать версию ${v.number}`}
                onClick={() => onSelect(v.number)}
              >
                {v.number}
              </button>
            </td>
            <td>{v.valid_from ? formatDate(v.valid_from) : 'с начала обращения'}</td>
            <td>
              {SOURCE_LABELS[v.source]}
              <div className="muted">
                {v.author}, {formatTime(v.created_at)}
              </div>
            </td>
            <td>
              {v.comment ?? '—'}
              {v.pinned.length > 0 && (
                <div className="muted">
                  Закреплено: {v.pinned.map((field) => FIELD_LABELS[field]).join(', ')}
                </div>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SyncPanel({
  instrument,
  api,
  canEdit,
  onChanged,
}: {
  readonly instrument: Instrument;
  readonly api: RefdataApi;
  readonly canEdit: boolean;
  readonly onChanged: () => void;
}) {
  const { run, error, busy } = useAction(onChanged);
  const [note, setNote] = useState<string | null>(null);

  // Если ISS что-то изменил, появится новая версия и карточка откроется заново.
  // Здесь остаётся только случай «изменений нет».
  function sync() {
    setNote(null);
    void run(async () => {
      await api.sync(instrument.id);
      setNote('Данные ISS совпадают с последней версией.');
    });
  }

  return (
    <div aria-label="Сверка с ISS" role="group">
      <p className="muted">
        Последняя сверка с MOEX ISS: {formatTime(instrument.last_synced_at)}. Сверка идёт каждое
        утро сама.
      </p>
      {instrument.last_sync_error && (
        <p className="notice notice-error">
          Последняя попытка сверки не удалась: {instrument.last_sync_error}
        </p>
      )}
      {canEdit && (
        <button type="button" disabled={busy} onClick={sync}>
          Обновить из ISS
        </button>
      )}
      {note && <p role="status">{note}</p>}
      <ErrorNote message={error} />
    </div>
  );
}
