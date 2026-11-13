import type {
  Coupon,
  CouponKind,
  Decimal,
  Field,
  InstrumentKind,
  InstrumentSummary,
  IsoDate,
  NominalIndexation,
  OfferKind,
  RateKind,
  RatingAction,
  Target,
  Version,
} from './api';

export const KIND_LABELS: Readonly<Record<InstrumentKind, string>> = {
  bond: 'облигация',
  fund_share: 'пай БПИФ',
};

export const COUPON_KIND_LABELS: Readonly<Record<CouponKind, string>> = {
  fixed: 'фиксированный',
  floater: 'флоатер',
  unknown: 'неизвестен',
};

export const RATE_LABELS: Readonly<Record<RateKind, string>> = {
  key_rate: 'ключевая ставка ЦБ',
  ruonia: 'RUONIA',
  rusfar: 'RUSFAR',
  other: 'другая',
};

export const INDEXATION_LABELS: Readonly<Record<NominalIndexation, string>> = {
  none: 'нет',
  ruonia: 'на индекс RUONIA',
  unknown: 'индексируется, индекс неизвестен',
};

export const OFFER_KIND_LABELS: Readonly<Record<OfferKind, string>> = {
  put: 'put — выкуп по требованию держателя',
  call: 'call — досрочное погашение эмитентом',
};

export const SOURCE_LABELS: Readonly<Record<Version['source'], string>> = {
  iss: 'MOEX ISS',
  manual: 'вручную',
};

/** К чему относится классификатор. */
export const TARGET_LABELS: Readonly<Record<Target, string>> = {
  instrument: 'для бумаг',
  issuer: 'для эмитентов',
};

export const RATING_ACTION_LABELS: Readonly<Record<RatingAction, string>> = {
  assigned: 'присвоен или изменён',
  withdrawn: 'отозван',
  unrated: 'рейтинга нет ни у одного агентства',
};

const ISS_TYPE_LABELS: Readonly<Record<string, string>> = {
  ofz_bond: 'ОФЗ',
  exchange_bond: 'биржевая облигация',
  corporate_bond: 'корпоративная облигация',
  subfederal_bond: 'региональная облигация',
  municipal_bond: 'муниципальная облигация',
  exchange_ppif: 'пай биржевого фонда',
};

/** Названия полей — те же, что в сервисе и в журнале. */
export const FIELD_LABELS: Readonly<Record<Field, string>> = {
  name: 'Название',
  short_name: 'Краткое название',
  board: 'Режим торгов',
  currency: 'Валюта',
  issuer: 'Эмитент',
  initial_face_value: 'Начальный номинал',
  issue_date: 'Дата выпуска',
  maturity_date: 'Дата погашения',
  coupon_kind: 'Тип купона',
  coupon_frequency: 'Купонов в год',
  nominal_indexation: 'Индексация номинала',
  coupons: 'Купоны',
  amortizations: 'Амортизации',
  offers: 'Оферты',
  floater: 'Параметры флоатера',
  iss_floater: 'Ставка и маржа флоатера по ISS',
};

const NO_VALUE = '—';
const WEEKDAYS = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'] as const;

/** У паёв фондов купона нет. */
export function couponKindLabel(kind: CouponKind | null): string {
  return kind ? COUPON_KIND_LABELS[kind] : NO_VALUE;
}

/** Тип бумаги ISS словами; незнакомый — кодом ISS. */
export function issTypeLabel(type: string | null): string {
  return type ? (ISS_TYPE_LABELS[type] ?? type) : NO_VALUE;
}

/** 2026-10-26 → 26.10.2026. Дату не переводим в Date: так она не сдвинется из-за часового пояса. */
export function formatDate(day: IsoDate | null): string {
  if (!day) {
    return NO_VALUE;
  }
  const [year, month, date] = day.split('-');
  return `${date}.${month}.${year}`;
}

/** Время по Москве: в нём работают биржа и пользователи. */
export function formatTime(iso: string | null): string {
  if (!iso) {
    return NO_VALUE;
  }
  return new Date(iso).toLocaleString('ru-RU', {
    timeZone: 'Europe/Moscow',
    dateStyle: 'short',
    timeStyle: 'short',
  });
}

/**
 * Десятичное число как есть, без перевода в float: 1234567.50 → 1 234 567,50.
 * Справочник показывает ровно то, что хранит.
 */
export function formatDecimal(value: Decimal | null, unit = ''): string {
  if (value === null) {
    return NO_VALUE;
  }
  const [whole = '', fraction] = value.split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  const number = fraction === undefined ? grouped : `${grouped},${fraction}`;
  return unit ? `${number} ${unit}` : number;
}

export function weekday(day: IsoDate): string {
  const [year, month, date] = day.split('-').map(Number);
  return WEEKDAYS[new Date(Date.UTC(year!, month! - 1, date)).getUTCDay()]!;
}

/** Сегодняшняя дата по Москве в формате ГГГГ-ММ-ДД. */
export function todayInMoscow(now: Date = new Date()): IsoDate {
  // Канадская локаль пишет дату как ГГГГ-ММ-ДД.
  return now.toLocaleDateString('en-CA', { timeZone: 'Europe/Moscow' });
}

