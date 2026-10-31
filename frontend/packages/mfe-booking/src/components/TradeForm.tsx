import { errorMessage } from '@am/platform';
import { ErrorNote, useAction } from '@am/ui';
import { useState, type FormEvent } from 'react';

import type { Context } from '../App';
import type { Side, Trade, TradeInput } from '../api';
import { decimalInput, fromMoscowInput, moscowInput } from '../format';

interface Draft {
  portfolio_id: string;
  instrument_id: string;
  side: Side;
  quantity: string;
  price: string;
  trade_at: string;
  settle_date: string;
  face_value: string;
  accrued: string;
  fee: string;
  venue: 'exchange' | 'otc';
  counterparty: string;
  external_id: string;
  comment: string;
}

function fromTrade(trade: Trade): Draft {
  return {
    portfolio_id: trade.portfolio_id,
    instrument_id: trade.instrument_id,
    side: trade.side,
    quantity: String(trade.quantity),
    price: trade.price,
    trade_at: moscowInput(new Date(trade.trade_at)),
    settle_date: trade.settle_date,
    face_value: trade.face_value ?? '',
    accrued: trade.accrued ?? '',
    fee: trade.fee,
    venue: trade.venue,
    counterparty: trade.counterparty ?? '',
    external_id: trade.external_id ?? '',
    comment: '',
  };
}

function blank(portfolioId: string): Draft {
  return {
    portfolio_id: portfolioId,
    instrument_id: '',
    side: 'buy',
    quantity: '',
    price: '',
    trade_at: moscowInput(new Date()),
    settle_date: '',
    face_value: '',
    accrued: '',
    fee: '0',
    venue: 'exchange',
    counterparty: '',
    external_id: '',
    comment: '',
  };
}

/**
 * Ввод и исправление сделки. Бумагу и дату сделки выбрали — форма подставляет
 * дату расчётов T+1, номинал и НКД на неё (решение 86); их можно исправить
 * по подтверждению брокера. Сумму считает сервис.
 */
