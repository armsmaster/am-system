import type { Ladder, LadderDay } from '../api';
import { formatDate, formatMoney, weekday } from '../format';
import type { DaySummary } from '../plan';

/** Больше дней — подписи только у понедельников и сегодня: иначе они слипаются. */
const LABEL_EVERY_DAY = 31;

/**
 * Деньги на конец каждого дня (решение 166): столбик от нуля вверх — остаток, вниз —
 * дефицит. Прошлые дни светлее, сегодня — в рамке. Щелчок — движения дня.
 */
export function PlanChart({
  ladder,
  summaries,
  onOpen,
}: {
  readonly ladder: Ladder;
  readonly summaries: ReadonlyMap<string, DaySummary>;
  readonly onOpen: (day: LadderDay) => void;
}) {
  const values = ladder.days.map((d) => Number(d.cash));
  const high = Math.max(0, ...values);
  const low = Math.min(0, ...values);
  const span = Math.max(high - low, 1);
  const every = ladder.days.length <= LABEL_EVERY_DAY;
  return (
    <div className="plan-chart">
      <div className="plan-chart-head">
        <h3>Деньги на конец дня</h3>
        <span className="subline">светлые — факт · в рамке — сегодня · красные — дефицит</span>
      </div>
      <div className="plan-chart-plot" role="group" aria-label="Деньги на конец дня">
        {ladder.days.map((d) => {
          const value = Number(d.cash);
          const summary = summaries.get(d.date)!;
          const info =
            `${formatDate(d.date).slice(0, 5)} ${weekday(d.date)}: на конец дня ${formatMoney(d.cash)} ₽, ` +
            `поступило ${formatMoney(summary.inflow)}, списано ${formatMoney(summary.outflow.replace('-', ''))}`;
          const classes = ['plan-col', d.date < ladder.today ? 'past' : null, d.date === ladder.today ? 'today' : null];
          return (
            <button
              key={d.date}
              type="button"
              className={classes.filter(Boolean).join(' ')}
              aria-label={info}
              onClick={() => onOpen(d)}
            >
              <span className="plan-col-up" style={{ height: `${(high / span) * 100}%` }}>
                {value > 0 && <span className="plan-bar" style={{ height: `${(value / high) * 100}%` }} />}
              </span>
              <span className="plan-col-down" style={{ height: `${(-low / span) * 100}%` }}>
                {value < 0 && <span className="plan-bar plan-bar-short" style={{ height: `${(value / low) * 100}%` }} />}
              </span>
              <span className="plan-col-label">
                {every || weekday(d.date) === 'пн' || d.date === ladder.today ? d.date.slice(8, 10) : ''}
              </span>
              <span className="plan-col-tip" aria-hidden="true">
                {info}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
