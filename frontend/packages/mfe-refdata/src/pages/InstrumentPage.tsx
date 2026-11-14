import { Drawer, ErrorNote, LoadedView, Tabs, useAction, useLoad, type Choice } from '@am/ui';
import { useCallback, useMemo, useState } from 'react';
import { Link } from 'react-router';

import type { Instrument, IsoDate, Params, RefdataApi, VersionHeader } from '../api';
import { ClassesPanel } from '../components/ClassesPanel';
import { EditForm } from '../components/EditForm';
import { RatingsPanel } from '../components/RatingsPanel';
import {
  AmortizationsTable,
  couponSummary,
  CouponsTable,
  nextCoupon,
  OffersTable,
  VersionFacts,
} from '../components/VersionDetails';
import {
  currencySign,
  FIELD_LABELS,
  formatDate,
  formatDecimal,
  formatTime,
  issTypeLabel,
  KIND_LABELS,
  SOURCE_LABELS,
  todayInMoscow,
} from '../format';

interface Props {
  readonly api: RefdataApi;
  readonly id: string;
  readonly basePath: string;
  readonly canEdit: boolean;
  /** Вести рейтинги и классификаторы: риск-менеджмент. */
  readonly canRate: boolean;
  readonly today?: IsoDate;
}

/** Карточка бумаги: ключевые параметры плитками, графики и версии во вкладках, правка — в панели. */
export function InstrumentPage({ api, id, basePath, canEdit, canRate, today = todayInMoscow() }: Props) {
  const load = useCallback(() => api.instrument(id), [api, id]);
  const [state, reload] = useLoad(load);

  return (
    <>
      <p>
        <Link to={basePath}>← Бумаги</Link>
      </p>
      <LoadedView state={state}>
        {(instrument) => (
          // Новая версия — карточка заново: выбрана последняя версия, панель правки закрыта.
          <InstrumentCard
            key={instrument.current.number}
            instrument={instrument}
            api={api}
            basePath={basePath}
            canEdit={canEdit}
            canRate={canRate}
            today={today}
            onChanged={reload}
          />
        )}
      </LoadedView>
    </>
  );
}

type Tab = 'coupons' | 'amortizations' | 'offers' | 'params' | 'versions' | 'ratings' | 'classes';

function InstrumentCard({
  instrument,
  api,
  basePath,
  canEdit,
  canRate,
  today,
  onChanged,
}: {
  readonly instrument: Instrument;
  readonly api: RefdataApi;
  readonly basePath: string;
  readonly canEdit: boolean;
  readonly canRate: boolean;
  readonly today: IsoDate;
  readonly onChanged: () => void;
}) {
  const isBond = instrument.kind === 'bond';
  const subject = useMemo(() => ({ instrument_id: instrument.id }), [instrument.id]);
  const [selected, setSelected] = useState(instrument.current.number);
  const [tab, setTab] = useState<Tab>(isBond ? 'coupons' : 'params');
  const [editing, setEditing] = useState(false);
  const { sync, note, error, busy } = useSync(instrument, api, onChanged);
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
      <div className="card-head">
        <span className="tag">{instrument.short_name}</span>
        <span className="mono">{instrument.secid}</span>
        {canEdit && (
          <div className="page-actions">
            <button type="button" disabled={busy} onClick={sync}>
              Обновить из ISS
            </button>
            <button type="button" className="primary" onClick={() => setEditing(true)}>
              Изменить
            </button>
          </div>
        )}
      </div>
      <p className="subline">
        {[instrument.name, instrument.isin, KIND_LABELS[instrument.kind], instrument.iss_type && issTypeLabel(instrument.iss_type)]
          .filter(Boolean)
          .join(' · ')}
        {' · эмитент: '}
        {instrument.issuer ? (
          <Link to={`${basePath}/issuers/${instrument.issuer.id}`}>{instrument.issuer.name}</Link>
        ) : (
          'появится после сверки с MOEX ISS'
        )}
        {` · сверка с ISS ${formatTime(instrument.last_synced_at)}`}
      </p>
      {note && <p role="status">{note}</p>}
      <ErrorNote message={error} />
      {(instrument.alerts.length > 0 || instrument.last_sync_error) && (
        <ul className="plain alerts" aria-label="Предупреждения">
          {instrument.last_sync_error && (
            <li className="notice notice-error">Последняя сверка с ISS не удалась: {instrument.last_sync_error}</li>
          )}
          {instrument.alerts.map((alert, index) => (
            <li key={`${alert.code}-${index}`} className="notice notice-warning">
              {alert.message}
            </li>
          ))}
        </ul>
      )}
      <LoadedView state={version}>
        {(shown) => {
          const p = shown.params;
          const tabs: Choice<Tab>[] = [
            ...(isBond
              ? [
                  { value: 'coupons' as const, label: 'Купоны', count: p.coupons.length },
                  { value: 'amortizations' as const, label: 'Погашение номинала', count: p.amortizations.length },
                  { value: 'offers' as const, label: 'Оферты', count: p.offers.length },
                ]
              : []),
            { value: 'params', label: 'Параметры' },
            { value: 'versions', label: 'Версии', count: instrument.versions.length },
            // Рейтинг бывает у облигации, а не у пая.
            ...(isBond ? [{ value: 'ratings' as const, label: 'Рейтинги' }] : []),
            { value: 'classes', label: 'Классификаторы' },
          ];
          return (
            <>
              {instrument.versions.length > 1 && (
                <VersionBar
                  versions={instrument.versions}
                  current={instrument.current.number}
                  selected={selected}
                  onSelect={setSelected}
                />
              )}
              {shown.number !== instrument.current.number && (
                <p className="notice">
                  Показана версия {shown.number} — не последняя: плитки и вкладки — по ней.{' '}
                  <button type="button" className="link" onClick={() => setSelected(instrument.current.number)}>
                    К последней версии
                  </button>
                </p>
              )}
              <Tiles params={p} isBond={isBond} today={today} />
              <Tabs label="Карточка бумаги" tabs={tabs} value={tab} onChange={setTab} />
              {tab === 'coupons' && <CouponsTable params={p} today={today} />}
              {tab === 'amortizations' && <AmortizationsTable params={p} />}
              {tab === 'offers' && <OffersTable params={p} />}
              {tab === 'params' && <VersionFacts version={shown} kind={instrument.kind} />}
              {tab === 'versions' && (
                <VersionsTable versions={instrument.versions} selected={selected} onSelect={setSelected} />
              )}
              {tab === 'ratings' && <RatingsPanel api={api} subject={subject} canEdit={canRate} />}
              {tab === 'classes' && (
                <ClassesPanel api={api} subject={subject} target="instrument" canEdit={canRate} />
              )}
            </>
          );
        }}
      </LoadedView>
      <Drawer title="Изменить бумагу" open={editing} onClose={() => setEditing(false)}>
        <EditForm instrument={instrument} api={api} onDone={onChanged} />
      </Drawer>
    </article>
  );
}

