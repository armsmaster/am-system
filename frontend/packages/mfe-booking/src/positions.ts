import type {
  Attributes,
  BondType,
  Classifier,
  Decimal,
  InstrumentAttributes,
  IssuerAttributes,
  Line,
  Period,
  RatingScale,
  RatingsOn,
} from './api';
import { formatDate, formatQuantity, monthStart, shiftDay, sumMoney, weekday } from './format';

/**
 * Вкладка «Позиции» (решение 164): колонки на выбор, готовые наборы, группировка
 * облигаций, рейтинг и классификаторы из справочника, быстрые даты. Здесь — только
 * правила; что и как нарисовать, решает страница.
 */

/** Капитализация доходности и спреда: непрерывная по умолчанию (решение 156). */
export type Measure = 'continuous' | 'annual';
export type FontSize = 'small' | 'normal' | 'large';
/** Тип, эмитент, без групп или классификатор справочника — ``class:<id>``. */
export type Grouping = string;
export type PeriodKey = 'day' | Period;
export type Part = 'result' | 'realized';

export const PERIOD_KEYS: readonly PeriodKey[] = ['day', 'week', 'days30', 'month', 'quarter', 'year'];

/** Заголовок колонки периода. */
export const PERIOD_LABELS: Readonly<Record<PeriodKey, string>> = {
  day: 'День',
  week: 'Неделя',
  days30: '30 дней',
  month: 'С нач. мес.',
  quarter: 'С нач. кв.',
  year: 'С нач. года',
};

/** Подпись в матрице выбора колонок — коротко. */
export const PERIOD_SHORT: Readonly<Record<PeriodKey, string>> = {
  day: 'День',
  week: 'Неделя',
  days30: '30 дней',
  month: 'Мес.',
  quarter: 'Кв.',
  year: 'Год',
};

export const PART_LABELS: Readonly<Record<Part, string>> = { result: 'Результат', realized: 'Реализованный' };

export interface ColumnOption {
  readonly id: string;
  readonly label: string;
}

export const POSITION_COLUMNS: readonly ColumnOption[] = [
  { id: 'quantity', label: 'Количество' },
  { id: 'settle_quantity', label: 'На счёте депо' },
  { id: 'price', label: 'Цена' },
  { id: 'value', label: 'Стоимость' },
  { id: 'accrued', label: 'НКД' },
  { id: 'unrealized', label: 'Нереализованный' },
];

export const RISK_COLUMNS: readonly ColumnOption[] = [
  { id: 'yield', label: 'Доходность / спред' },
  { id: 'duration', label: 'Дюрация' },
  { id: 'dv01', label: 'DV01' },
  { id: 'rating', label: 'Рейтинг' },
  { id: 'type', label: 'Тип облигации' },
];

export function periodColumn(part: Part, period: PeriodKey): string {
  return `${part}:${period}`;
}

export function classColumn(classifier: Classifier): string {
  return `class:${classifier.id}`;
}

/** Все колонки в естественном порядке: новая встаёт среди выбранных на своё место. */
export function canonicalColumns(classifiers: readonly Classifier[]): string[] {
  return [
    ...POSITION_COLUMNS.map((c) => c.id),
    ...(['result', 'realized'] as const).flatMap((part) => PERIOD_KEYS.map((p) => periodColumn(part, p))),
    ...RISK_COLUMNS.map((c) => c.id),
    ...classifiers.map(classColumn),
  ];
}

/** Название колонки — для списка «Порядок колонок». */
export function columnLabel(id: string, classifiers: readonly Classifier[]): string {
  const [head = '', tail = ''] = id.split(':');
  if (head === 'result' || head === 'realized') {
    return `${PART_LABELS[head]}: ${PERIOD_LABELS[tail as PeriodKey].toLowerCase()}`;
  }
  if (head === 'class') {
    return classifiers.find((c) => c.id === tail)?.name ?? id;
  }
  return [...POSITION_COLUMNS, ...RISK_COLUMNS].find((c) => c.id === id)?.label ?? id;
}

export type Preset = 'mine' | 'compact' | 'result' | 'risk';

