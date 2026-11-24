import type { CouponForecast, Decimal, Ladder, LadderDay, LadderMovement, MovementKind } from './api';
import { forecastBasis, formatMoney, formatQuantity, movementLabel, plural, sumMoney } from './format';

/**
 * План по расчётам (решение 166): движения дня — строками «что пришло» и «что ушло»,
 * сделки по бумаге, РЕПО по сроку и части, комиссии — внутри своей строки.
 * Здесь — только правила; что и как нарисовать, решает страница.
 */

export type Category = 'repo' | 'deal' | 'payment' | 'units' | 'expense' | 'transfer' | 'fee';

/** Порядок групп в панели дня. */
export const CATEGORY_LABELS: Readonly<Record<Category, string>> = {
  repo: 'РЕПО',
  deal: 'Сделки',
  payment: 'Купоны и погашения',
  units: 'Паи',
  expense: 'Расходы и вознаграждение',
  transfer: 'Переводы',
  fee: 'Комиссии',
};

export interface PlanItem {
  readonly key: string;
  readonly category: Category;
  /** Метка у строки: «РЕПО», «сделки», «купон». */
  readonly tag: string;
  readonly label: string;
  /** Мелко под строкой: комиссия, прогноз купона. */
  readonly note: string | null;
  /** Со знаком: приход +, расход −. */
  readonly cash: Decimal;
  readonly movements: readonly LadderMovement[];
}

const PAYMENT_TAGS: Partial<Readonly<Record<MovementKind, string>>> = {
  coupon: 'купон',
  coupon_forecast: 'купон, прогноз',
  amortization: 'амортизация',
  redemption: 'погашение',
  payment_unknown: 'выплата',
};

const REPO_LEGS: Partial<Readonly<Record<MovementKind, string>>> = {
  repo_first: 'первая часть',
  repo_second: 'вторая часть',
  repo_contribution: 'взнос',
};

/** Срок РЕПО: 1 торговый день — «овернайт». */
export function termLabel(term: number | null): string {
  if (term === null) {
    return 'срок неизвестен';
  }
  return term === 1 ? 'овернайт' : plural(term, 'день', 'дня', 'дней');
}

function isCash(value: Decimal): boolean {
  return Number(value) !== 0;
}

interface Draft {
  key: string;
  category: Category;
  tag: string;
  label: (members: readonly LadderMovement[]) => string;
  movements: LadderMovement[];
}

/**
 * Денежные движения дня — строками. Комиссия сделки идёт в строку своей сделки:
 * РЕПО — в первую часть, купля-продажа — в бумагу.
 */
export function dayItems(day: LadderDay, ladder: Ladder): PlanItem[] {
  const name = (id: string | null) => {
    const found = ladder.instruments.find((i) => i.instrument_id === id);
    return found?.short_name ?? id ?? '—';
  };
  const drafts = new Map<string, Draft>();
  const put = (key: string, draft: Omit<Draft, 'key' | 'movements'>, movement: LadderMovement) => {
    const found = drafts.get(key) ?? { key, ...draft, movements: [] };
    found.movements.push(movement);
    drafts.set(key, found);
  };
  for (const m of day.movements.filter((movement) => isCash(movement.cash))) {
    const leg = REPO_LEGS[m.kind];
    if (leg !== undefined || (m.kind === 'fee' && m.repo_term !== null)) {
      const part = leg ?? 'первая часть';
      put(
        `repo:${m.repo_term}:${part}`,
        {
          category: 'repo',
          tag: 'РЕПО',
          label: (members) => {
            const deals = new Set(members.filter((x) => x.kind !== 'fee').map((x) => x.trade_id)).size;
            return `${termLabel(m.repo_term)} · ${part}${deals > 1 ? ` · ${plural(deals, 'сделка', 'сделки', 'сделок')}` : ''}`;
          },
        },
        m,
      );
    } else if (m.kind === 'settlement' || (m.kind === 'fee' && m.trade_id !== null)) {
      put(`deal:${m.instrument_id}`, { category: 'deal', tag: 'сделки', label: (members) => dealLabel(name(m.instrument_id), members) }, m);
    } else if (PAYMENT_TAGS[m.kind] !== undefined) {
      put(`pay:${m.kind}:${m.instrument_id}`, { category: 'payment', tag: PAYMENT_TAGS[m.kind]!, label: () => name(m.instrument_id) }, m);
    } else if (m.kind === 'fee') {
      put('fee', { category: 'fee', tag: 'комиссия', label: () => 'комиссии брокера' }, m);
    } else {
      put(`other:${m.kind}`, { category: categoryOf(m.kind), tag: CATEGORY_TAGS[categoryOf(m.kind)], label: () => movementLabel(m) }, m);
    }
  }
  const items = [...drafts.values()].map((draft) => ({
    key: draft.key,
    category: draft.category,
    tag: draft.tag,
    label: draft.label(draft.movements),
    note: noteOf(draft, day, ladder.forecasts),
    cash: sumMoney(draft.movements.map((m) => m.cash)),
    movements: draft.movements,
  }));
  return items.sort((a, b) => Math.abs(Number(b.cash)) - Math.abs(Number(a.cash)) || a.label.localeCompare(b.label, 'ru'));
}

