import { Drawer, FilterChips, LoadedView, useLoad, type Choice } from '@am/ui';
import { useCallback, useState } from 'react';
import { Link, useNavigate } from 'react-router';

import type { InstrumentSummary, RefdataApi } from '../api';
import { AddInstrument } from '../components/AddInstrument';
import { couponCaption, familyOf, formatDate, formatTime, shortIssuer, type Family } from '../format';

type Filter = Family | 'all';

const FILTERS: readonly { readonly value: Filter; readonly label: string }[] = [
  { value: 'all', label: 'Все' },
  { value: 'fixed', label: 'Фиксы' },
  { value: 'floater', label: 'Флоатеры' },
  { value: 'linker', label: 'Линкеры' },
  { value: 'fund', label: 'Паи' },
  { value: 'other', label: 'Прочие' },
];

/**
 * Бумаги справочника: поиск, быстрые фильтры по купону, компактная таблица. Загрузка
 * из MOEX ISS — в выдвижной панели: таблица остаётся на всю ширину.
 */
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
  const [filter, setFilter] = useState<Filter>('all');
  const [adding, setAdding] = useState(false);
  // Смотреть бумагу в ISS может каждый; загрузить — трейдинг и сопровождение.
  const addLabel = canLoad ? 'Загрузить из ISS' : 'Найти в ISS';

  return (
    <>
      <div className="toolbar">
        <input
          type="search"
          aria-label="Поиск по справочнику"
          placeholder="Код, ISIN или название"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <button type="button" className={canLoad ? 'primary' : undefined} onClick={() => setAdding(true)}>
          {addLabel}
        </button>
      </div>
      <LoadedView state={state}>
        {(items) => (
          <Listing
            items={items}
            filter={filter}
            onFilter={setFilter}
            basePath={basePath}
            searching={query !== ''}
          />
        )}
      </LoadedView>
      <Drawer title={addLabel} open={adding} onClose={() => setAdding(false)}>
        <AddInstrument api={api} basePath={basePath} canLoad={canLoad} />
      </Drawer>
    </>
  );
}

function Listing({
  items,
  filter,
  onFilter,
  basePath,
  searching,
}: {
  readonly items: readonly InstrumentSummary[];
  readonly filter: Filter;
  readonly onFilter: (filter: Filter) => void;
  readonly basePath: string;
  readonly searching: boolean;
}) {
  const navigate = useNavigate();
  if (items.length === 0) {
    return (
      <p className="muted">
        {searching
          ? 'Ничего не нашлось. Если бумаги нет в справочнике, найдите её в MOEX ISS.'
          : 'В справочнике пока нет бумаг. Найдите первую по коду в MOEX ISS.'}
      </p>
    );
  }
  const families = items.map(familyOf);
  const count = (value: Filter) => (value === 'all' ? items.length : families.filter((f) => f === value).length);
  // Пустые фильтры не показываем; выбранный остаётся, даже если поиск его опустошил.
  const choices: Choice<Filter>[] = FILTERS.filter(
    (f) => f.value === 'all' || f.value === filter || count(f.value) > 0,
  ).map((f) => ({ ...f, count: count(f.value) }));
  const shown = items.filter((_, index) => filter === 'all' || families[index] === filter);
  const open = (item: InstrumentSummary) => void navigate(`${basePath}/instruments/${item.id}`);

  return (
    <>
      <div className="toolbar">
        <FilterChips label="Купон" choices={choices} value={filter} onChange={onFilter} />
      </div>
      {shown.length === 0 ? (
        <p className="muted">Таких бумаг в списке нет.</p>
      ) : (
        <table className="data" aria-label="Бумаги">
          <thead>
            <tr>
              <th>Бумага</th>
              <th className="hide-sm">Эмитент</th>
              <th className="hide-sm">Погашение</th>
              <th>Сверка с ISS</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((item) => (
              <tr key={item.id} className="link-row" onClick={() => open(item)}>
                <td>
                  <div className="cell-head">
                    <Link to={`${basePath}/instruments/${item.id}`} className="tag" onClick={(e) => e.stopPropagation()}>
                      {item.short_name}
                    </Link>
                    <span className="mono">{item.secid}</span>
                  </div>
                  <div className="subline">
                    {couponCaption(item)}
                    {/* На телефоне колонки «Погашение» нет: ближайшая дата — в подписи, оферта важнее. */}
                    <span className="show-sm">
                      {item.next_offer_date
                        ? ` · оферта ${formatDate(item.next_offer_date)}`
                        : item.maturity_date && ` · ${formatDate(item.maturity_date)}`}
                    </span>
                  </div>
                </td>
                <td className="hide-sm subline">{item.issuer_name ? shortIssuer(item.issuer_name) : '—'}</td>
                <td className="hide-sm">
                  {formatDate(item.maturity_date)}
                  {item.next_offer_date && <div className="subline offer">оферта {formatDate(item.next_offer_date)}</div>}
                </td>
                <td>
                  <SyncStatus item={item} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

/** Сверка с ISS: точка и время; на телефоне — только точка. */
function SyncStatus({ item }: { readonly item: InstrumentSummary }) {
  if (item.last_sync_error) {
    return (
      <span className="sync danger" title={item.last_sync_error}>
        <span className="dot dot-error" aria-hidden="true" />
        не удалась
      </span>
    );
  }
  if (!item.last_synced_at) {
    return <span className="muted">—</span>;
  }
  return (
    <span className="sync" title={`Сверка ${formatTime(item.last_synced_at)}`}>
      <span className="dot dot-ok" aria-hidden="true" />
      <span className="hide-sm">{formatTime(item.last_synced_at)}</span>
    </span>
  );
}