export const PRESET_LABELS: Readonly<Record<Preset, string>> = {
  mine: 'Мой',
  compact: 'Компактно',
  result: 'Результат',
  risk: 'Риск',
};

/** По умолчанию: позиция, доходность, результат за день — год, рейтинг и признаки эмитента. */
export function defaultColumns(classifiers: readonly Classifier[]): string[] {
  return [
    'quantity',
    'price',
    'value',
    'yield',
    'result:day',
    'result:week',
    'result:month',
    'result:year',
    'realized:year',
    'rating',
    ...classifiers.filter((c) => c.target === 'issuer').map(classColumn),
  ];
}

export function presetColumns(preset: Exclude<Preset, 'mine'>, classifiers: readonly Classifier[]): string[] {
  switch (preset) {
    case 'compact':
      return ['value', 'yield', 'result:day', 'result:year'];
    case 'result':
      return [
        'value',
        'unrealized',
        'result:day',
        'result:week',
        'result:month',
        'result:quarter',
        'result:year',
        'realized:month',
        'realized:year',
      ];
    case 'risk':
      return ['quantity', 'price', 'value', 'yield', 'duration', 'dv01', 'rating', ...classifiers.map(classColumn)];
  }
}

/** Выбор колонок: текущий набор и свой («Мой»); null — по умолчанию. */
export interface ColumnChoice {
  readonly columns: readonly string[] | null;
  readonly custom: readonly string[] | null;
}

export const NO_CHOICE: ColumnChoice = { columns: null, custom: null };

/** Колонки, которые показать: выбранные и известные — классификатор могли удалить. */
export function shownColumns(choice: ColumnChoice, classifiers: readonly Classifier[]): string[] {
  const known = new Set(canonicalColumns(classifiers));
  return (choice.columns ?? defaultColumns(classifiers)).filter((id) => known.has(id));
}

/** Какой набор сейчас выбран: готовый, если совпадает, иначе — свой. */
export function activePreset(shown: readonly string[], classifiers: readonly Classifier[]): Preset {
  const same = (list: readonly string[]) => list.join() === shown.join();
  const named = (['compact', 'result', 'risk'] as const).find((preset) => same(presetColumns(preset, classifiers)));
  return named ?? 'mine';
}

/** Включить или выключить колонку; включённая встаёт на своё место среди выбранных. */
export function toggleColumn(shown: readonly string[], id: string, classifiers: readonly Classifier[]): string[] {
  if (shown.includes(id)) {
    return shown.filter((c) => c !== id);
  }
  // Встаёт сразу за последней выбранной колонкой, которая в естественном порядке раньше неё.
  const canonical = canonicalColumns(classifiers);
  const rank = (c: string) => canonical.indexOf(c);
  let after = -1;
  shown.forEach((c, index) => {
    if (rank(c) < rank(id)) {
      after = index;
    }
  });
  return [...shown.slice(0, after + 1), id, ...shown.slice(after + 1)];
}

/** Сдвинуть колонку на одну позицию: −1 — левее, +1 — правее. */
export function moveColumn(shown: readonly string[], id: string, step: -1 | 1): string[] {
  const from = shown.indexOf(id);
  const to = from + step;
  const next = [...shown];
  [next[from], next[to]] = [next[to]!, next[from]!];
  return next;
}

// --- справочник: эмитент, классификаторы, рейтинг, тип ------------------------------

/** Справочник на дату позиций; без него колонки справочника пустые. */
export interface Refs {
  readonly instruments: ReadonlyMap<string, InstrumentAttributes>;
  readonly issuers: ReadonlyMap<string, IssuerAttributes>;
  readonly classifiers: readonly Classifier[];
  readonly grades: readonly string[];
}

export function makeRefs(attributes: Attributes | null, scale: RatingScale | null): Refs {
  return {
    instruments: new Map(attributes?.instruments.map((i) => [i.id, i])),
    issuers: new Map(attributes?.issuers.map((i) => [i.id, i])),
    classifiers: attributes?.classifiers ?? [],
    grades: scale?.grades ?? [],
  };
}

