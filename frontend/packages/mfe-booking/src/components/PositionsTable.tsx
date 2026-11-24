import { Fragment, useEffect, useState, type CSSProperties, type ReactNode } from 'react';

import type { Decimal, Line, Period, PeriodTotal } from '../api';
import { formatPrice, formatQuantity, plural, sumMoney } from '../format';
import {
  BOND_TYPE_LABELS,
  bondType,
  classValue,
  durationOf,
  dv01Of,
  fixed,
  horizonText,
  issuerOf,
  PART_LABELS,
  PERIOD_LABELS,
  periodValue,
  pickRating,
  rubles,
  signed,
  yieldText,
  type Group,
  type Measure,
  type Part,
  type PeriodKey,
  type Refs,
} from '../positions';

/** Что нужно строкам таблицы помимо самих бумаг. */
export interface TableContext {
  readonly refs: Refs;
  readonly measure: Measure;
  /** Позиции нескольких портфелей — в подписи бумаги её портфели. */
  readonly several: boolean;
  readonly onLots: (line: Line) => void;
}

/** Колонки только облигаций: у пая — прочерк. */
const BOND_ONLY = new Set(['yield', 'duration', 'dv01', 'rating', 'type']);
const MEASURE_NOTES: Readonly<Record<Measure, string>> = { continuous: 'непрерывная', annual: 'годовая' };

function isNumeric(id: string): boolean {
  return !['rating', 'type'].includes(id) && !id.startsWith('class:');
}

function periodOf(id: string): { readonly part: Part; readonly period: PeriodKey } | null {
  const [part, period] = id.split(':');
  return part === 'result' || part === 'realized' ? { part, period: period as PeriodKey } : null;
}

/**
 * Бумаги блока «Облигации» или «Паи фондов» (решение 164): колонки — какие выбраны,
 * в выбранном порядке; у группы — итоги; внизу — проданные в периоде.
 */
export function PositionsTable({
  label,
  groups,
  grouped,
  columns,
  closed = null,
  context,
}: {
  readonly label: string;
  readonly groups: readonly Group[];
  readonly grouped: boolean;
  readonly columns: readonly string[];
  /** Результат бумаг, которых в позиции уже нет. */
  readonly closed?: Readonly<Record<Period, PeriodTotal>> | null;
  readonly context: TableContext;
}) {
  return (
    <div className="positions-scroll">
      <table className="data positions-table" aria-label={label}>
        <thead>
          <tr>
            <th>Бумага</th>
            {columns.map((id) => (
              <th key={id} className={isNumeric(id) ? 'num' : undefined}>
                <Header id={id} context={context} />
              </th>
            ))}
            <th aria-label="Действия" />
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => (
            <Fragment key={group.key}>
              {grouped && (
                <tr className="group-row">
                  <td>
                    {group.label} · {plural(group.lines.length, 'бумага', 'бумаги', 'бумаг')}
                  </td>
                  {columns.map((id) => (
                    <td key={id} className="num">
                      {total(id, group.lines, context.measure)}
                    </td>
                  ))}
                  <td />
                </tr>
              )}
              {group.lines.map((line) => (
                <PositionRow key={line.instrument_id} line={line} columns={columns} context={context} />
              ))}
            </Fragment>
          ))}
          {closed && <ClosedRow closed={closed} columns={columns} />}
        </tbody>
      </table>
    </div>
  );
}

function Header({ id, context }: { readonly id: string; readonly context: TableContext }) {
  const period = periodOf(id);
  if (period !== null) {
    return (
      <>
        <span className="th-kicker">{PART_LABELS[period.part]}</span>
        {PERIOD_LABELS[period.period]}
      </>
    );
  }
  const note = <span className="th-note">{MEASURE_NOTES[context.measure]}</span>;
  switch (id) {
    case 'quantity':
      return <>Кол-во, шт.</>;
    case 'settle_quantity':
      return <>На счёте депо, шт.</>;
    case 'price':
      return <>Цена</>;
    case 'value':
      return <>Стоимость, ₽</>;
    case 'accrued':
      return <>НКД, ₽</>;
    case 'unrealized':
      return <>Нереализ., ₽</>;
    case 'yield':
      return <>Доходность{note}</>;
    case 'duration':
      return <>Дюрация{note}</>;
    case 'dv01':
      return <>DV01, ₽{note}</>;
    case 'rating':
      return <>Рейтинг</>;
    case 'type':
      return <>Тип</>;
    default:
      return <>{context.refs.classifiers.find((c) => `class:${c.id}` === id)!.name}</>;
  }
}

function Signed({ value }: { readonly value: Decimal }) {
  const { text, tone } = signed(value);
  return <span className={tone || undefined}>{text}</span>;
}

/** Итог группы — по колонкам, которые складываются. */
function total(id: string, lines: readonly Line[], measure: Measure): ReactNode {
  const sum = (pick: (line: Line) => Decimal | null) => sumMoney(lines.map((line) => pick(line) ?? '0'));
  const period = periodOf(id);
  if (period !== null) {
    return <Signed value={sum((line) => periodValue(line, period.period, period.part))} />;
  }
  switch (id) {
    case 'value':
      return rubles(sum((line) => line.clean));
    case 'accrued':
      return rubles(sum((line) => line.accrued_value));
    case 'unrealized':
      return <Signed value={sum((line) => line.unrealized)} />;
    case 'dv01':
      return rubles(sum((line) => dv01Of(line, measure)));
    default:
      return null;
  }
}

