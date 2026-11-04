import type { Check, CheckUsage, Classifier, Condition, Decimal, IsoDate, SetBody, Status } from './api';

const NO_VALUE = '—';

export const STATUS_LABELS: Readonly<Record<Status, string>> = {
  ok: 'в пределах',
  warning: 'близко к лимиту',
  breach: 'нарушение',
  no_data: 'нет данных',
};

export const AGENCY_LABELS: Readonly<Record<string, string>> = {
  acra: 'АКРА',
  expert_ra: 'Эксперт РА',
  nkr: 'НКР',
  nra: 'НРА',
};

/** Имена из справочника — чтобы набор читался словами, а не кодами. */
export interface Names {
  readonly classifiers: ReadonlyMap<string, Classifier>;
  readonly issuers: ReadonlyMap<string, string>;
  readonly instruments: ReadonlyMap<string, string>;
}

export function names(
  classifiers: readonly Classifier[],
  issuers: readonly { readonly id: string; readonly name: string }[],
  instruments: readonly { readonly id: string; readonly secid: string }[],
): Names {
  return {
    classifiers: new Map(classifiers.map((c) => [c.id, c])),
    issuers: new Map(issuers.map((i) => [i.id, i.name])),
    instruments: new Map(instruments.map((i) => [i.id, i.secid])),
  };
}

// --- числа и даты ----------------------------------------------------------------------

/** 2800000000 → 2 800 000 000,00; знаков после запятой — ``digits``. */
export function formatNumber(value: Decimal | null, digits = 2): string {
  if (value === null) {
    return NO_VALUE;
  }
  const [whole = '', fraction = ''] = Number(value).toFixed(digits).split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ' ').replace('-', '−');
  return fraction ? `${grouped},${fraction}` : grouped;
}

/** Порог как его записали в наборе: 2.5 → 2,5. */
export function plain(value: Decimal): string {
  return value.replace('.', ',');
}

/** «2,5 года», «0 лет», «1 год»: дробное число — «года», целое — по правилам числа. */
export function years(value: Decimal): string {
  if (value.includes('.')) {
    return 'года';
  }
  const n = Math.abs(Number(value));
  if (n % 10 === 1 && n % 100 !== 11) {
    return 'год';
  }
  return n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? 'года' : 'лет';
}

/** 2026-11-10 → 10.11.2026. */
export function formatDate(day: IsoDate): string {
  const [year, month, date] = day.split('-');
  return `${date}.${month}.${year}`;
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', dateStyle: 'short', timeStyle: 'short' });
}

/** Сегодняшняя дата по Москве в формате ГГГГ-ММ-ДД. */
export function todayInMoscow(now: Date = new Date()): IsoDate {
  return now.toLocaleDateString('en-CA', { timeZone: 'Europe/Moscow' });
}

// --- использование -----------------------------------------------------------------------

/** Значение проверки: плечо, дюрация, доля с самой большой группой, число нарушений. */
export function valueText(check: CheckUsage): string {
  if (check.type === 'metric') {
    return check.value === null ? NO_VALUE : `${formatNumber(check.value)}${check.metric === 'duration' ? ' года' : ''}`;
  }
  if (check.type === 'share') {
    const top = check.groups[0];
    const share = `${formatNumber(check.value)}%`;
    return top && top.key !== 'all' ? `${share}, ${top.name}` : share;
  }
  const count = Number(check.value);
  return count ? `нарушений: ${count}` : 'нарушений нет';
}

export function thresholdText(check: CheckUsage): string {
  const unit = (bound: Decimal) =>
    check.type === 'share' ? '%' : check.metric === 'duration' ? ` ${years(bound)}` : '';
  const bounds = [
    check.maximum === null ? null : `не более ${plain(check.maximum)}${unit(check.maximum)}`,
    check.minimum === null ? null : `не менее ${plain(check.minimum)}${unit(check.minimum)}`,
  ].filter(Boolean);
  return bounds.length ? bounds.join(', ') : NO_VALUE;
}

export function usageText(usage: Decimal | null): string {
  return usage === null ? NO_VALUE : `${Math.round(Number(usage))}%`;
}

// --- набор словами -------------------------------------------------------------------------

const ATTRIBUTE_LABELS: Readonly<Record<string, string>> = {
  kind: 'вид',
  iss_type: 'тип ISS',
  coupon_kind: 'тип купона',
  currency: 'валюта',
  instrument: 'бумага',
  issuer: 'эмитент',
  rating_group: 'рейтинговая группа',
};

