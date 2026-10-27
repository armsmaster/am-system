import { ErrorNote, useAction } from '@am/ui';
import { useState, type FormEvent } from 'react';

import { PRICE_TYPES, type MarketdataApi, type PriceType, type Rule } from '../api';
import { PRICE_LABELS } from '../format';

/**
 * Правило оценки бумаги: типы цен по порядку, параметры DEMA, порог разброса.
 * Действует с момента сохранения (решение 70); оценка на прошлый момент берёт
 * правило того времени.
 */
export function RuleForm({
  id,
  rule,
  timeframes,
  api,
  onDone,
}: {
  readonly id: string;
  readonly rule: Rule;
  /** Таймфреймы баров, которые скрипт присылал по бумаге, — подсказка. */
  readonly timeframes: readonly number[];
  readonly api: MarketdataApi;
  readonly onDone: () => void;
}) {
  const [types, setTypes] = useState<readonly PriceType[]>(rule.price_types);
  const [timeframe, setTimeframe] = useState(rule.dema_timeframe?.toString() ?? '');
  const [halfLife, setHalfLife] = useState(rule.dema_half_life ?? '');
  const [threshold, setThreshold] = useState(String(rule.spread_threshold_bp));
  const [comment, setComment] = useState('');
  const missing = PRICE_TYPES.filter((t) => !types.includes(t));
  const [adding, setAdding] = useState<PriceType>(missing[0] ?? 'vwap');
  const { run, error, busy } = useAction(onDone);
  const withDema = types.includes('dema') || timeframe !== '';

  function move(index: number, step: -1 | 1) {
    const next = [...types];
    [next[index], next[index + step]] = [next[index + step]!, next[index]!];
    setTypes(next);
  }

  function add() {
    const next = [...types, adding];
    setTypes(next);
    setAdding(PRICE_TYPES.find((t) => !next.includes(t)) ?? 'vwap');
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(() =>
      api.setRule(id, {
        price_types: types,
        dema_timeframe: withDema && timeframe ? Number(timeframe) : null,
        dema_half_life: withDema && halfLife ? halfLife.replace(',', '.') : null,
        spread_threshold_bp: Number(threshold),
        comment: comment.trim(),
      }),
    );
  }

  return (
    <form onSubmit={submit} aria-label="Правило оценки">
      <h3>Правило оценки</h3>
      <p className="muted">
        В оценку идёт первый тип, у которого есть цена за сегодня. Ни у одного нет — последняя
        цена первого типа с пометкой «устарела».
      </p>
      <ol className="rule-types" aria-label="Типы цен по порядку">
        {types.map((type, index) => (
          <li key={type}>
            {PRICE_LABELS[type]}{' '}
            <button
              type="button"
              aria-label={`Выше: ${PRICE_LABELS[type]}`}
              disabled={index === 0}
              onClick={() => move(index, -1)}
            >
              ↑
            </button>
            <button
              type="button"
              aria-label={`Ниже: ${PRICE_LABELS[type]}`}
              disabled={index === types.length - 1}
              onClick={() => move(index, 1)}
            >
              ↓
            </button>
            <button
              type="button"
              aria-label={`Убрать: ${PRICE_LABELS[type]}`}
              disabled={types.length === 1}
              onClick={() => setTypes(types.filter((t) => t !== type))}
            >
              ×
            </button>
          </li>
        ))}
      </ol>
      {missing.length > 0 && (
        <div className="inline">
          <select
            aria-label="Тип цены"
            value={adding}
            onChange={(e) => setAdding(e.target.value as PriceType)}
          >
            {missing.map((t) => (
              <option key={t} value={t}>
                {PRICE_LABELS[t]}
              </option>
            ))}
          </select>
          <button type="button" onClick={add}>
            Добавить
          </button>
        </div>
      )}
      {withDema && (
        <fieldset>
          <legend>DEMA — сглаженная цена по барам</legend>
          <label>
            Таймфрейм баров, минут
            <input
              type="number"
              min={1}
              max={240}
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
              required
            />
          </label>
          <p className="muted hint">
            {timeframes.length
              ? `Скрипт присылал бары: ${timeframes.map((t) => `${t} мин`).join(', ')}.`
              : 'Скрипт ещё не присылал бары по этой бумаге.'}
          </p>
          <label>
            Период полураспада, минут
            <input
              inputMode="decimal"
              value={halfLife}
              onChange={(e) => setHalfLife(e.target.value)}
              required
            />
          </label>
        </fieldset>
      )}
      <label>
        Порог разброса, бп
        <input
          type="number"
          min={0}
          value={threshold}
          onChange={(e) => setThreshold(e.target.value)}
          required
        />
      </label>
      <label>
        Комментарий — почему меняем
        <textarea
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          minLength={3}
          maxLength={500}
          required
        />
      </label>
      <button type="submit" disabled={busy}>
        Сохранить правило
      </button>
      <ErrorNote message={error} />
    </form>
  );
}
