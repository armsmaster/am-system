import type { Decimal, PriceType } from './api';

export const PRICE_LABELS: Readonly<Record<PriceType | 'manual', string>> = {
  vwap: 'VWAP',
  last: 'последняя сделка',
  bid: 'bid',
  ask: 'ask',
  close: 'закрытие',
  market_price: 'рыночная цена',
  admitted_quote: 'признаваемая котировка',
  dema: 'DEMA',
  manual: 'ручная цена',
};

/** Источники, которые сервис называет сам; остальные — коды скрипта как есть. */
const SOURCE_LABELS: Readonly<Record<string, string>> = {
  iss: 'MOEX ISS',
  'cbr-ws': 'Банк России',
  manual: 'трейдинг',
  bars: 'по барам',
};

export const ERROR_LABELS: Readonly<Record<string, string>> = {
  unknown_instrument: 'нет в справочнике',
  future_time: 'время из будущего',
  misaligned_start: 'бар не выровнен',
  inconsistent_ohlc: 'бар противоречив',
  bad_timeframe: 'длина бара больше 4 часов',
  duplicate_in_batch: 'повтор в пакете',
  future_date: 'дата из будущего',
};

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

const NO_VALUE = '—';

/** 51.823000 → 51,823: цена без лишних нулей, разряды не теряются. */
export function formatPrice(value: Decimal | null): string {
  if (value === null) {
    return NO_VALUE;
  }
  const [whole = '', fraction = ''] = value.split('.');
  const digits = fraction.replace(/0+$/, '');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  return digits ? `${grouped},${digits}` : grouped;
}

/** Время по Москве: в нём работают биржа и пользователи. */
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString('ru-RU', {
    timeZone: 'Europe/Moscow',
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** Значение для поля datetime-local по Москве: 2026-10-27T11:05. */
export function moscowInput(date: Date): string {
  return date.toLocaleString('sv-SE', { timeZone: 'Europe/Moscow' }).slice(0, 16).replace(' ', 'T');
}

/** Поле datetime-local (время по Москве) → момент со смещением для API. */
export function fromMoscowInput(value: string): string {
  return `${value}:00+03:00`;
}

/** 15.935227 → 15,94: число с заданным числом знаков после запятой. */
export function formatNumber(value: Decimal | null, digits: number): string {
  if (value === null) {
    return NO_VALUE;
  }
  // Разряды — обычным пробелом, как в formatPrice; минус — типографский.
  return Number(value)
    .toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits })
    .replace(/\s/g, ' ')
    .replace('-', '−');
}

/** 2026-10-29 → 29.10.2026. */
export function formatDate(iso: string): string {
  return iso.split('-').reverse().join('.');
}