const VALUE_LABELS: Readonly<Record<string, string>> = {
  bond: 'облигация',
  fund_share: 'пай',
  ofz_bond: 'ОФЗ',
  exchange_bond: 'биржевая облигация',
  corporate_bond: 'корпоративная облигация',
  exchange_ppif: 'пай биржевого фонда',
  fixed: 'фиксированный',
  floater: 'флоатер',
  none: 'нет',
};

/** «тип ISS: не ОФЗ», «Очерёдность»: старший долг». */
export function conditionText(condition: Condition, n: Names): string {
  const classifier = condition.attribute.startsWith('class:')
    ? n.classifiers.get(condition.attribute.slice(6))
    : undefined;
  const name = condition.attribute.startsWith('class:')
    ? classifier
      ? `«${classifier.name}»`
      : 'классификатор, которого нет в справочнике'
    : (ATTRIBUTE_LABELS[condition.attribute] ?? condition.attribute);
  const values = condition.values.map((value) => valueName(condition.attribute, value, classifier, n));
  return `${name}: ${condition.negate ? 'не ' : ''}${values.join(', ')}`;
}

function valueName(attribute: string, value: string, classifier: Classifier | undefined, n: Names): string {
  if (classifier) {
    return classifier.values.find((v) => v.id === value)?.name ?? value;
  }
  if (attribute === 'issuer') {
    return n.issuers.get(value) ?? value;
  }
  if (attribute === 'instrument') {
    return n.instruments.get(value) ?? value;
  }
  if (attribute === 'rating_group' && value === 'none') {
    return 'группы нет';
  }
  return attribute === 'rating_group' || attribute === 'currency' ? value : (VALUE_LABELS[value] ?? value);
}

export function filterText(filter: readonly Condition[] | undefined, n: Names): string {
  return filter?.length ? filter.map((c) => conditionText(c, n)).join('; ') : 'все бумаги';
}

const GROUP_LABELS = { all: 'всех бумаг проверки вместе', issuer: 'на одного эмитента', instrument: 'на один выпуск' };

/** Проверка набора словами: что считает, по каким бумагам, какой порог. */
export function checkText(check: Check, n: Names): string {
  if (check.type === 'metric') {
    const metric = check.metric === 'leverage' ? 'Плечо — активы / СЧА' : 'Модифицированная дюрация к активам';
    const unit = (bound: Decimal) => (check.metric === 'duration' ? ` ${years(bound)}` : '');
    const bounds = [
      check.max == null ? null : `не более ${plain(check.max)}${unit(check.max)}`,
      check.min == null ? null : `не менее ${plain(check.min)}${unit(check.min)}`,
    ].filter(Boolean);
    return `${metric}: ${bounds.join(', ')}.`;
  }
  if (check.type === 'share') {
    const excluded = (check.exclude_issuers ?? []).map((id) => n.issuers.get(id) ?? id);
    return [
      `Доля в активах ${GROUP_LABELS[check.group ?? 'all']} — не более ${plain(check.max)}%.`,
      `Бумаги: ${filterText(check.filter, n)}.`,
      excluded.length ? `Не применяется к эмитентам: ${excluded.join(', ')}.` : null,
    ]
      .filter(Boolean)
      .join(' ');
  }
  if (check.type === 'eligible') {
    const rules = check.rules.map(
      (r) => `«${r.name}» — ${filterText(r.filter, n)}${r.shorts ? '; короткие позиции разрешены' : ''}`,
    );
    return `Каждая позиция и сделка дня подходит под одно из правил: ${rules.join(' | ')}.`;
  }
  return `При покупке бумаг (${filterText(check.filter, n)}) на дату сделки: ${filterText(check.condition, n)}.`;
}

const SOURCE_LABELS = {
  instrument: 'рейтинг выпуска',
  issuer: 'рейтинг эмитента',
  instrument_or_issuer: 'рейтинг выпуска, а без него — эмитента',
};

/** Правило рейтинга и группы словами (п. 6.2 политики). */
export function ratingText(settings: SetBody['settings'], n: Names): readonly string[] {
  const { ratings } = settings;
  const groups = Object.entries(ratings.groups).map(([grade, group]) => `${grade} → ${group}`);
  return [
    `Агентства: ${ratings.agencies.map((a) => AGENCY_LABELS[a] ?? a).join(', ')}; из нескольких рейтингов — ${
      ratings.pick === 'lowest' ? 'наинизший' : 'наивысший'
    }.`,
    `Рейтинговые группы: ${groups.join(', ')}; остальные ступени — группы нет.`,
    ...ratings.rules.map((r) => `${filterText(r.filter, n)} → ${SOURCE_LABELS[r.source]}.`),
    `Предупреждение — с ${plain(settings.warn_at ?? '90')}% лимита.`,
  ];
}
