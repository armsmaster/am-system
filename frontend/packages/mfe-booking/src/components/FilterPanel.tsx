import { useEffect, useRef, useState } from 'react';

import type { FilterDays, NavFilter } from '../api';
import { fitFilter, fromSlider, HALF_LIFE_RANGE, maxHalfLife, MEMORY_RANGE, toSlider } from '../nav';

/** Новые настройки уходят на сервер, когда ползунок замер на это время, — без запроса на каждый шаг. */
export const SETTLE_MS = 300;

const days = (value: number, places: number) => value.toFixed(places).replace('.', ',');
const noise = (value: string) => Number(value).toPrecision(3).replace('.', ',');

/**
 * Свои настройки фильтра Калмана в днях (решение 174). Хранятся в этом браузере, другие
 * пользователи видят свои; данные фонда не меняются. Ползунки — в логарифмах.
 */
export function FilterPanel({
  filter,
  onChange,
}: {
  readonly filter: NavFilter;
  readonly onChange: (value: FilterDays | null) => void;
}) {
  const [halfLife, setHalfLife] = useState(Number(filter.half_life));
  const [memory, setMemory] = useState(Number(filter.trend_memory));
  const pending = useRef(false);
  const commit = useRef(onChange);
  commit.current = onChange;

  useEffect(() => {
    if (!pending.current) {
      return undefined;
    }
    const timer = setTimeout(() => {
      pending.current = false;
      commit.current({ halfLife, memory });
    }, SETTLE_MS);
    return () => clearTimeout(timer);
  }, [halfLife, memory]);

  const move = (next: FilterDays) => {
    pending.current = true;
    setHalfLife(next.halfLife);
    setMemory(next.memory);
  };
  const reset = () => {
    pending.current = false;
    setHalfLife(Number(filter.standard_half_life));
    setMemory(Number(filter.standard_trend_memory));
    onChange(null);
  };
  const limit = maxHalfLife(memory);

  return (
    <section className="drawer-form filter-panel">
      <p className="hint">
        Сглаженная линия и прогноз пересчитываются по вашим настройкам. Они хранятся в этом браузере: другие
        пользователи видят свои, данные фонда не меняются.
      </p>
      <label>
        <span>
          Сглаживание: скачок наполовину забывается за <b>{days(halfLife, halfLife < 10 ? 1 : 0)} торг. дн.</b>
        </span>
        <input
          type="range"
          min={0}
          max={1000}
          value={toSlider(halfLife, HALF_LIFE_RANGE)}
          onChange={(e) => move(fitFilter(fromSlider(Number(e.target.value), HALF_LIFE_RANGE), memory))}
        />
      </label>
      <p className="hint">
        Больше — линия спокойнее, но позже замечает настоящие изменения. При памяти тренда {days(memory, 0)} дн. —
        не больше {days(limit, 1)} дн.
      </p>
      <label>
        <span>
          Память тренда: <b>{days(memory, 0)} торг. дн.</b>
        </span>
        <input
          type="range"
          min={0}
          max={1000}
          value={toSlider(memory, MEMORY_RANGE)}
          onChange={(e) => move(fitFilter(halfLife, fromSlider(Number(e.target.value), MEMORY_RANGE)))}
        />
      </label>
      <p className="hint">
        Больше — тренд, прогноз и «% годовых» устойчивее, но дольше помнят старые события.
      </p>
      <p className="hint filter-noises">
        Шумы фильтра: уровня {noise(filter.q_level)} · тренда {noise(filter.q_trend)}
      </p>
      <button type="button" disabled={filter.standard && !pending.current} onClick={reset}>
        По умолчанию: {days(Number(filter.standard_half_life), 2)} и {days(Number(filter.standard_trend_memory), 1)} дн.
      </button>
    </section>
  );
}