export function issuerOf(line: Line, refs: Refs): IssuerAttributes | undefined {
  const issuerId = refs.instruments.get(line.instrument_id)?.issuer_id;
  return issuerId ? refs.issuers.get(issuerId) : undefined;
}

/** Значение классификатора бумаги или её эмитента; null — не задано. */
export function classValue(line: Line, classifier: Classifier, refs: Refs): string | null {
  const subject = classifier.target === 'instrument' ? refs.instruments.get(line.instrument_id) : issuerOf(line, refs);
  const valueId = subject?.classes[classifier.id];
  return classifier.values.find((v) => v.id === valueId)?.name ?? null;
}

export type ShownRating =
  | {
      readonly kind: 'rated';
      readonly rating: string;
      /** Ступень общей шкалы — по ней сравнивают рейтинги агентств. */
      readonly grade: string;
      readonly agency: string;
      readonly issuer: boolean;
    }
  | { readonly kind: 'unrated' }
  | { readonly kind: 'unknown' };

/**
 * Рейтинг — как в лимитах (design/09): выпуска, если есть, иначе эмитента; из рейтингов
 * нескольких агентств — высший по общей шкале.
 */
export function pickRating(line: Line, refs: Refs): ShownRating {
  const own = refs.instruments.get(line.instrument_id)?.ratings;
  const issuer = issuerOf(line, refs)?.ratings;
  const rated = (found: RatingsOn | undefined) => found?.status === 'rated' && found.ratings.length > 0;
  const source = rated(own) ? own : rated(issuer) ? issuer : undefined;
  if (source === undefined) {
    return own?.status === 'unrated' || issuer?.status === 'unrated' ? { kind: 'unrated' } : { kind: 'unknown' };
  }
  const rank = (grade: string) => {
    const index = refs.grades.indexOf(grade);
    return index === -1 ? refs.grades.length : index;
  };
  const [best] = [...source.ratings].sort((a, b) => rank(a.grade) - rank(b.grade));
  return { kind: 'rated', rating: best!.rating, grade: best!.grade, agency: best!.agency_name, issuer: source === issuer };
}

/** Тип облигации: из расчёта сервиса цен, нет расчёта — по условиям выпуска. */
export function bondType(line: Line, refs: Refs): BondType | null {
  if (line.bond_type !== null) {
    return line.bond_type;
  }
  const terms = refs.instruments.get(line.instrument_id);
  if (terms === undefined) {
    return null;
  }
  if (terms.nominal_indexation !== 'none') {
    return 'linker';
  }
  return terms.coupon_kind === 'fixed' || terms.coupon_kind === 'floater' ? terms.coupon_kind : null;
}

export const BOND_TYPE_LABELS: Readonly<Record<BondType, string>> = {
  fixed: 'фикс. купон',
  floater: 'флоатер',
  linker: 'линкер',
};

// --- группы облигаций -----------------------------------------------------------------

export interface Group {
  readonly key: string;
  readonly label: string;
  readonly lines: readonly Line[];
}

const TYPE_GROUPS: readonly { readonly key: BondType | 'none'; readonly label: string }[] = [
  { key: 'fixed', label: 'С фиксированным купоном' },
  { key: 'floater', label: 'Флоатеры' },
  { key: 'linker', label: 'Линкеры' },
  { key: 'none', label: 'Тип не определён' },
];

/** Группировка, которую можно применить: классификатор могли удалить — тогда по типу. */
export function validGrouping(grouping: Grouping, classifiers: readonly Classifier[]): Grouping {
  const fixed = ['type', 'issuer', 'none'].includes(grouping);
  return fixed || classifiers.some((c) => classColumn(c) === grouping) ? grouping : 'type';
}