function PositionRow({
  line,
  columns,
  context,
}: {
  readonly line: Line;
  readonly columns: readonly string[];
  readonly context: TableContext;
}) {
  const portfolios = context.several ? [...new Set(line.lots.map((lot) => lot.portfolio_code))].join(', ') : '';
  const subline = [issuerOf(line, context.refs)?.name, portfolios].filter(Boolean).join(' · ');
  return (
    <tr>
      <td className="p-paper">
        <div className="cell-head">
          <span className="tag">{line.short_name}</span>
          <span className="mono">{line.secid}</span>
        </div>
        {subline && <div className="subline">{subline}</div>}
      </td>
      {columns.map((id) => (
        <td key={id} className={isNumeric(id) ? 'num' : undefined}>
          {line.kind !== 'bond' && BOND_ONLY.has(id) ? '—' : <Cell id={id} line={line} context={context} />}
        </td>
      ))}
      <td className="p-actions">
        <RowMenu line={line} onLots={context.onLots} />
      </td>
    </tr>
  );
}

function Cell({ id, line, context }: { readonly id: string; readonly line: Line; readonly context: TableContext }) {
  const { refs, measure } = context;
  const period = periodOf(id);
  if (period !== null) {
    return <Signed value={periodValue(line, period.period, period.part)} />;
  }
  switch (id) {
    case 'quantity':
      return <>{formatQuantity(line.trade_quantity)}</>;
    case 'settle_quantity':
      return <>{formatQuantity(line.settle_quantity)}</>;
    case 'price':
      return (
        <>
          {line.price === null ? '—' : `${formatPrice(line.price)}${line.kind === 'bond' ? '%' : ' ₽'}`}
          {line.flags.includes('stale_price') && (
            <span className="danger" title="Цена не за этот день">
              {' '}
              *
            </span>
          )}
        </>
      );
    case 'value':
      return <>{rubles(line.clean)}</>;
    case 'accrued':
      return <>{rubles(line.accrued_value)}</>;
    case 'unrealized':
      return <Signed value={line.unrealized} />;
    case 'yield': {
      const text = yieldText(line, measure);
      return text === null ? (
        <>—</>
      ) : (
        <>
          {text}
          <div className="subline">{horizonText(line)}</div>
        </>
      );
    }
    case 'duration': {
      const duration = durationOf(line, measure);
      return <>{duration === null ? '—' : fixed(duration, 2)}</>;
    }
    case 'dv01':
      return <>{rubles(dv01Of(line, measure))}</>;
    case 'rating': {
      const rating = pickRating(line, refs);
      if (rating.kind !== 'rated') {
        return <span className="muted">{rating.kind === 'unrated' ? 'нет рейтинга' : 'нет данных'}</span>;
      }
      return (
        <>
          {rating.rating}
          <div className="subline">
            {rating.agency}
            {rating.issuer && ' · эмитента'}
          </div>
        </>
      );
    }
    case 'type': {
      const type = bondType(line, refs);
      return <>{type === null ? '—' : BOND_TYPE_LABELS[type]}</>;
    }
    default: {
      const classifier = refs.classifiers.find((c) => `class:${c.id}` === id)!;
      return <>{classValue(line, classifier, refs) ?? '—'}</>;
    }
  }
}

/** Проданные в периоде: результат бумаг, которых в позиции уже нет, — если он есть. */
function ClosedRow({
  closed,
  columns,
}: {
  readonly closed: Readonly<Record<Period, PeriodTotal>>;
  readonly columns: readonly string[];
}) {
  const valueOf = (id: string): Decimal | null => {
    const period = periodOf(id);
    return period === null || period.period === 'day' ? null : closed[period.period][period.part];
  };
  if (!columns.some((id) => Number(valueOf(id) ?? 0) !== 0)) {
    return null;
  }
  return (
    <tr className="closed-row">
      <td className="subline">Проданные в периоде</td>
      {columns.map((id) => {
        const value = valueOf(id);
        return (
          <td key={id} className="num">
            {value !== null && <Signed value={value} />}
          </td>
        );
      })}
      <td />
    </tr>
  );
}

/**
 * Кнопка ⋯ в конце строки: меню действий с бумагой. Меню — поверх страницы у кнопки:
 * внутри прокручиваемой вбок таблицы его обрезало бы у нижней строки.
 */
function RowMenu({ line, onLots }: { readonly line: Line; readonly onLots: (line: Line) => void }) {
  const [place, setPlace] = useState<CSSProperties | null>(null);
  const open = place !== null;
  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const close = () => setPlace(null);
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        close();
      }
    };
    document.addEventListener('keydown', escape);
    // Только прокрутка страницы: таблица внутри сдвигается и от фокуса на кнопке.
    window.addEventListener('scroll', close);
    return () => {
      document.removeEventListener('keydown', escape);
      window.removeEventListener('scroll', close);
    };
  }, [open]);
  const toggle = (button: HTMLElement) => {
    const box = button.getBoundingClientRect();
    setPlace(open ? null : { top: box.bottom + 4, right: window.innerWidth - box.right });
  };
  return (
    <div className="row-menu">
      <button
        type="button"
        className="icon-button row-menu-button"
        aria-label={`Действия: ${line.short_name}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(event) => toggle(event.currentTarget)}
      >
        ⋯
      </button>
      {open && (
        <>
          <div className="menu-scrim" aria-hidden="true" onClick={() => setPlace(null)} />
          <div className="row-menu-list" role="menu" aria-label={`Действия: ${line.short_name}`} style={place}>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setPlace(null);
                onLots(line);
              }}
            >
              Лоты и оценка
            </button>
          </div>
        </>
      )}
    </div>
  );
}
