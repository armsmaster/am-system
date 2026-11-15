import { LoadedView, useLoad } from '@am/ui';
import { useCallback, useState } from 'react';
import { Link, useNavigate } from 'react-router';

import type { Choice, MarketdataApi, PriceView, Reference } from '../api';
import { MomentPicker } from '../components/MomentPicker';
import { formatDate, formatNumber, formatPrice, formatTime, PRICE_LABELS, sourceLabel } from '../format';
import {
  changeClass,
  changePercent,
  formatChange,
  momentAt,
  quarterInMoscow,
  todayInMoscow,
  type Moment,
} from '../moment';
import { REFRESH_MS, useEvery } from '../refresh';

/**
 * Обзор цен: по каждой бумаге — цена для оценки, её тип, изменение за день и за неделю,
 * номинал и НКД на дату расчётов (решение 152). Сейчас — обновляется каждые 30 секунд.
 */
export function OverviewPage({
  api,
  basePath,
  now = new Date(),
}: {
  readonly api: MarketdataApi;
  readonly basePath: string;
  readonly now?: Date;
}) {
  const today = todayInMoscow(now);
  const [moment, setMoment] = useState<Moment>({ kind: 'now' });
  const at = momentAt(moment, today);
  const load = useCallback(() => api.overview(at), [api, at]);
  const [state, reload] = useLoad(load);
  useEvery(reload, REFRESH_MS, moment.kind === 'now');

  return (
    <>
      <div className="toolbar">
        <MomentPicker value={moment} today={today} quarter={quarterInMoscow(now)} onChange={setMoment} />
      </div>
      <LoadedView state={state}>
        {(rows) => (
          <>
            <p className="muted moment-note">
              {at === undefined
                ? 'Текущие цены, обновляются каждые 30 секунд.'
                : `Цены на ${formatTime(at)} МСК — как их видела система тогда.`}
              {rows.length > 0 && ` Номинал и НКД — на дату расчётов ${formatDate(rows[0]!.settle_date)}.`}
            </p>
            <OverviewTable rows={rows} basePath={basePath} />
          </>
        )}
      </LoadedView>
    </>
  );
}

function OverviewTable({ rows, basePath }: { readonly rows: readonly PriceView[]; readonly basePath: string }) {
  const navigate = useNavigate();
  if (rows.length === 0) {
    return <p className="muted">Бумаг пока нет: цены появятся, когда бумагу загрузят в справочник.</p>;
  }
  return (
    <table className="data" aria-label="Цены">
      <thead>
        <tr>
          <th>Бумага</th>
          <th className="num">Цена</th>
          <th className="hide-sm">Тип</th>
          <th className="num">День</th>
          <th className="num hide-sm">Неделя</th>
          <th className="num hide-sm">Номинал</th>
          <th className="num hide-sm">НКД</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const link = `${basePath}/instruments/${row.instrument.id}`;
          return (
            <tr key={row.instrument.id} className="link-row" onClick={() => void navigate(link)}>
              <td>
                <Link to={link} className="tag" onClick={(e) => e.stopPropagation()}>
                  {row.instrument.short_name}
                </Link>
                <div className="subline">
                  <span className="mono">{row.instrument.secid}</span>
                </div>
                {row.price && <div className="subline show-sm">{priceKind(row.price)}</div>}
              </td>
              <td className="num">
                <PriceValue view={row} />
              </td>
              <td className="hide-sm">
                {row.price ? (
                  <>
                    {priceKind(row.price)}
                    <div className="subline">{formatTime(row.price.time)}</div>
                  </>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
              <Change price={row.price} reference={row.day_ago} />
              <Change price={row.price} reference={row.week_ago} className="hide-sm" />
              <td className="num hide-sm">{money(row.face_value)}</td>
              <td className="num hide-sm">{money(row.accrued)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** «VWAP · MOEX ISS». */
export function priceKind(price: Choice): string {
  return `${PRICE_LABELS[price.type]} · ${sourceLabel(price.source)}`;
}

function money(value: string | null): string {
  return value === null ? '—' : `${formatNumber(value, 2)} ₽`;
}

/** Цена для оценки; под ней — пометки: устарела, широкий разброс. */
function PriceValue({ view }: { readonly view: PriceView }) {
  const price = view.price;
  if (price === null) {
    return <span className="muted">нет цены</span>;
  }
  return (
    <>
      <strong>{formatPrice(price.value)}</strong>
      {view.accrued !== null && <div className="subline show-sm">НКД {money(view.accrued)}</div>}
      {price.stale && <div className="flag">устарела</div>}
      {view.wide_spread && <div className="flag">разброс {formatPrice(view.spread_bp)} бп</div>}
    </>
  );
}

/** Изменение к цене на конец дня; при наведении — с какой цены и на какой день. */
export function Change({
  price,
  reference,
  className,
}: {
  readonly price: Choice | null;
  readonly reference: Reference;
  readonly className?: string;
}) {
  const change = changePercent(price?.value ?? null, reference.price?.value ?? null);
  const title = reference.price
    ? `с ${formatPrice(reference.price.value)} на конец ${formatDate(reference.day)}`
    : `нет цены на конец ${formatDate(reference.day)}`;
  const classes = ['num', changeClass(change), className].filter(Boolean).join(' ');
  return (
    <td className={classes} title={title}>
      {formatChange(change)}
    </td>
  );
}
