import { useState, type ReactNode } from 'react';

import type { Coupon, Field, InstrumentKind, IsoDate, IssFloater, Params, Version } from '../api';
import {
  couponKindLabel,
  couponLength,
  currencySign,
  daysBetween,
  floaterCaption,
  formatDate,
  formatDecimal,
  formatPercent,
  INDEXATION_LABELS,
  periodLabel,
  RATE_LABELS,
  SOURCE_LABELS,
} from '../format';

/** Параметры одной версии бумаги — карточками по смыслу: выпуск, номинал, торги, купон. */
export function VersionFacts({ version, kind }: { readonly version: Version; readonly kind: InstrumentKind }) {
  const p = version.params;
  const pinned = new Set(version.pinned);
  const isBond = kind === 'bond';
  const row = (field: Field, label: string, value: ReactNode) => (
    <div className="kv-row" key={field}>
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
    </div>
  );
  // Карточка группы. Широкая — во всю ширину, её строки делятся поровну на две колонки.
  const group = (title: string, rows: readonly ReactNode[], wide = false) => {
    const half = Math.ceil(rows.length / 2);
    const columns = wide ? [rows.slice(0, half), rows.slice(half)] : [rows];
    return (
      <section className={wide ? 'param-group wide' : 'param-group'} aria-label={title}>
        <h4>{title}</h4>
        <div className="kv-columns">
          {columns.map((column, i) => (
            <dl className="kv" key={i}>
              {column}
            </dl>
          ))}
        </div>
      </section>
    );
  };
  const release = [
    row('name', 'Название', p.name),
    row('short_name', 'Краткое название', p.short_name),
    row('issuer', 'Эмитент', p.issuer ?? '—'),
    ...(isBond
      ? [
          row('issue_date', 'Дата выпуска', formatDate(p.issue_date)),
          row('maturity_date', 'Дата погашения', formatDate(p.maturity_date)),
        ]
      : []),
  ];
  const trading = [row('board', 'Режим торгов', p.board), row('currency', 'Валюта', p.currency)];
  const coupon = [
    row('coupon_kind', 'Тип купона', couponKindLabel(p.coupon_kind)),
    // Длину считаем по графику купонов: поля такого нет, закрепить нечего.
    <div className="kv-row" key="coupon_length">
      <dt>Длина купона</dt>
      <dd>{couponLength(p.coupons) ?? '—'}</dd>
    </div>,
    row('coupon_frequency', 'Купонов в год', p.coupon_frequency ?? '—'),
    ...(p.coupon_kind === 'floater' || p.floater ? [row('floater', 'Параметры флоатера', floaterText(p))] : []),
    ...(p.iss_floater ? [row('iss_floater', 'Ставка и маржа по ISS', issFloaterText(p.iss_floater))] : []),
  ];

  return (
    <section aria-label={`Версия ${version.number}`} className="params">
      <h3>
        Версия {version.number}:{' '}
        {version.valid_from ? `действует с ${formatDate(version.valid_from)}` : 'с начала обращения'}
      </h3>
      <p className="subline">
        {SOURCE_LABELS[version.source]}, {version.author}
        {version.comment && ` — ${version.comment}`}
      </p>
      {/* Ритм «широкая — пара — широкая»: длинные группы делятся на колонки, короткие стоят парой. */}
      <div className="param-layout">
        {group('Выпуск', release, true)}
        {isBond ? (
          <>
            <div className="param-pair">
              {group('Номинал', [
                row('initial_face_value', 'Начальный номинал', formatDecimal(p.initial_face_value, currencySign(p.currency))),
                row('nominal_indexation', 'Индексация номинала', INDEXATION_LABELS[p.nominal_indexation]),
              ])}
              {group('Торги', trading)}
            </div>
            {group('Купон', coupon, true)}
          </>
        ) : (
          group('Торги', trading, true)
        )}
      </div>
    </section>
  );
}

function floaterText({ floater }: Params): string {
  if (!floater) {
    return 'не заполнены';
  }
  const sign = floater.margin_bp.startsWith('-') ? '' : '+';
  const capitalized = floater.capitalized ? 'капитализируется' : 'без капитализации';
  return `${RATE_LABELS[floater.rate]} ${sign}${formatDecimal(floater.margin_bp)}\u00a0бп, ${capitalized}`;
}

/** Как в ISS: незнакомую ставку показываем кодом ISS; маржи нет — только ставку. */
function issFloaterText(iss: IssFloater): string {
  const rate = iss.rate === 'other' ? iss.benchmark : RATE_LABELS[iss.rate];
  if (iss.margin_bp === null) {
    return rate;
  }
  // ISS даёт маржу в процентах: 1.30 → 130.00 бп; лишние нули не показываем.
  const margin = iss.margin_bp.includes('.') ? iss.margin_bp.replace(/\.?0+$/, '') : iss.margin_bp;
  const sign = margin.startsWith('-') ? '' : '+';
  return `${rate} ${sign}${formatDecimal(margin)}\u00a0бп`;
}

/** Ближайший купон: первая выплата не раньше сегодня. */
export function nextCoupon(coupons: readonly Coupon[], today: IsoDate): Coupon | null {
  return coupons.find((c) => c.date >= today) ?? null;
}

