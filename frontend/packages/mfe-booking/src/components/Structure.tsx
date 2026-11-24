import type { ReactNode } from 'react';

import type { Line } from '../api';
import { formatDate, formatMoney, plural, sumMoney } from '../format';
import {
  BREAKDOWN_KEY,
  breakdown,
  compactMoney,
  marketValue,
  shareText,
  type Bucket,
  type Refs,
} from '../positions';
import { useStored } from '../storage';

/** Щелчок по группе: таблица позиций только с её бумагами. */
export type Pick = (label: string, ids: readonly string[]) => void;

/**
 * Структура портфеля (решение 165): MV с НКД по эмитентам, типам бумаг, классификатору
 * справочника и рейтингам — ранжированными столбиками одного цвета, с суммой и долей.
 */
export function StructureView({
  lines,
  date,
  refs,
  onPick,
}: {
  readonly lines: readonly Line[];
  readonly date: string;
  readonly refs: Refs;
  readonly onPick: Pick;
}) {
  const { classifiers } = refs;
  const [chosen, setChosen] = useStored(BREAKDOWN_KEY, (raw) => raw ?? '');
  // По умолчанию — первый признак эмитента (сектор), если его нет — первый классификатор.
  const classifier =
    classifiers.find((c) => c.id === chosen) ?? classifiers.find((c) => c.target === 'issuer') ?? classifiers[0] ?? null;
  return (
    <section className="structure" aria-label="Структура портфеля">
      <p className="subline">
        MV с НКД, ₽ · облигации и паи фондов на {formatDate(date)} · всего {compactMoney(sumMoney(lines.map(marketValue)))}
      </p>
      <div className="structure-grid">
        <BarsCard
          title="По эмитентам"
          dimension="Эмитент"
          note="крупнейшие и прочие"
          buckets={breakdown(lines, 'issuer', refs, null)}
          onPick={onPick}
        />
        <BarsCard
          title="По типам бумаг"
          dimension="Тип"
          note="фикс. купон, флоатеры, линкеры"
          buckets={breakdown(lines, 'type', refs, null)}
          onPick={onPick}
        />
        {classifier === null ? (
          <div className="structure-card">
            <h3>По классификатору</h3>
            <p className="muted">Классификаторов нет: справочник недоступен или признаки не заведены.</p>
          </div>
        ) : (
          <BarsCard
            title="По классификатору"
            dimension={classifier.name}
            control={
              <select aria-label="Классификатор" value={classifier.id} onChange={(e) => setChosen(e.target.value)}>
                {classifiers.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            }
            buckets={breakdown(lines, 'class', refs, classifier)}
            onPick={onPick}
          />
        )}
        <BarsCard
          title="По рейтингам"
          dimension="Рейтинг"
          note="по шкале, сверху — высший"
          buckets={breakdown(lines, 'rating', refs, null)}
          onPick={onPick}
        />
      </div>
    </section>
  );
}

function BarsCard({
  title,
  dimension,
  note = '',
  control,
  buckets,
  onPick,
}: {
  readonly title: string;
  /** Название признака — для подписи фильтра: «Эмитент: Банк А». */
  readonly dimension: string;
  readonly note?: string;
  readonly control?: ReactNode;
  readonly buckets: readonly Bucket[];
  readonly onPick: Pick;
}) {
  // Общая ось от самой короткой позиции до самой большой: ноль — там, где кончаются минусы.
  const low = Math.min(0, ...buckets.map((b) => Number(b.value)));
  const high = Math.max(0, ...buckets.map((b) => Number(b.value)));
  const span = Math.max(high - low, 1);
  const zero = (-low / span) * 100;
  return (
    <div className="structure-card">
      <div className="structure-head">
        <h3>{title}</h3>
        {control ?? <span className="subline">{note}</span>}
      </div>
      <ul className="bars" aria-label={title}>
        {buckets.map((b) => {
          const value = Number(b.value);
          const width = (Math.abs(value) / span) * 100;
          const info = `${b.label}: ${formatMoney(b.value)} ₽, ${shareText(b.share)}, ${plural(b.ids.length, 'бумага', 'бумаги', 'бумаг')}`;
          return (
            <li key={`${b.tone}:${b.key}`}>
              <button
                type="button"
                className={`bar-row bar-${b.tone}`}
                aria-label={info}
                onClick={() => onPick(`${dimension}: ${b.label}`, b.ids)}
              >
                <span className="bar-label">{b.label}</span>
                <span className="bar-track">
                  <span className="bar-zero" style={{ left: `${zero}%` }} />
                  <span
                    className={value < 0 ? 'bar-fill bar-negative' : 'bar-fill'}
                    style={{ left: `${value < 0 ? zero - width : zero}%`, width: `${width}%` }}
                  />
                </span>
                {/* Сумма в подсказке не рвётся между разрядами. */}
                <span className="bar-tip" aria-hidden="true">
                  {info.replace(/(\d) (?=\d)/g, '$1\u00a0')}
                </span>
                <span className="bar-value">{compactMoney(b.value)}</span>
                <span className="bar-share">{shareText(b.share)}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
