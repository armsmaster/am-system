import type { ReactNode } from 'react';

import type { Field, InstrumentKind, Params, Version } from '../api';
import {
  couponKindLabel,
  formatDate,
  formatDecimal,
  INDEXATION_LABELS,
  OFFER_KIND_LABELS,
  RATE_LABELS,
  SOURCE_LABELS,
} from '../format';

/** Параметры и графики одной версии бумаги. */
export function VersionDetails({
  version,
  kind,
}: {
  readonly version: Version;
  readonly kind: InstrumentKind;
}) {
  const p = version.params;
  const pinned = new Set(version.pinned);
  const row = (field: Field, label: string, value: ReactNode) => (
    <>
      <dt>{label}</dt>
      <dd>
        {value}
        {pinned.has(field) && (
          <>
            {' '}
            <span className="badge" title="Поправлено вручную: синхронизация с ISS его не меняет">
              закреплено
            </span>
          </>
        )}
      </dd>
    </>
  );

  return (
    <section aria-label={`Версия ${version.number}`}>
      <h3>
        Версия {version.number}:{' '}
        {version.valid_from ? `действует с ${formatDate(version.valid_from)}` : 'с начала обращения'}
      </h3>
      <p className="muted">
        {SOURCE_LABELS[version.source]}, {version.author}
        {version.comment && ` — ${version.comment}`}
      </p>
      <dl className="facts">
        {row('name', 'Название', p.name)}
        {row('short_name', 'Краткое название', p.short_name)}
        {row('issuer', 'Эмитент', p.issuer ?? '—')}
        {row('board', 'Режим торгов', p.board)}
        {row('currency', 'Валюта', p.currency)}
        {kind === 'bond' && (
          <>
            {row('initial_face_value', 'Начальный номинал', formatDecimal(p.initial_face_value))}
            {row('issue_date', 'Дата выпуска', formatDate(p.issue_date))}
            {row('maturity_date', 'Дата погашения', formatDate(p.maturity_date))}
            {row('coupon_kind', 'Тип купона', couponKindLabel(p.coupon_kind))}
            {row('coupon_frequency', 'Купонов в год', p.coupon_frequency ?? '—')}
            {row('nominal_indexation', 'Индексация номинала', INDEXATION_LABELS[p.nominal_indexation])}
            {(p.coupon_kind === 'floater' || p.floater) &&
              row('floater', 'Параметры флоатера', floaterText(p))}
          </>
        )}
      </dl>
      {kind === 'bond' && <Schedules params={p} />}
    </section>
  );
}

function floaterText({ floater }: Params): string {
  if (!floater) {
    return 'не заполнены';
  }
  const sign = floater.margin_bp.startsWith('-') ? '' : '+';
  const capitalized = floater.capitalized ? 'капитализируется' : 'без капитализации';
  return `${RATE_LABELS[floater.rate]} ${sign}${formatDecimal(floater.margin_bp)} бп, ${capitalized}`;
}

function Schedules({ params: p }: { readonly params: Params }) {
  return (
    <>
      {p.nominal_indexation !== 'none' && (
        <p className="muted">
          Номинал индексируется, поэтому сумма купона и погашения зависит от номинала в день
          выплаты. Справочник покажет сумму, когда выплата состоится.
        </p>
      )}
      <h4>Купоны</h4>
      {p.coupons.length === 0 ? (
        <p className="muted">Купонов нет.</p>
      ) : (
        <table className="data" aria-label="Купоны">
          <thead>
            <tr>
              <th>Выплата</th>
              <th>Начало периода</th>
              <th>Фиксация реестра</th>
              <th className="num">Ставка, % годовых</th>
              <th className="num">Сумма, {p.currency}</th>
            </tr>
          </thead>
          <tbody>
            {p.coupons.map((c) => (
              <tr key={c.date}>
                <td>{formatDate(c.date)}</td>
                <td>{formatDate(c.start_date)}</td>
                <td>{formatDate(c.record_date)}</td>
                <td className="num">{formatDecimal(c.rate)}</td>
                <td className="num">{formatDecimal(c.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4>Амортизации и погашение</h4>
      {p.amortizations.length === 0 ? (
        <p className="muted">Графика погашения нет.</p>
      ) : (
        <table className="data" aria-label="Амортизации">
          <thead>
            <tr>
              <th>Дата</th>
              <th className="num">% начального номинала</th>
              <th className="num">Сумма, {p.currency}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {p.amortizations.map((a) => (
              <tr key={a.date}>
                <td>{formatDate(a.date)}</td>
                <td className="num">{formatDecimal(a.percent)}</td>
                <td className="num">{formatDecimal(a.amount)}</td>
                <td>{a.is_maturity ? 'погашение' : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4>Оферты</h4>
      {p.offers.length === 0 ? (
        <p className="muted">Оферт нет.</p>
      ) : (
        <table className="data" aria-label="Оферты">
          <thead>
            <tr>
              <th>Дата</th>
              <th>Тип</th>
              <th>Приём заявок</th>
              <th className="num">Цена, % номинала</th>
            </tr>
          </thead>
          <tbody>
            {p.offers.map((o) => (
              <tr key={`${o.date}-${o.kind}`}>
                <td>{formatDate(o.date)}</td>
                <td>
                  {OFFER_KIND_LABELS[o.kind]}
                  {o.iss_type && <div className="muted">{o.iss_type}</div>}
                </td>
                <td>
                  {o.window_start || o.window_end
                    ? `${formatDate(o.window_start)} – ${formatDate(o.window_end)}`
                    : '—'}
                </td>
                <td className="num">{formatDecimal(o.price)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