export function groupLines(lines: readonly Line[], grouping: Grouping, refs: Refs): Group[] {
  if (grouping === 'none') {
    return [{ key: 'all', label: '', lines }];
  }
  if (grouping === 'type') {
    return TYPE_GROUPS.map(({ key, label }) => ({
      key,
      label,
      lines: lines.filter((line) => (bondType(line, refs) ?? 'none') === key),
    })).filter((group) => group.lines.length > 0);
  }
  const classifier = refs.classifiers.find((c) => classColumn(c) === grouping);
  const labelOf = (line: Line) =>
    classifier === undefined ? (issuerOf(line, refs)?.name ?? null) : classValue(line, classifier, refs);
  const missing = classifier === undefined ? 'Эмитент не указан' : 'Без значения';
  const labels = [...new Set(lines.map(labelOf))];
  const named = labels.filter((label): label is string => label !== null).sort((a, b) => a.localeCompare(b, 'ru'));
  const groups = named.map((label) => ({ key: label, label, lines: lines.filter((l) => labelOf(l) === label) }));
  const rest = lines.filter((line) => labelOf(line) === null);
  return rest.length > 0 ? [...groups, { key: '', label: missing, lines: rest }] : groups;
}

// --- числа ---------------------------------------------------------------------------

/** Результат или реализованный результат бумаги за период. */
export function periodValue(line: Line, period: PeriodKey, part: Part): Decimal {
  if (period === 'day') {
    return part === 'result' ? line.result : line.realized;
  }
  return line.periods[period][part];
}

/** До рубля: 21485951.37 → «21 485 951». */
export function rubles(value: Decimal | null): string {
  return value === null ? '—' : formatQuantity(Math.round(Number(value)));
}

/** Результат со знаком: «+11 333», «−564», «0» — и класс цвета. */
export function signed(value: Decimal): { readonly text: string; readonly tone: string } {
  const amount = Math.round(Number(value));
  if (amount > 0) {
    return { text: `+${formatQuantity(amount)}`, tone: 'up' };
  }
  return { text: formatQuantity(amount), tone: amount < 0 ? 'down' : '' };
}

/** Число с заданным числом знаков: 14.786146 → «14,79». */
export function fixed(value: Decimal, digits: number): string {
  return Number(value).toFixed(digits).replace('.', ',').replace('-', '−');
}

// --- быстрые даты ---------------------------------------------------------------------

const ROMAN = ['I', 'II', 'III', 'IV'];

/** Последний день прошлого месяца. */
export function previousMonthEnd(today: string): string {
  return shiftDay(monthStart(today), -1);
}

/** Последний день прошлого квартала. */
export function previousQuarterEnd(today: string): string {
  const month = Number(today.slice(5, 7));
  const first = 3 * Math.floor((month - 1) / 3) + 1;
  return shiftDay(`${today.slice(0, 4)}-${String(first).padStart(2, '0')}-01`, -1);
}

function otherYear(iso: string, today: string): string {
  return iso.slice(0, 4) === today.slice(0, 4) ? '' : ` ${iso.slice(0, 4)}`;
}

/** «Конец октября»; прошлый год — с годом: «Конец декабря 2025». */
export function monthEndLabel(iso: string, today: string): string {
  const month = new Date(`${iso}T12:00:00Z`).toLocaleDateString('ru-RU', {
    timeZone: 'UTC',
    day: 'numeric',
    month: 'long',
  });
  return `Конец ${month.split(' ')[1]}${otherYear(iso, today)}`;
}

/** «Конец III кв.»; прошлый год — с годом. */
export function quarterEndLabel(iso: string, today: string): string {
  return `Конец ${ROMAN[Math.floor((Number(iso.slice(5, 7)) - 1) / 3)]} кв.${otherYear(iso, today)}`;
}

