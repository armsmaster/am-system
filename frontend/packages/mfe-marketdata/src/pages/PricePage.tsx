import { LoadedView, useLoad } from '@am/ui';
import { useCallback } from 'react';
import { Link } from 'react-router';

import type { MarketdataApi, PriceView, Series } from '../api';
import { AnalyticsBlock } from '../components/AnalyticsBlock';
import { ManualPriceForm } from '../components/ManualPriceForm';
import { PriceChart } from '../components/PriceChart';
import { RuleForm } from '../components/RuleForm';
import { formatPrice, formatTime, PRICE_LABELS, sourceLabel } from '../format';
import { REFRESH_MS, useEvery } from '../refresh';

interface Props {
  readonly api: MarketdataApi;
  readonly id: string;
  readonly basePath: string;
  readonly canEdit: boolean;
}

/**
 * Карточка цены бумаги: цена оценки и почему она, все цены, расчёт по облигации,
 * график, правило, ручная цена.
 */
export function PricePage({ api, id, basePath, canEdit }: Props) {
  const load = useCallback(() => Promise.all([api.price(id), api.series(id)]), [api, id]);
  const [state, reload] = useLoad(load);
  useEvery(reload, REFRESH_MS);

  return (
    <>
      <p>
        <Link to={basePath}>← Все бумаги</Link>
      </p>
      <LoadedView state={state}>
        {([view, series]) => (
          <article>
            <h2>
              <span className="code">{view.instrument.secid}</span> — {view.instrument.short_name}
            </h2>
            <div className="split">
              <section aria-label="Цена оценки">
                <ValuationBlock view={view} />
                <CandidatesTable view={view} />
              </section>
              <section aria-label="Правило оценки">
                {canEdit ? (
                  <>
                    <ManualPriceForm id={id} api={api} onDone={reload} />
                    <RuleForm
                      key={JSON.stringify(view.rule)}
                      id={id}
                      rule={view.rule}
                      timeframes={Object.keys(series.bars).map(Number)}
                      api={api}
                      onDone={reload}
                    />
                  </>
                ) : (
                  <RuleSummary view={view} />
                )}
              </section>
            </div>
            {view.instrument.kind === 'bond' && <AnalyticsBlock api={api} id={id} />}
            <PriceChart series={series} timeframe={view.rule.dema_timeframe} />
            <RuleHistory series={series} />
          </article>
        )}
      </LoadedView>
    </>
  );
}

function ValuationBlock({ view }: { readonly view: PriceView }) {
  const price = view.price;
  return (
    <div aria-label="Цена для оценки" role="group">
      <h3>Цена для оценки</h3>
      {price === null ? (
        <p className="notice notice-warning">
          Цены нет: ни одного значения типов из правила оценки ещё не приходило.
        </p>
      ) : (
        <>
          <p className="price">
            {formatPrice(price.value)}{' '}
            {price.stale && <span className="badge">устарела</span>}
          </p>
          <p className="muted">
            {PRICE_LABELS[price.type]} · {sourceLabel(price.source)}, {formatTime(price.time)}
          </p>
          <p>{price.reason}</p>
        </>
      )}
      <p className="muted">
        Торговый день — {view.trading_day.split('-').reverse().join('.')}. Разброс цен за день —{' '}
        {view.spread_bp === null ? 'нет' : `${formatPrice(view.spread_bp)} бп`}
        {view.wide_spread && ', больше порога'}.
      </p>
    </div>
  );
}

function CandidatesTable({ view }: { readonly view: PriceView }) {
  if (view.candidates.length === 0) {
    return <p className="muted">Цен по бумаге ещё нет.</p>;
  }
  return (
    <table className="data" aria-label="Все цены">
      <thead>
        <tr>
          <th>Тип</th>
          <th>Источник</th>
          <th className="num">Цена</th>
          <th>Время, МСК</th>
        </tr>
      </thead>
      <tbody>
        {view.candidates.map((c) => (
          <tr key={`${c.type}-${c.source}`} className={c.today ? undefined : 'muted'}>
            <td>{PRICE_LABELS[c.type]}</td>
            <td>{sourceLabel(c.source)}</td>
            <td className="num">{formatPrice(c.value)}</td>
            <td>{formatTime(c.time)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RuleSummary({ view }: { readonly view: PriceView }) {
  const rule = view.rule;
  return (
    <>
      <h3>Правило оценки</h3>
      <p>{rule.price_types.map((t) => PRICE_LABELS[t]).join(' → ')}</p>
      {rule.dema_timeframe !== null && (
        <p className="muted">
          DEMA по барам {rule.dema_timeframe} мин, период полураспада{' '}
          {formatPrice(rule.dema_half_life)} мин.
        </p>
      )}
      <p className="muted">
        Порог разброса — {rule.spread_threshold_bp} бп. Правило и ручную цену меняет трейдинг.
      </p>
    </>
  );
}

function RuleHistory({ series }: { readonly series: Series }) {
  if (series.rules.length === 0) {
    return <p className="muted">Правило по умолчанию — VWAP; его ещё не меняли.</p>;
  }
  return (
    <table className="data" aria-label="История правила">
      <thead>
        <tr>
          <th>С какого момента</th>
          <th>Правило</th>
          <th>Кто и почему</th>
        </tr>
      </thead>
      <tbody>
        {[...series.rules].reverse().map((r) => (
          <tr key={r.valid_from}>
            <td>{formatTime(r.valid_from)}</td>
            <td>
              {r.price_types.map((t) => PRICE_LABELS[t]).join(' → ')}
              {r.dema_timeframe !== null && (
                <div className="muted">
                  DEMA {r.dema_timeframe} мин, H {formatPrice(r.dema_half_life)} мин
                </div>
              )}
            </td>
            <td>
              {r.author}
              <div className="muted">{r.comment}</div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
