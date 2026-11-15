import { Drawer, LoadedView, useLoad } from '@am/ui';
import { useCallback, useState } from 'react';
import { Link } from 'react-router';

import type { MarketdataApi, PriceView, RuleRecord } from '../api';
import { AnalyticsBlock } from '../components/AnalyticsBlock';
import { ManualPriceForm } from '../components/ManualPriceForm';
import { PriceChart } from '../components/PriceChart';
import { RuleForm } from '../components/RuleForm';
import { formatDate, formatNumber, formatPrice, formatTime, PRICE_LABELS, sourceLabel } from '../format';
import { changeClass, changePercent, formatChange, todayInMoscow } from '../moment';
import { REFRESH_MS, useEvery } from '../refresh';
import { priceKind } from './OverviewPage';

/** График за две недели; обновляем его реже цены — точек много. */
const CHART_DAYS = 14;
const CHART_REFRESH_MS = 5 * 60_000;

interface Props {
  readonly api: MarketdataApi;
  readonly id: string;
  readonly basePath: string;
  readonly canEdit: boolean;
  readonly now?: Date;
}

type Panel = 'manual' | 'rule';

/**
 * Карточка цены бумаги: плитки (цена, изменение, номинал, НКД), график за две недели,
 * калькулятор доходности на дату, все цены на момент, правило оценки и ручная цена.
 */
export function PricePage({ api, id, basePath, canEdit, now = new Date() }: Props) {
  const today = todayInMoscow(now);
  const [since] = useState(() => new Date(now.getTime() - CHART_DAYS * 86_400_000).toISOString());
  const loadPrice = useCallback(() => api.price(id), [api, id]);
  const [priceState, reloadPrice] = useLoad(loadPrice);
  useEvery(reloadPrice, REFRESH_MS);
  const loadSeries = useCallback(() => api.series(id, since), [api, id, since]);
  const [seriesState, reloadSeries] = useLoad(loadSeries);
  useEvery(reloadSeries, CHART_REFRESH_MS);
  const [panel, setPanel] = useState<Panel | null>(null);
  const series = seriesState.status === 'ready' ? seriesState.data : null;
  const changed = useCallback(() => {
    setPanel(null);
    reloadPrice();
    reloadSeries();
  }, [reloadPrice, reloadSeries]);

  return (
    <>
      <p>
        <Link to={basePath}>← Все бумаги</Link>
      </p>
      <LoadedView state={priceState}>
        {(view) => (
          <article className="price-card" aria-label={view.instrument.short_name}>
            <div className="card-head">
              <span className="tag">{view.instrument.short_name}</span>
              <span className="mono">{view.instrument.secid}</span>
              {canEdit && (
                <div className="page-actions">
                  <button type="button" onClick={() => setPanel('manual')}>
                    Ручная цена
                  </button>
                  <button type="button" onClick={() => setPanel('rule')}>
                    Правило оценки
                  </button>
                </div>
              )}
            </div>
            <Tiles view={view} />
            <ValuationNote view={view} />
            <section className="chart-card" aria-label="График за две недели">
              <h3>Две недели</h3>
              <LoadedView state={seriesState}>{(found) => <PriceChart series={found} rule={view.rule} />}</LoadedView>
            </section>
            <div className={view.instrument.kind === 'bond' ? 'price-grid' : 'price-grid single'}>
              {view.instrument.kind === 'bond' && <AnalyticsBlock api={api} id={id} today={today} />}
              <section className="price-panel" aria-label="Все цены на момент">
                <h3>Все цены на момент</h3>
                <CandidatesTable view={view} />
                <RuleSummary view={view} rules={series?.rules ?? []} canEdit={canEdit} />
              </section>
            </div>
            <Drawer title="Ручная цена" open={panel === 'manual'} onClose={() => setPanel(null)}>
              <ManualPriceForm id={id} api={api} onDone={changed} />
            </Drawer>
            <Drawer title="Правило оценки" open={panel === 'rule'} onClose={() => setPanel(null)}>
              <RuleForm
                key={JSON.stringify(view.rule)}
                id={id}
                rule={view.rule}
                timeframes={series ? Object.keys(series.bars).map(Number) : []}
                api={api}
                onDone={changed}
              />
            </Drawer>
          </article>
        )}
      </LoadedView>
    </>
  );
}

