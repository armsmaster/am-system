import { LoadedView, useLoad } from '@am/ui';
import { useCallback, useState } from 'react';
import { Link } from 'react-router';

import type { Candidate, MarketdataApi, PriceType, PriceView } from '../api';
import { formatPrice, formatTime, fromMoscowInput, PRICE_LABELS, sourceLabel } from '../format';
import { REFRESH_MS, useEvery } from '../refresh';

// Столбцы обзора — цены, которые сравнивают чаще всего.
const COLUMNS: readonly PriceType[] = ['vwap', 'last', 'bid', 'ask', 'market_price', 'dema'];

/**
 * Обзор цен: по каждой бумаге — цена оценки и рядом остальные цены на тот же
 * момент. Широкий разброс подсвечен (решение 65). Без выбранного момента —
 * текущие цены, обновляются каждые 30 секунд.
 */
export function OverviewPage({
  api,
  basePath,
}: {
  readonly api: MarketdataApi;
  readonly basePath: string;
}) {
  const [moment, setMoment] = useState('');
  const load = useCallback(
    () => api.overview(moment ? fromMoscowInput(moment) : undefined),
    [api, moment],
  );
  const [state, reload] = useLoad(load);
  useEvery(reload, REFRESH_MS, moment === '');

  return (
    <>
      <form className="inline" aria-label="Момент" onSubmit={(e) => e.preventDefault()}>
        <label>
          Момент, МСК
          <input
            type="datetime-local"
            value={moment}
            onChange={(e) => setMoment(e.target.value)}
          />
        </label>
        <button type="button" onClick={() => setMoment('')} disabled={moment === ''}>
          Сейчас
        </button>
      </form>
      <p className="muted">
        {moment
          ? 'Цены на выбранный момент — как их видела система тогда.'
          : 'Текущие цены, обновляются каждые 30 секунд.'}{' '}
        Серым — цены не за текущий торговый день.
      </p>
      <LoadedView state={state}>
        {(rows) => <OverviewTable rows={rows} basePath={basePath} />}
      </LoadedView>
    </>
  );
}

function OverviewTable({
  rows,
  basePath,
}: {
  readonly rows: readonly PriceView[];
  readonly basePath: string;
}) {
  if (rows.length === 0) {
    return (
      <p className="muted">
        Бумаг пока нет: цены появятся, когда бумагу загрузят в справочник.
      </p>
    );
  }
  return (
    <table className="data">
      <thead>
        <tr>
          <th>Бумага</th>
          <th>Цена оценки</th>
          {COLUMNS.map((kind) => (
            <th key={kind} className="num">
              {PRICE_LABELS[kind]}
            </th>
          ))}
          <th className="num">Разброс, бп</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.instrument.id} className={row.wide_spread ? 'wide' : undefined}>
            <td>
              <Link to={`${basePath}/instruments/${row.instrument.id}`} className="code">
                {row.instrument.secid}
              </Link>
              <div className="muted">{row.instrument.short_name}</div>
            </td>
            <td title={row.price?.reason}>
              <Valuation view={row} />
            </td>
            {COLUMNS.map((kind) => (
              <PriceCell key={kind} candidate={freshest(row.candidates, kind)} />
            ))}
            <td className={row.wide_spread ? 'num danger' : 'num'}>
              {row.spread_bp === null ? '—' : formatPrice(row.spread_bp)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Valuation({ view }: { readonly view: PriceView }) {
  const price = view.price;
  if (price === null) {
    return <span className="muted">нет цены</span>;
  }
  return (
    <>
      <strong>{formatPrice(price.value)}</strong>{' '}
      {price.stale && <span className="badge">устарела</span>}
      <div className="muted">
        {PRICE_LABELS[price.type]} · {sourceLabel(price.source)}, {formatTime(price.time)}
      </div>
    </>
  );
}

function PriceCell({ candidate }: { readonly candidate: Candidate | undefined }) {
  if (!candidate) {
    return <td className="num muted">—</td>;
  }
  return (
    <td
      className={candidate.today ? 'num' : 'num muted'}
      title={`${sourceLabel(candidate.source)}, ${formatTime(candidate.time)}`}
    >
      {formatPrice(candidate.value)}
    </td>
  );
}

/** Самая свежая цена типа среди источников. */
function freshest(candidates: readonly Candidate[], kind: PriceType): Candidate | undefined {
  return candidates
    .filter((c) => c.type === kind)
    .reduce<Candidate | undefined>(
      (best, c) => (best === undefined || c.time > best.time ? c : best),
      undefined,
    );
}