/** Под кнопкой соседнего дня: «пн 23.11». */
export function shortDay(iso: string): string {
  return `${weekday(iso)} ${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
}

/** Соседний будний день — если календарь торгов недоступен. */
export function nearbyWeekday(iso: string, step: -1 | 1): string {
  let day = shiftDay(iso, step);
  while ([0, 6].includes(new Date(`${day}T12:00:00Z`).getUTCDay())) {
    day = shiftDay(day, step);
  }
  return day;
}

// --- доходность и риск в выбранной капитализации ----------------------------------------

/** Доходность (фиксированный купон) или спред (флоатер, линкер); null — расчёта нет. */
export function yieldText(line: Line, measure: Measure): string | null {
  const annual = measure === 'annual';
  if (line.yield_percent !== null) {
    const value = annual ? line.yield_percent : line.yield_continuous;
    return value === null ? null : `${fixed(value, 2)}%`;
  }
  if (line.spread_bp !== null) {
    const value = annual ? line.spread_bp : line.spread_continuous_bp;
    return value === null ? null : `спред ${fixed(value, 0)} бп`;
  }
  return null;
}

/** «к оферте 14.10.2027» или «к погаш. 15.05.2041». */
export function horizonText(line: Line): string {
  return `${line.horizon_kind === 'offer' ? 'к оферте' : 'к погаш.'} ${formatDate(line.horizon_date)}`;
}

/** Модифицированная (спред-) дюрация; в непрерывной — дюрация Маколея. */
export function durationOf(line: Line, measure: Measure): Decimal | null {
  return measure === 'annual' ? line.duration : line.duration_continuous;
}

/** DV01 на всю позицию, ₽ за 1 бп. */
export function dv01Of(line: Line, measure: Measure): Decimal | null {
  return measure === 'annual' ? line.dv01 : line.dv01_continuous;
}

// --- что помнит браузер -----------------------------------------------------------------

export const MEASURE_KEY = 'am.marketdata.measure';
export const SIZE_KEY = 'am.booking.positions.size';
export const GROUP_KEY = 'am.booking.positions.group';
export const COLUMNS_KEY = 'am.booking.positions.columns';

export function parseMeasure(raw: string | null): Measure {
  return raw === 'annual' ? 'annual' : 'continuous';
}

export function parseSize(raw: string | null): FontSize {
  return raw === 'small' || raw === 'large' ? raw : 'normal';
}

export function parseGrouping(raw: string | null): Grouping {
  return raw ?? 'type';
}

/** Выбор колонок из хранилища; испорченная запись — как будто её нет. */
export function parseChoice(raw: string | null): ColumnChoice {
  const list = (value: unknown) =>
    Array.isArray(value) && value.every((item) => typeof item === 'string') ? (value as string[]) : null;
  try {
    const found = JSON.parse(raw ?? 'null') as { columns?: unknown; custom?: unknown } | null;
    return { columns: list(found?.columns), custom: list(found?.custom) };
  } catch {
    return NO_CHOICE;
  }
}

// --- структура MV (решение 165) -------------------------------------------------------

export type View = 'table' | 'structure';
export type Dimension = 'issuer' | 'type' | 'class' | 'rating';
/** named — группа; rest — «Прочие»; missing — признака нет. */
export type Tone = 'named' | 'rest' | 'missing';

export const VIEW_KEY = 'am.booking.positions.view';
export const BREAKDOWN_KEY = 'am.booking.positions.breakdown';

export function parseView(raw: string | null): View {
  return raw === 'structure' ? 'structure' : 'table';
}

/** Сколько эмитентов показать отдельно; остальные — одной строкой «Прочие». */
const TOP_ISSUERS = 7;

export interface Bucket {
  readonly key: string;
  readonly label: string;
  readonly tone: Tone;
  /** MV с НКД, ₽; короткая позиция уменьшает группу. */
  readonly value: Decimal;
  /** Доля в MV всех бумаг. */
  readonly share: number;
  /** Бумаги группы — для фильтра таблицы. */
  readonly ids: readonly string[];
}

/** MV бумаги: стоимость по чистой цене и НКД в оценке. */
export function marketValue(line: Line): Decimal {
  return sumMoney([line.clean, line.accrued_value]);
}

interface Slot {
  readonly key: string;
  readonly label: string;
  readonly tone: Tone;
}

const SHARES: Slot = { key: 'shares', label: 'Паи фондов', tone: 'named' };

function slotOf(line: Line, dimension: Dimension, refs: Refs, classifier: Classifier | null): Slot {
  if (line.kind !== 'bond') {
    return SHARES;
  }
  switch (dimension) {
    case 'issuer': {
      const issuer = issuerOf(line, refs);
      return issuer ? { key: issuer.id, label: issuer.name, tone: 'named' } : { key: '', label: 'Эмитент не указан', tone: 'missing' };
    }
    case 'type': {
      const type = bondType(line, refs) ?? 'none';
      return { key: type, label: TYPE_GROUPS.find((g) => g.key === type)!.label, tone: type === 'none' ? 'missing' : 'named' };
    }
    case 'class': {
      const value = classifier === null ? null : classValue(line, classifier, refs);
      return value === null ? { key: '', label: 'Без значения', tone: 'missing' } : { key: value, label: value, tone: 'named' };
    }
    case 'rating': {
      const rating = pickRating(line, refs);
      if (rating.kind === 'rated') {
        return { key: rating.grade, label: rating.grade, tone: 'named' };
      }
      return rating.kind === 'unrated'
        ? { key: 'unrated', label: 'Нет рейтинга', tone: 'missing' }
        : { key: 'unknown', label: 'Нет данных', tone: 'missing' };
    }
  }
}

/**
 * MV бумаг по группам (решение 165). Эмитенты, типы и классификатор — от большей
 * группы к меньшей, эмитентов сверх семи — в «Прочие»; рейтинги — по шкале, сверху
 * высший. Группы «нет признака» — в конце, паи фондов — своей группой.
 */
export function breakdown(lines: readonly Line[], dimension: Dimension, refs: Refs, classifier: Classifier | null): Bucket[] {
  const total = Number(sumMoney(lines.map(marketValue)));
  const slots = new Map<string, { slot: Slot; lines: Line[] }>();
  for (const line of lines) {
    const slot = slotOf(line, dimension, refs, classifier);
    const found = slots.get(`${slot.tone}:${slot.key}`);
    if (found) {
      found.lines.push(line);
    } else {
      slots.set(`${slot.tone}:${slot.key}`, { slot, lines: [line] });
    }
  }
  const bucket = (slot: Slot, members: readonly Line[]): Bucket => {
    const value = sumMoney(members.map(marketValue));
    return { ...slot, value, share: total === 0 ? 0 : Number(value) / total, ids: members.map((l) => l.instrument_id) };
  };
  const all = [...slots.values()].map(({ slot, lines: members }) => bucket(slot, members));
  const named = all.filter((b) => b.tone === 'named');
  const missing = all.filter((b) => b.tone === 'missing');
  if (dimension === 'rating') {
    // Ступени, которых нет в шкале (или шкала не загрузилась), — после известных, по алфавиту.
    const rank = (b: Bucket) => {
      const index = refs.grades.indexOf(b.key);
      return index === -1 ? refs.grades.length : index;
    };
    const ordered = [...named].sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label, 'ru'));
    const shares = ordered.filter((b) => b.key === SHARES.key);
    const order = ['unrated', 'unknown'];
    return [
      ...ordered.filter((b) => b.key !== SHARES.key),
      ...missing.sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key)),
      ...shares,
    ];
  }
  const ranked = [...named].sort((a, b) => Number(b.value) - Number(a.value) || a.label.localeCompare(b.label, 'ru'));
  if (dimension !== 'issuer' || ranked.length <= TOP_ISSUERS + 1) {
    return [...ranked, ...missing];
  }
  const tail = ranked.slice(TOP_ISSUERS);
  const members = lines.filter((line) => tail.some((b) => b.ids.includes(line.instrument_id)));
  const rest = bucket({ key: 'rest', label: `Прочие · ${tail.length}`, tone: 'rest' }, members);
  return [...ranked.slice(0, TOP_ISSUERS), rest, ...missing];
}

/** Коротко для подписи столбика: «21,5 млн», «15 тыс.», «−200 тыс.». */
export function compactMoney(value: Decimal): string {
  const amount = Number(value);
  const sign = amount < 0 ? '−' : '';
  const size = Math.abs(amount);
  if (size >= 1_000_000) {
    const millions = (size / 1_000_000).toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    return `${sign}${millions} млн`;
  }
  if (size >= 1_000) {
    return `${sign}${formatQuantity(Math.round(size / 1_000))} тыс.`;
  }
  return `${sign}${formatQuantity(Math.round(size))}`;
}

/** Доля в процентах; не ноль, но меньше процента — «<1%». */
export function shareText(share: number): string {
  const percent = Math.round(Math.abs(share) * 100);
  const sign = share < 0 ? '−' : '';
  return `${sign}${percent === 0 && share !== 0 ? '<1' : percent}%`;
}