export function TradeForm({
  context,
  initial,
  defaultPortfolio,
  onSaved,
  onCancel,
}: {
  readonly context: Context;
  readonly initial?: Trade;
  readonly defaultPortfolio?: string | undefined;
  readonly onSaved: (trade: Trade) => void;
  readonly onCancel: () => void;
}) {
  const physical = context.portfolios.filter((p) => p.kind === 'physical');
  const [draft, setDraft] = useState<Draft>(() =>
    initial ? fromTrade(initial) : blank(defaultPortfolio ?? physical[0]?.id ?? ''),
  );
  const [hint, setHint] = useState<string | null>(null);
  const { run, error, busy } = useAction(() => undefined);
  const instrument = context.instruments.find((i) => i.id === draft.instrument_id);
  const isBond = instrument?.kind !== 'fund_share';

  function set<K extends keyof Draft>(field: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  async function fillDefaults(instrumentId: string, tradeAt: string) {
    if (!instrumentId || !tradeAt) {
      return;
    }
    try {
      const found = await context.api.defaults(instrumentId, tradeAt.slice(0, 10));
      setDraft((current) => ({
        ...current,
        settle_date: found.settle_date,
        face_value: found.face_value ?? '',
        accrued: found.accrued ?? '',
      }));
      setHint(
        found.kind === 'bond' && found.accrued === null
          ? 'НКД на дату расчётов неизвестен — введите его по подтверждению брокера.'
          : null,
      );
    } catch (failure) {
      setHint(errorMessage(failure));
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body: TradeInput = {
      portfolio_id: draft.portfolio_id,
      instrument_id: draft.instrument_id,
      side: draft.side,
      quantity: Number(draft.quantity),
      price: decimalInput(draft.price),
      trade_at: fromMoscowInput(draft.trade_at),
      settle_date: draft.settle_date,
      face_value: isBond && draft.face_value ? decimalInput(draft.face_value) : null,
      accrued: isBond && draft.accrued !== '' ? decimalInput(draft.accrued) : null,
      fee: decimalInput(draft.fee || '0'),
      venue: draft.venue,
      counterparty: draft.counterparty.trim() || null,
      external_id: draft.external_id.trim() || null,
      comment: draft.comment.trim() || null,
    };
    let saved: Trade | undefined;
    const ok = await run(async () => {
      saved = initial ? await context.api.amend(initial.id, body) : await context.api.enter(body);
    });
    if (ok && saved) {
      onSaved(saved);
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} aria-label={initial ? 'Исправление сделки' : 'Новая сделка'}>
      <h3>{initial ? 'Исправление: новая версия сделки' : 'Новая сделка'}</h3>
      <div className="fields">
        <label>
          Портфель
          <select value={draft.portfolio_id} onChange={(e) => set('portfolio_id', e.target.value)} required>
            {physical.map((p) => (
              <option key={p.id} value={p.id}>
                {p.code}
              </option>
            ))}
          </select>
        </label>
        <label>
          Бумага
          <select
            value={draft.instrument_id}
            required
            onChange={(e) => {
              set('instrument_id', e.target.value);
              void fillDefaults(e.target.value, draft.trade_at);
            }}
          >
            <option value="">— выберите —</option>
            {context.instruments.map((i) => (
              <option key={i.id} value={i.id}>
                {i.secid} — {i.short_name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Направление
          <select value={draft.side} onChange={(e) => set('side', e.target.value as Side)}>
            <option value="buy">покупка</option>
            <option value="sell">продажа</option>
          </select>
        </label>
        <label>
          Количество, шт.
          <input inputMode="numeric" value={draft.quantity} required onChange={(e) => set('quantity', e.target.value)} />
        </label>
        <label>
          {isBond ? 'Цена, % от номинала' : 'Цена, ₽ за пай'}
          <input inputMode="decimal" value={draft.price} required onChange={(e) => set('price', e.target.value)} />
        </label>
        <label>
          Время сделки, МСК
          <input
            type="datetime-local"
            value={draft.trade_at}
            required
            onChange={(e) => {
              set('trade_at', e.target.value);
              void fillDefaults(draft.instrument_id, e.target.value);
            }}
          />
        </label>
        <label>
          Дата расчётов
          <input type="date" value={draft.settle_date} required onChange={(e) => set('settle_date', e.target.value)} />
        </label>
        {isBond && (
          <>
            <label>
              Номинал на дату расчётов, ₽
              <input inputMode="decimal" value={draft.face_value} onChange={(e) => set('face_value', e.target.value)} />
            </label>
            <label>
              НКД на бумагу, ₽
              <input inputMode="decimal" value={draft.accrued} onChange={(e) => set('accrued', e.target.value)} />
            </label>
          </>
        )}
        <label>
          Комиссия, ₽
          <input inputMode="decimal" value={draft.fee} onChange={(e) => set('fee', e.target.value)} />
        </label>
        <label>
          Место
          <select value={draft.venue} onChange={(e) => set('venue', e.target.value as Draft['venue'])}>
            <option value="exchange">биржа</option>
            <option value="otc">внебиржевая</option>
          </select>
        </label>
        <label>
          Контрагент
          <input value={draft.counterparty} onChange={(e) => set('counterparty', e.target.value)} />
        </label>
        <label>
          Номер сделки у брокера
          <input value={draft.external_id} onChange={(e) => set('external_id', e.target.value)} />
        </label>
        <label>
          {initial ? 'Что исправлено и почему' : 'Комментарий'}
          <input
            value={draft.comment}
            required={initial !== undefined}
            minLength={initial ? 3 : undefined}
            onChange={(e) => set('comment', e.target.value)}
          />
        </label>
      </div>
      {hint && <p className="notice notice-warning">{hint}</p>}
      <button type="submit" disabled={busy}>
        {initial ? 'Сохранить версию' : 'Ввести сделку'}
      </button>{' '}
      <button type="button" onClick={onCancel}>
        Отмена
      </button>
      <ErrorNote message={error} />
    </form>
  );
}
