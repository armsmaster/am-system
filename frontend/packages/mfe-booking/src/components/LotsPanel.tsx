import { Fragment } from 'react';

import type { Line } from '../api';
import { FLAG_LABELS, formatMoney, formatPrice, formatQuantity, formatTime, incompleteFlags } from '../format';
import {
  fixed,
  horizonText,
  issuerOf,
  PART_LABELS,
  PERIOD_KEYS,
  PERIOD_LABELS,
  periodValue,
  yieldText,
  type Measure,
  type Refs,
} from '../positions';

const MEASURE_NOTES: Readonly<Record<Measure, string>> = { continuous: 'непрерывная', annual: 'годовая' };

/** Части результата дня: в сумме дают результат. */
const DAY_PARTS = [
  ['переоценка', 'revaluation'],
  ['реализованный', 'realized'],
  ['купонный доход', 'coupon'],
  ['комиссии', 'fees'],
] as const;

/** Панель «Лоты и оценка» по кнопке ⋯ в строке позиции: оценка, результат по периодам, лоты FIFO. */
export function LotsPanel({ line, refs, measure }: { readonly line: Line; readonly refs: Refs; readonly measure: Measure }) {
  const unit = line.kind === 'bond' ? '%' : ' ₽';
  const issuer = issuerOf(line, refs)?.name;
  const yields = yieldText(line, measure);
  return (
    <div className="lots-panel">
      <div className="cell-head">
        <span className="tag">{line.short_name}</span>
        <span className="mono">{line.secid}</span>
      </div>
      {issuer && <p className="subline">{issuer}</p>}
      <dl className="facts" aria-label={`Оценка ${line.secid}`}>
        <dt>по дате сделки, шт.</dt>
        <dd>{formatQuantity(line.trade_quantity)}</dd>
        <dt>на счёте депо, шт.</dt>
        <dd>{formatQuantity(line.settle_quantity)}</dd>
        <dt>средняя цена покупки</dt>
        <dd>{line.average_price === null ? '—' : `${formatPrice(line.average_price)}${unit}`}</dd>
        <dt>стоимость покупки, ₽</dt>
        <dd>{formatMoney(line.cost)}</dd>
        <dt>уплаченный НКД, ₽</dt>
        <dd>{formatMoney(line.accrued_paid)}</dd>
        <dt>цена оценки</dt>
        <dd>{line.price === null ? '—' : `${formatPrice(line.price)}${unit}`}</dd>
        <dt>стоимость, ₽</dt>
        <dd>{formatMoney(line.clean)}</dd>
        <dt>НКД в оценке, ₽</dt>
        <dd>{formatMoney(line.accrued_value)}</dd>
        <dt>нереализованный результат, ₽</dt>
        <dd>{formatMoney(line.unrealized)}</dd>
        <dt>результат дня, ₽</dt>
        <dd>{formatMoney(line.result)}</dd>
        {DAY_PARTS.map(([label, part]) => (
          <Fragment key={part}>
            <dt className="fact-part">{label}</dt>
            <dd>{formatMoney(line[part])}</dd>
          </Fragment>
        ))}
        {Number(line.coupon_due) !== 0 && (
          <>
            <dt>купоны к получению, ₽</dt>
            <dd>{formatMoney(line.coupon_due)}</dd>
          </>
        )}
        {Number(line.principal_due) !== 0 && (
          <>
            <dt>погашение номинала к получению, ₽</dt>
            <dd>{formatMoney(line.principal_due)}</dd>
          </>
        )}
        {yields !== null && (
          <>
            <dt>доходность, {MEASURE_NOTES[measure]}</dt>
            <dd>
              {yields}
              <div className="subline">{horizonText(line)}</div>
            </dd>
          </>
        )}
        {line.duration !== null && (
          <>
            <dt>{line.metrics_basis === 'spread' ? 'спред-дюрация' : 'модифицированная дюрация'}</dt>
            <dd>{fixed(line.duration, 2)}</dd>
          </>
        )}
        {line.flags.length > 0 && (
          <>
            <dt>оценка</dt>
            <dd className={incompleteFlags(line.flags).length ? 'danger' : 'muted'}>
              {line.flags.map((f) => FLAG_LABELS[f]).join('; ')}
            </dd>
          </>
        )}
      </dl>

      <h3>Результат по периодам, ₽</h3>
      <div className="positions-scroll">
        <table className="data positions-table" aria-label={`Результат ${line.secid}`}>
          <thead>
            <tr>
              <th />
              {PERIOD_KEYS.map((period) => (
                <th key={period} className="num">
                  {PERIOD_LABELS[period]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(['result', 'realized'] as const).map((part) => (
              <tr key={part}>
                <td>{PART_LABELS[part]}</td>
                {PERIOD_KEYS.map((period) => (
                  <td key={period} className="num">
                    {formatMoney(periodValue(line, period, part))}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>Лоты FIFO</h3>
      {line.lots.length === 0 ? (
        <p className="muted">Открытых лотов нет.</p>
      ) : (
        <div className="positions-scroll">
          <table className="data positions-table" aria-label={`Лоты ${line.secid}`}>
          <thead>
            <tr>
              <th>Портфель</th>
              <th>Открыт</th>
              <th className="num">Кол-во</th>
              <th className="num">Цена</th>
              <th className="num">Стоимость, ₽</th>
            </tr>
          </thead>
          <tbody>
            {line.lots.map((lot) => (
              <tr key={`${lot.portfolio_code}-${lot.trade_id}`}>
                <td>{lot.portfolio_code}</td>
                <td>{formatTime(lot.opened_at)}</td>
                <td className="num">{formatQuantity(lot.quantity)}</td>
                <td className="num">
                  {formatPrice(lot.price)}
                  {unit}
                </td>
                <td className="num">{formatMoney(lot.cost)}</td>
              </tr>
            ))}
          </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