const CATEGORY_TAGS: Readonly<Record<Category, string>> = {
  repo: 'РЕПО',
  deal: 'сделки',
  payment: 'выплата',
  units: 'паи',
  expense: 'расходы',
  transfer: 'перевод',
  fee: 'комиссия',
};

function categoryOf(kind: MovementKind): Category {
  if (kind.startsWith('unit_')) {
    return 'units';
  }
  return kind.startsWith('transfer_') ? 'transfer' : 'expense';
}

/** «Банк А 1P-02 · 4 покупки, +160 шт.»; только комиссия — «… · комиссия». */
function dealLabel(name: string, members: readonly LadderMovement[]): string {
  const settled = members.filter((m) => m.kind === 'settlement');
  if (settled.length === 0) {
    return `${name} · комиссия`;
  }
  const count = new Set(settled.map((m) => m.trade_id)).size;
  const bought = settled.every((m) => m.quantity > 0);
  const sold = settled.every((m) => m.quantity < 0);
  const what = bought
    ? plural(count, 'покупка', 'покупки', 'покупок')
    : sold
      ? plural(count, 'продажа', 'продажи', 'продаж')
      : plural(count, 'сделка', 'сделки', 'сделок');
  const net = settled.reduce((total, m) => total + m.quantity, 0);
  return `${name} · ${what}${net === 0 ? '' : `, ${net > 0 ? '+' : ''}${formatQuantity(net)} шт.`}`;
}

/** Комиссии в строке сделки; прогноз купона — из какой ставки. */
function noteOf(draft: Draft, day: LadderDay, forecasts: readonly CouponForecast[]): string | null {
  const fees = draft.movements.filter((m) => m.kind === 'fee');
  if (draft.category === 'repo' || draft.category === 'deal') {
    if (fees.length === 0 || fees.length === draft.movements.length) {
      return null;
    }
    const total = formatMoney(sumMoney(fees.map((m) => m.cash)).replace('-', ''));
    return draft.category === 'repo' ? `комиссия ${total}` : `в т.ч. комиссии ${total}`;
  }
  const forecast = draft.movements.some((m) => m.kind === 'coupon_forecast')
    ? forecasts.find((f) => f.instrument_id === draft.movements[0]!.instrument_id && f.date === day.date)
    : undefined;
  return forecast ? forecastBasis(forecast) : null;
}

export interface DaySummary {
  readonly incoming: readonly PlanItem[];
  readonly outgoing: readonly PlanItem[];
  readonly inflow: Decimal;
  readonly outflow: Decimal;
  /** Деньги на начало дня: на конец минус движения дня. */
  readonly opening: Decimal;
}

export function summarize(day: LadderDay, ladder: Ladder): DaySummary {
  const items = dayItems(day, ladder);
  const incoming = items.filter((i) => Number(i.cash) > 0);
  const outgoing = items.filter((i) => Number(i.cash) < 0);
  const inflow = sumMoney(incoming.map((i) => i.cash));
  const outflow = sumMoney(outgoing.map((i) => i.cash));
  return { incoming, outgoing, inflow, outflow, opening: sumMoney([day.cash, negate(inflow), negate(outflow)]) };
}

