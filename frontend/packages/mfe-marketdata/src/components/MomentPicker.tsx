import { FilterChips, type Choice } from '@am/ui';

import { monthGenitive, nudge, endOfPreviousMonth, TIMES, type Moment } from '../moment';

type Kind = Moment['kind'];

/**
 * Выбор момента: «Сейчас», «Вчера», «Конец прошлого месяца» — одним нажатием;
 * «Другой момент» — дата и время с шагом 15 минут, стрелки листают по 15 минут.
 */
export function MomentPicker({
  value,
  today,
  quarter,
  onChange,
}: {
  readonly value: Moment;
  readonly today: string;
  /** С какого времени начинать «другой момент»: сейчас, округлённое до 15 минут. */
  readonly quarter: string;
  readonly onChange: (moment: Moment) => void;
}) {
  const choices: readonly Choice<Kind>[] = [
    { value: 'now', label: 'Сейчас' },
    { value: 'yesterday', label: 'Вчера' },
    { value: 'month', label: `Конец ${monthGenitive(endOfPreviousMonth(today))}` },
    { value: 'custom', label: 'Другой момент' },
  ];

  function choose(kind: Kind) {
    onChange(kind === 'custom' ? { kind, day: today, time: quarter } : { kind });
  }

  return (
    <div className="moment-picker">
      <FilterChips label="Момент" choices={choices} value={value.kind} onChange={choose} />
      {value.kind === 'custom' && (
        <div className="moment-custom">
          <input
            type="date"
            aria-label="Дата"
            value={value.day}
            max={today}
            onChange={(e) => e.target.value && onChange({ ...value, day: e.target.value })}
          />
          <button
            type="button"
            className="icon-button"
            aria-label="На 15 минут раньше"
            onClick={() => onChange({ kind: 'custom', ...nudge(value.day, value.time, -1) })}
          >
            ‹
          </button>
          <select
            aria-label="Время, МСК"
            value={value.time}
            onChange={(e) => onChange({ ...value, time: e.target.value })}
          >
            {TIMES.map((time) => (
              <option key={time} value={time}>
                {time}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="icon-button"
            aria-label="На 15 минут позже"
            onClick={() => onChange({ kind: 'custom', ...nudge(value.day, value.time, 1) })}
          >
            ›
          </button>
        </div>
      )}
    </div>
  );
}