// --- список бумаг: подпись о купоне, быстрые фильтры, эмитент ----------------------

/** Быстрый фильтр списка бумаг: по тому, как устроен купон. */
export type Family = 'fixed' | 'floater' | 'linker' | 'fund' | 'other';

export function familyOf(item: InstrumentSummary): Family {
  if (item.kind === 'fund_share') {
    return 'fund';
  }
  if (item.nominal_indexation !== 'none') {
    return 'linker';
  }
  if (item.coupon_kind === 'floater') {
    return 'floater';
  }
  return item.coupon_kind === 'fixed' ? 'fixed' : 'other';
}

/** База флоатера коротко — для подписи: КС + 130 бп. */
const RATE_SHORT: Readonly<Record<RateKind, string>> = {
  key_rate: 'КС',
  ruonia: 'RUONIA',
  rusfar: 'RUSFAR',
  other: 'плавающая ставка',
};

function trimZeros(value: Decimal): string {
  return value.includes('.') ? value.replace(/\.?0+$/, '') : value;
}

/** 7.10 → «7,1%». */
export function formatPercent(value: Decimal): string {
  return `${trimZeros(value).replace('.', ',')}%`;
}

/** База и спред флоатера: «КС + 130 бп», «RUONIA − 20 бп»; базы нет — «флоатер». */
export function floaterCaption(base: RateKind | null, marginBp: Decimal | null): string {
  if (base === null) {
    return 'флоатер';
  }
  if (marginBp === null) {
    return RATE_SHORT[base];
  }
  const margin = trimZeros(marginBp);
  const negative = margin.startsWith('-');
  return `${RATE_SHORT[base]} ${negative ? '−' : '+'} ${margin.replace('-', '').replace('.', ',')} бп`;
}

/** Подпись о купоне под названием бумаги: фикс — ставка, флоатер — база и спред.
 * Оферту показывает колонка «Погашение» — в подписи её нет. */
export function couponCaption(item: InstrumentSummary): string {
  const rate = item.coupon_rate === null ? null : formatPercent(item.coupon_rate);
  switch (familyOf(item)) {
    case 'fund':
      return 'пай фонда';
    case 'linker':
      return [item.nominal_indexation === 'ruonia' ? 'линкер на RUONIA' : 'линкер', rate]
        .filter(Boolean)
        .join(' · ');
    case 'floater':
      return floaterCaption(item.floater_rate, item.floater_margin_bp);
    case 'fixed':
      return rate ? `фикс ${rate}` : 'фикс';
    default:
      return rate ? `купон ${rate}` : 'тип купона неизвестен';
  }
}

function utc(day: IsoDate): number {
  const [year, month, date] = day.split('-').map(Number);
  return Date.UTC(year!, month! - 1, date);
}

export function daysBetween(from: IsoDate, to: IsoDate): number {
  return Math.round((utc(to) - utc(from)) / 86_400_000);
}

/** Длина купонного периода: целые месяцы — 1M, 3M, 6M, 12M — 1Y; иначе дни — 182D. */
export function periodLabel(start: IsoDate, end: IsoDate): string {
  const [y1, m1, d1] = start.split('-').map(Number);
  const [y2, m2, d2] = end.split('-').map(Number);
  if (d1 !== d2) {
    return `${daysBetween(start, end)}D`;
  }
  const months = (y2! - y1!) * 12 + (m2! - m1!);
  return months % 12 === 0 ? `${months / 12}Y` : `${months}M`;
}

/** Типичная длина купона по графику — самая частая; первый нестандартный период не в счёт. */
export function couponLength(coupons: readonly Coupon[]): string | null {
  const counts = new Map<string, number>();
  for (const c of coupons) {
    if (c.start_date) {
      const label = periodLabel(c.start_date, c.date);
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
  }
  let best: string | null = null;
  let most = 0;
  for (const [label, count] of counts) {
    if (count > most) {
      best = label;
      most = count;
    }
  }
  return best;
}

const LEGAL_FORMS: readonly (readonly [RegExp, string])[] = [
  [/^публичное акционерное общество\s+/i, 'ПАО '],
  [/^открытое акционерное общество\s+/i, 'ОАО '],
  [/^закрытое акционерное общество\s+/i, 'ЗАО '],
  [/^акционерное общество\s+/i, 'АО '],
  [/^общество с ограниченной ответственностью\s+/i, 'ООО '],
  [/^государственная корпорация развития\s+/i, 'ГК '],
  [/^министерство финансов российской федерации$/i, 'Минфин России'],
];

/** Валюта коротко: рубль — знаком, остальные — кодом. */
export function currencySign(currency: string): string {
  return currency === 'RUB' ? '₽' : currency;
}

/** «Публичное акционерное общество "Газпром нефть"» → «ПАО «Газпром нефть»». */
export function shortIssuer(name: string): string {
  const short = LEGAL_FORMS.reduce((text, [form, abbr]) => text.replace(form, abbr), name.trim());
  return short.replace(/"([^"]*)"/g, '«$1»');
}