/**
 * Купон коротко: флоатер — база и спред, остальные — ставка ближайшего купона
 * с известной ставкой; через точку — длина купона: «КС + 130 бп · 1M», «7,1% · 182D».
 */
export function couponSummary(p: Params, today: IsoDate): string {
  let text: string;
  if (p.coupon_kind === 'floater') {
    const terms = p.floater ?? p.iss_floater;
    text = floaterCaption(terms?.rate ?? null, terms?.margin_bp ?? null);
  } else {
    const known = p.coupons.filter((c) => c.rate !== null);
    const rate = known.find((c) => c.date >= today) ?? known[known.length - 1];
    text = rate ? formatPercent(rate.rate!) : '—';
  }
  const length = couponLength(p.coupons);
  return length ? `${text} · ${length}` : text;
}

export function CouponsTable({ params: p, today }: { readonly params: Params; readonly today: IsoDate }) {
  const [showPast, setShowPast] = useState(false);
  if (p.coupons.length === 0) {
    return <p className="muted">Купонов нет.</p>;
  }
  const next = nextCoupon(p.coupons, today);
  // Прошедшие купоны — по кнопке: обычно нужны ближайшие.
  const past = p.coupons.filter((c) => c.date < today);
  const shown = showPast ? p.coupons : p.coupons.filter((c) => c.date >= today);
  return (
    <>
      {p.nominal_indexation !== 'none' && (
        <p className="muted">
          Номинал индексируется, поэтому сумма купона и погашения зависит от номинала в день выплаты.
          Справочник покажет сумму, когда выплата состоится.
        </p>
      )}
      {past.length > 0 && (
        <p>
          <button type="button" className="link" onClick={() => setShowPast(!showPast)}>
            {showPast ? 'Скрыть прошедшие купоны' : `Показать прошедшие купоны: ${past.length}`}
          </button>
        </p>
      )}
      {shown.length === 0 ? (
        <p className="muted">Купонов впереди нет.</p>
      ) : (
        <table className="data" aria-label="Купоны">
          <thead>
            <tr>
              <th>Выплата</th>
              <th className="hide-sm">Период</th>
              <th className="num">Ставка</th>
              <th className="num">Сумма, {currencySign(p.currency)}</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => (
              <tr key={c.date} className={c === next ? 'next' : c.date < today ? 'past' : undefined}>
                <td>
                  {formatDate(c.date)}
                  {c === next && (
                    <>
                      {' '}
                      <span className="badge">ближайший</span>
                    </>
                  )}
                  {c.record_date && <div className="subline">фиксация {formatDate(c.record_date)}</div>}
                </td>
                <td className="hide-sm subline">{c.start_date ? periodText(c.start_date, c.date) : '—'}</td>
                <td className="num">{c.rate === null ? '—' : formatPercent(c.rate)}</td>
                <td className="num">{c.amount === null ? 'не объявлен' : formatDecimal(c.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

/** «1M · 30 дн.»; если длина и так в днях — только она: «182D». */
function periodText(start: IsoDate, end: IsoDate): string {
  const label = periodLabel(start, end);
  return label.endsWith('D') ? label : `${label} · ${daysBetween(start, end)} дн.`;
}

export function AmortizationsTable({ params: p }: { readonly params: Params }) {
  if (p.amortizations.length === 0) {
    return <p className="muted">Графика погашения нет.</p>;
  }
  return (
    <table className="data" aria-label="Погашение номинала">
      <thead>
        <tr>
          <th>Дата</th>
          <th className="num">% номинала</th>
          <th className="num">Сумма, {currencySign(p.currency)}</th>
        </tr>
      </thead>
      <tbody>
        {p.amortizations.map((a) => (
          <tr key={a.date}>
            <td>
              {formatDate(a.date)}
              {a.is_maturity && (
                <>
                  {' '}
                  <span className="badge">погашение</span>
                </>
              )}
            </td>
            <td className="num">{formatDecimal(a.percent)}</td>
            <td className="num">{formatDecimal(a.amount)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const OFFER_SHORT = { put: 'put — выкуп у держателей', call: 'call — погашение эмитентом' } as const;

export function OffersTable({ params: p }: { readonly params: Params }) {
  if (p.offers.length === 0) {
    return <p className="muted">Оферт нет.</p>;
  }
  return (
    <table className="data" aria-label="Оферты">
      <thead>
        <tr>
          <th>Дата</th>
          <th className="hide-sm">Тип</th>
          <th className="num">Цена, % номинала</th>
        </tr>
      </thead>
      <tbody>
        {p.offers.map((o) => (
          <tr key={`${o.date}-${o.kind}`}>
            <td>
              {formatDate(o.date)}
              <div className="subline">
                {o.window_start || o.window_end
                  ? `приём заявок ${formatDate(o.window_start)} – ${formatDate(o.window_end)}`
                  : 'приём заявок не указан'}
              </div>
            </td>
            <td className="hide-sm">
              {OFFER_SHORT[o.kind]}
              {o.iss_type && <div className="subline">{o.iss_type}</div>}
            </td>
            <td className="num">{formatDecimal(o.price)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
