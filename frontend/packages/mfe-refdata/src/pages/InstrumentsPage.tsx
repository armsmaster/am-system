import { LoadedView, useLoad } from '@am/ui';
import { useCallback, useState } from 'react';
import { Link } from 'react-router';

import type { InstrumentSummary, RefdataApi } from '../api';
import { AddInstrument } from '../components/AddInstrument';
import { formatTime, KIND_LABELS } from '../format';

/** Бумаги в справочнике и загрузка новой из MOEX ISS. */
export function InstrumentsPage({
  api,
  basePath,
  canLoad,
}: {
  readonly api: RefdataApi;
  readonly basePath: string;
  readonly canLoad: boolean;
}) {
  const [text, setText] = useState('');
  const query = text.trim();
  const load = useCallback(() => api.search(query), [api, query]);
  const [state] = useLoad(load);

  return (
    <div className="split">
      <section aria-label="Бумаги в справочнике">
        <h2>Бумаги в справочнике</h2>
        <input
          type="search"
          aria-label="Поиск по справочнику"
          placeholder="Код, ISIN или название"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <LoadedView state={state}>
          {(items) => (
            <InstrumentsTable items={items} basePath={basePath} searching={query !== ''} />
          )}
        </LoadedView>
      </section>
      <AddInstrument api={api} basePath={basePath} canLoad={canLoad} />
    </div>
  );
}

function InstrumentsTable({
  items,
  basePath,
  searching,
}: {
  readonly items: readonly InstrumentSummary[];
  readonly basePath: string;
  readonly searching: boolean;
}) {
  if (items.length === 0) {
    return (
      <p className="muted">
        {searching
          ? 'Ничего не нашлось. Если бумаги нет в справочнике, найдите её в MOEX ISS.'
          : 'В справочнике пока нет бумаг. Найдите первую по коду в MOEX ISS.'}
      </p>
    );
  }
  return (
    <table className="data">
      <thead>
        <tr>
          <th>Код</th>
          <th>Название</th>
          <th>Тип</th>
          <th>Сверка с ISS</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.id}>
            <td>
              <Link to={`${basePath}/instruments/${item.id}`} className="code">
                {item.secid}
              </Link>
              <div className="muted">{item.isin}</div>
            </td>
            <td>
              {item.short_name}
              <div className="muted">{item.name}</div>
            </td>
            <td>{KIND_LABELS[item.kind]}</td>
            <td>
              {item.last_sync_error ? (
                <span className="danger">не удалась</span>
              ) : (
                formatTime(item.last_synced_at)
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