function negate(value: Decimal): Decimal {
  return value.startsWith('-') ? value.slice(1) : `-${value}`;
}

// --- бумаги дня ---------------------------------------------------------------------

/** Бумага на счёте на конец дня; нет строки — ноль. */
export function held(day: LadderDay | undefined, instrumentId: string): number {
  return day?.securities.find((s) => s.instrument_id === instrumentId)?.quantity ?? 0;
}

/** Сколько бумаг пришло и ушло за день — по каждой бумаге, всеми портфелями счёта. */
export function moved(day: LadderDay): Map<string, number> {
  const found = new Map<string, number>();
  for (const m of day.movements) {
    if (m.instrument_id !== null && m.quantity) {
      found.set(m.instrument_id, (found.get(m.instrument_id) ?? 0) + m.quantity);
    }
  }
  return found;
}

export interface SecurityMark {
  readonly id: string;
  readonly quantity: number;
  readonly balance: number;
}

/** Бумаги дня: что пришло или ушло, и все, которых не хватает, — даже без движения. */
export function securityMarks(day: LadderDay): SecurityMark[] {
  const marks = [...moved(day)].map(([id, quantity]) => ({ id, quantity, balance: held(day, id) }));
  const short = day.securities
    .filter((s) => s.quantity < 0 && !marks.some((m) => m.id === s.instrument_id))
    .map((s) => ({ id: s.instrument_id, quantity: 0, balance: s.quantity }));
  return [...marks, ...short];
}

export function isShort(day: LadderDay): boolean {
  return Number(day.cash) < 0 || day.securities.some((s) => s.quantity < 0);
}

// --- строки таблицы: тихие дни свёрнуты -----------------------------------------------

export type PlanRow =
  | { readonly kind: 'day'; readonly day: LadderDay }
  | { readonly kind: 'quiet'; readonly first: LadderDay; readonly last: LadderDay };

/**
 * Дни без движений подряд — одной строкой; сегодня не сворачивается. День без движений
 * не создаёт и не углубляет дефицит — тянущийся дефицит видно в свёрнутой строке.
 */
export function planRows(ladder: Ladder): PlanRow[] {
  const rows: PlanRow[] = [];
  for (const day of ladder.days) {
    const quiet = day.movements.length === 0 && day.date !== ladder.today;
    const last = rows.at(-1);
    if (quiet && last?.kind === 'quiet') {
      rows[rows.length - 1] = { ...last, last: day };
    } else {
      rows.push(quiet ? { kind: 'quiet', first: day, last: day } : { kind: 'day', day });
    }
  }
  return rows;
}

// --- плитки --------------------------------------------------------------------------

export interface Outlook {
  /** Деньги на конец сегодня или последнего дня до него. */
  readonly now: LadderDay | undefined;
  /** Самый низкий остаток с сегодня до горизонта. */
  readonly lowest: LadderDay | undefined;
  readonly horizon: string | undefined;
  /** Дни с сегодня, когда денег меньше нуля. */
  readonly cashShort: readonly LadderDay[];
  /** Бумаги, которых с сегодня не хватает, — с первым таким днём. */
  readonly securitiesShort: readonly { readonly id: string; readonly from: string }[];
}

export function outlook(ladder: Ladder): Outlook {
  const ahead = ladder.days.filter((d) => d.date >= ladder.today);
  const lowest = ahead.reduce<LadderDay | undefined>(
    (low, d) => (low === undefined || Number(d.cash) < Number(low.cash) ? d : low),
    undefined,
  );
  const securitiesShort: { id: string; from: string }[] = [];
  for (const day of ahead) {
    for (const s of day.securities) {
      if (s.quantity < 0 && !securitiesShort.some((x) => x.id === s.instrument_id)) {
        securitiesShort.push({ id: s.instrument_id, from: day.date });
      }
    }
  }
  return {
    now: [...ladder.days].reverse().find((d) => d.date <= ladder.today),
    lowest,
    horizon: ladder.days.at(-1)?.date,
    cashShort: ahead.filter((d) => Number(d.cash) < 0),
    securitiesShort,
  };
}
