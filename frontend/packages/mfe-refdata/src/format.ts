import type {
  CouponKind,
  Decimal,
  Field,
  InstrumentKind,
  IsoDate,
  NominalIndexation,
  OfferKind,
  RateKind,
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