function Tiles({ view }: { readonly view: PriceView }) {
  const price = view.price;
  const day = changePercent(price?.value ?? null, view.day_ago.price?.value ?? null);
  const week = changePercent(price?.value ?? null, view.week_ago.price?.value ?? null);
  const bond = view.instrument.kind === 'bond';
  return (
    <dl className="tiles price-tiles" aria-label="Цена и изменение">
      <div className="tile">
        <dt>Цена для оценки</dt>
        <dd>
          <span className="tile-big">{price ? formatPrice(price.value) : 'нет цены'}</span>
          {price && (
            <span className="subline">
              {priceKind(price)}, {formatTime(price.time)}
            </span>
          )}
          {price?.stale && <span className="flag">устарела</span>}
        </dd>
      </div>
      <ChangeTile label="За день" change={day} base={view.day_ago.price?.value ?? null} day={view.day_ago.day} />
      <ChangeTile label="За неделю" change={week} base={view.week_ago.price?.value ?? null} day={view.week_ago.day} />
      {bond && (
        <>
          <div className="tile">
            <dt>Номинал, {formatDate(view.settle_date).slice(0, 5)}</dt>
            <dd>
              <span className="tile-big">{money(view.face_value)}</span>
            </dd>
          </div>
          <div className="tile">
            <dt>НКД, {formatDate(view.settle_date).slice(0, 5)}</dt>
            <dd>
              <span className="tile-big">{money(view.accrued)}</span>
            </dd>
          </div>
        </>
      )}
    </dl>
  );
}

function money(value: string | null): string {
  return value === null ? '—' : `${formatNumber(value, 2)} ₽`;
}

function ChangeTile({
  label,
  change,
  base,
  day,
}: {
  readonly label: string;
  readonly change: number | null;
  readonly base: string | null;
  readonly day: string;
}) {
  return (
    <div className="tile">
      <dt>{label}</dt>
      <dd>
        <span className={['tile-big', changeClass(change)].filter(Boolean).join(' ')}>{formatChange(change)}</span>
        <span className="subline">
          {base === null ? `нет цены на ${formatDate(day)}` : `с ${formatPrice(base)} на конец ${formatDate(day)}`}
        </span>
      </dd>
    </div>
  );
}

/** Почему эта цена, торговый день и разброс; нет цены — предупреждение. */
function ValuationNote({ view }: { readonly view: PriceView }) {
  if (view.price === null) {
    return (
      <p className="notice notice-warning">Цены нет: ни одного значения типов из правила оценки ещё не приходило.</p>
    );
  }
  return (
    <p className="muted valuation-note">
      {capitalize(view.price.reason)}. Торговый день — {formatDate(view.trading_day)}; разброс цен за день —{' '}
      {view.spread_bp === null ? 'нет' : `${formatPrice(view.spread_bp)} бп`}
      {view.wide_spread && <strong className="danger">, больше порога {view.rule.spread_threshold_bp} бп</strong>}.
    </p>
  );
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function CandidatesTable({ view }: { readonly view: PriceView }) {
  if (view.candidates.length === 0) {
    return <p className="muted">Цен по бумаге ещё нет.</p>;
  }
  return (
    <table className="data compact" aria-label="Все цены">
      <thead>
        <tr>
          <th>Тип</th>
          <th className="num">Цена</th>
          <th className="num">Время</th>
        </tr>
      </thead>
      <tbody>
        {view.candidates.map((c) => (
          <tr key={`${c.type}-${c.source}`} className={c.today ? undefined : 'past'}>
            <td>
              {PRICE_LABELS[c.type]}
              <div className="subline">{sourceLabel(c.source)}</div>
            </td>
            <td className="num">{formatPrice(c.value)}</td>
            <td className="num">{formatTime(c.time)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Правило одной строкой, история — по раскрытию. */
function RuleSummary({
  view,
  rules,
  canEdit,
}: {
  readonly view: PriceView;
  readonly rules: readonly RuleRecord[];
  readonly canEdit: boolean;
}) {
  const rule = view.rule;
  return (
    <div className="rule-summary">
      <p className="subline">
        Правило: {rule.price_types.map((t) => PRICE_LABELS[t]).join(' → ')}
        {rule.dema_timeframe !== null &&
          ` · DEMA по барам ${rule.dema_timeframe} мин, полураспад ${formatPrice(rule.dema_half_life)} мин`}
        {` · порог разброса ${rule.spread_threshold_bp} бп`}
        {!canEdit && '. Правило и ручную цену меняет трейдинг.'}
      </p>
      {rules.length === 0 ? (
        <p className="subline">Правило по умолчанию — VWAP; его ещё не меняли.</p>
      ) : (
        <details>
          <summary>История правила ({rules.length})</summary>
          <RuleHistory rules={rules} />
        </details>
      )}
    </div>
  );
}

function RuleHistory({ rules }: { readonly rules: readonly RuleRecord[] }) {
  return (
    <table className="data compact" aria-label="История правила">
      <thead>
        <tr>
          <th>С какого момента</th>
          <th>Правило</th>
        </tr>
      </thead>
      <tbody>
        {[...rules].reverse().map((r) => (
          <tr key={r.valid_from}>
            <td>{formatTime(r.valid_from)}</td>
            <td>
              {r.price_types.map((t) => PRICE_LABELS[t]).join(' → ')}
              {r.dema_timeframe !== null && (
                <div className="subline">
                  DEMA {r.dema_timeframe} мин, H {formatPrice(r.dema_half_life)} мин
                </div>
              )}
              <div className="subline">
                {r.author} — {r.comment}
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

