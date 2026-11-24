import { Link } from 'react-router';

import type { Ladder, LadderDay } from '../api';
import { formatMoney, formatQuantity, movementLabel } from '../format';
import { CATEGORY_LABELS, securityMarks, type Category, type DaySummary } from '../plan';

/** Сумма со знаком: «+8 203 089,04», «−56,16». */
export function signedMoney(value: string): string {
  return `${Number(value) > 0 ? '+' : ''}${formatMoney(value)}`;
}

/**
 * Панель дня (решение 166): деньги на начало и конец, движения по группам — каждое
 * отдельно, со ссылкой на сделку; движения без денег и бумаги дня.
 */
export function PlanDay({
  day,
  summary,
  ladder,
  basePath,
}: {
  readonly day: LadderDay;
  readonly summary: DaySummary;
  readonly ladder: Ladder;
  readonly basePath: string;
}) {
  const name = (id: string | null) => ladder.instruments.find((i) => i.instrument_id === id)?.short_name ?? id;
  const items = [...summary.incoming, ...summary.outgoing];
  const silent = day.movements.filter((m) => Number(m.cash) === 0);
  const marks = securityMarks(day);
  return (
    <div className="plan-day">
      <dl className="facts" aria-label="Деньги дня">
        <dt>на начало дня, ₽</dt>
        <dd>{formatMoney(summary.opening)}</dd>
        <dt>поступило, ₽</dt>
        <dd>{signedMoney(summary.inflow)}</dd>
        <dt>списано, ₽</dt>
        <dd>{signedMoney(summary.outflow)}</dd>
        <dt>на конец дня, ₽</dt>
        <dd className={Number(day.cash) < 0 ? 'danger' : undefined}>
          <strong>{formatMoney(day.cash)}</strong>
        </dd>
      </dl>
      {(Object.keys(CATEGORY_LABELS) as Category[]).map((category) => {
        const found = items.filter((i) => i.category === category);
        if (found.length === 0) {
          return null;
        }
        return (
          <section key={category} aria-label={CATEGORY_LABELS[category]}>
            <h3>{CATEGORY_LABELS[category]}</h3>
            {found.map((item) => (
              <div key={item.key} className="plan-day-item">
                <div className="plan-day-head">
                  <span>
                    {item.label}
                    {item.note && <span className="subline"> · {item.note}</span>}
                  </span>
                  <span className={Number(item.cash) > 0 ? 'num up' : 'num'}>{signedMoney(item.cash)}</span>
                </div>
                <ul className="plan-day-moves">
                  {item.movements.map((m, index) => (
                    <li key={index}>
                      <span>
                        {m.portfolio_code} · {movementLabel(m)}
                        {m.trade_id !== null && (
                          <>
                            {' · '}
                            <Link to={`${basePath}/trades/${m.trade_id}`}>сделка</Link>
                          </>
                        )}
                      </span>
                      <span className="num">{signedMoney(m.cash)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </section>
        );
      })}
      {silent.length > 0 && (
        <section aria-label="Без денег">
          <h3>Без денег</h3>
          <ul className="plan-day-moves">
            {silent.map((m, index) => (
              <li key={index}>
                <span>
                  {m.portfolio_code} · {movementLabel(m)}
                  {m.instrument_id !== null && ` · ${name(m.instrument_id)}`}
                </span>
                <span className="num">{m.quantity ? `${m.quantity > 0 ? '+' : ''}${formatQuantity(m.quantity)} шт.` : ''}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {marks.length > 0 && (
        <section aria-label="Бумаги дня">
          <h3>Бумаги на счёте депо</h3>
          <ul className="plan-day-moves">
            {marks.map((mark) => (
              <li key={mark.id} className={mark.balance < 0 ? 'danger' : undefined}>
                <span>{name(mark.id)}</span>
                <span className="num">
                  {mark.quantity !== 0 && `${mark.quantity > 0 ? '+' : ''}${formatQuantity(mark.quantity)} → `}
                  {mark.balance < 0 ? `не хватает ${formatQuantity(-mark.balance)}` : formatQuantity(mark.balance)} шт.
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