/** Лента версий: новые слева, как в истории версий; по выбранной — плитки и вкладки. */
function VersionBar({
  versions,
  current,
  selected,
  onSelect,
}: {
  readonly versions: readonly VersionHeader[];
  readonly current: number;
  readonly selected: number;
  readonly onSelect: (number: number) => void;
}) {
  return (
    <div className="version-bar" role="group" aria-label="Версия бумаги">
      {[...versions].reverse().map((v) => {
        const since = v.valid_from ? `с ${formatDate(v.valid_from)}` : 'с начала обращения';
        const latest = v.number === current;
        return (
          <button
            key={v.number}
            type="button"
            aria-pressed={v.number === selected}
            aria-label={`Версия ${v.number}, ${since}${latest ? ', последняя' : ''}`}
            title={`${SOURCE_LABELS[v.source]}, ${v.author}${v.comment ? ` — ${v.comment}` : ''}`}
            onClick={() => onSelect(v.number)}
          >
            <span className="version-number">№{v.number}</span>
            <span className="subline">{since}</span>
            {latest && <span className="badge">последняя</span>}
          </button>
        );
      })}
    </div>
  );
}

/** Ключевые параметры плитками: у облигации — номинал, погашение, купон, ближайшие события. */
function Tiles({
  params: p,
  isBond,
  today,
}: {
  readonly params: Params;
  readonly isBond: boolean;
  readonly today: IsoDate;
}) {
  const tile = (label: string, value: string) => (
    <div className="tile" key={label}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
  if (!isBond) {
    return (
      <dl className="tiles" aria-label="Ключевые параметры">
        {tile('Валюта', p.currency)}
        {tile('Режим торгов', p.board)}
      </dl>
    );
  }
  const next = nextCoupon(p.coupons, today);
  const offer = p.offers.find((o) => o.date >= today);
  const unit = currencySign(p.currency);
  const face = formatDecimal(p.initial_face_value, unit);
  const nextText = next
    ? `${formatDate(next.date)} · ${next.amount === null ? 'не объявлен' : formatDecimal(next.amount, unit)}`
    : 'купонов впереди нет';
  return (
    <dl className="tiles" aria-label="Ключевые параметры">
      {tile('Номинал', p.nominal_indexation === 'none' ? face : `${face}, индексируется`)}
      {tile('Погашение', formatDate(p.maturity_date))}
      {tile('Купон', couponSummary(p, today))}
      {tile('Ближайший купон', nextText)}
      {offer && tile('Оферта', formatDate(offer.date))}
    </dl>
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
    <table className="data" aria-label="Версии">
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
          <tr
            key={v.number}
            className={v.number === selected ? 'link-row selected' : 'link-row'}
            onClick={() => onSelect(v.number)}
          >
            <td>{v.number}</td>
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

/** «Обновить из ISS»: если ISS что-то изменил, появится новая версия и карточка
 * откроется заново; здесь остаётся только случай «изменений нет». */
function useSync(instrument: Instrument, api: RefdataApi, onChanged: () => void) {
  const { run, error, busy } = useAction(onChanged);
  const [note, setNote] = useState<string | null>(null);
  const sync = () => {
    setNote(null);
    void run(async () => {
      await api.sync(instrument.id);
      setNote('Данные ISS совпадают с последней версией.');
    });
  };
  return { sync, note, error, busy };
}
