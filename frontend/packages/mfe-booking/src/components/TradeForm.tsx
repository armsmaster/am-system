import { errorMessage } from '@am/platform';
import { ErrorNote, useAction } from '@am/ui';
import { useState, type FormEvent } from 'react';

import type { Context } from '../App';
import type { Side, Trade, TradeInput, TradeKind } from '../api';
import { decimalInput, formatMoney, fromMoscowInput, moscowInput, secondAmount } from '../format';
import { LimitsCheck, tradeForCheck } from './LimitsCheck';

/** Обеспечение РЕПО с КСУ: бумаги нет, только деньги (решение 117). */
const KSU = 'ksu';

interface Draft {
  kind: TradeKind;
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
  amount: string;
  repo_rate: string;
  second_date: string;
  second_amount: string;
}

function fromTrade(trade: Trade): Draft {
  return {
    kind: trade.kind,
    portfolio_id: trade.portfolio_id,
    instrument_id: trade.instrument_id ?? KSU,
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
    amount: trade.amount,
    repo_rate: trade.repo_rate ?? '',
    second_date: trade.second_date ?? '',
    second_amount: trade.second_amount ?? '',
  };
}

function blank(portfolioId: string): Draft {
  return {
    kind: 'outright',
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
    amount: '',
    repo_rate: '',
    second_date: '',
    second_amount: '',
  };
}

/**
 * Ввод и исправление сделки. Бумагу и дату сделки выбрали — форма подставляет
 * дату расчётов T+1, номинал и НКД на неё (решение 86); их можно исправить
 * по подтверждению брокера. Сумму считает сервис.
 *
 * РЕПО с НКЦ (design/07): сторона — первой части, дата расчётов — первой части
 * (подсказка — день сделки). Сумму второй части вводят по отчёту брокера; форма
 * показывает расчётную по ставке и сроку — для проверки (решение 110).
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
  const repo = draft.kind === 'repo';
  const ksu = repo && draft.instrument_id === KSU;
  const instrument = context.instruments.find((i) => i.id === draft.instrument_id);
  const isBond = !ksu && instrument?.kind !== 'fund_share';
  const choices = repo ? context.instruments.filter((i) => i.kind === 'bond') : context.instruments;
  const expected = ksu ? secondAmount(draft.amount, draft.repo_rate, draft.settle_date, draft.second_date) : null;
  // Лимиты после сделки — только для новой: исправление уже сидит в позициях.
  const limits =
    context.seesLimits && !initial
      ? tradeForCheck({
          kind: draft.kind,
          side: draft.side,
          fundId: physical.find((p) => p.id === draft.portfolio_id)?.fund_id ?? null,
          instrument: ksu || !instrument ? null : instrument,
          quantity: draft.quantity,
          price: draft.price,
          faceValue: draft.face_value,
          accrued: draft.accrued,
          amount: draft.amount,
        })
      : undefined;

  function set<K extends keyof Draft>(field: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  async function fillDefaults(instrumentId: string, tradeAt: string, kind: TradeKind) {
    if (!instrumentId || instrumentId === KSU || !tradeAt) {
      return;
    }
    try {
      // У РЕПО первая часть — обычно в день сделки: номинал и НКД — на неё.
      const found = await context.api.defaults(instrumentId, tradeAt.slice(0, 10));
      setDraft((current) => ({
        ...current,
        settle_date: kind === 'repo' ? tradeAt.slice(0, 10) : found.settle_date,
        face_value: found.face_value ?? '',
        accrued: kind === 'repo' ? '' : (found.accrued ?? ''),
      }));
      setHint(
        found.kind === 'bond' && found.accrued === null && kind === 'outright'
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
      kind: draft.kind,
      portfolio_id: draft.portfolio_id,
      instrument_id: ksu ? null : draft.instrument_id,
      side: draft.side,
      quantity: ksu ? 0 : Number(draft.quantity),
      price: ksu ? '0' : decimalInput(draft.price),
      trade_at: fromMoscowInput(draft.trade_at),
      settle_date: draft.settle_date,
      face_value: isBond && draft.face_value ? decimalInput(draft.face_value) : null,
      accrued: isBond && draft.accrued !== '' ? decimalInput(draft.accrued) : null,
      fee: decimalInput(draft.fee || '0'),
      venue: repo ? 'exchange' : draft.venue,
      counterparty: repo ? null : draft.counterparty.trim() || null,
      external_id: draft.external_id.trim() || null,
      comment: draft.comment.trim() || null,
      amount: ksu ? decimalInput(draft.amount) : null,
      repo_rate: repo ? decimalInput(draft.repo_rate) : null,
      second_date: repo ? draft.second_date : null,
      second_amount: repo ? decimalInput(draft.second_amount) : null,
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
          Вид сделки
          <select
            value={draft.kind}
            disabled={initial !== undefined}
            onChange={(e) => {
              const kind = e.target.value as TradeKind;
              setDraft((current) => ({ ...current, kind, instrument_id: '' }));
            }}
          >
            <option value="outright">покупка или продажа</option>
            <option value="repo">РЕПО с НКЦ</option>
          </select>
        </label>
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
          {repo ? 'Обеспечение' : 'Бумага'}
          <select
            value={draft.instrument_id}
            required
            onChange={(e) => {
              set('instrument_id', e.target.value);
              void fillDefaults(e.target.value, draft.trade_at, draft.kind);
            }}
          >
            <option value="">— выберите —</option>
            {repo && <option value={KSU}>КСУ — клиринговые сертификаты участия</option>}
            {choices.map((i) => (
              <option key={i.id} value={i.id}>
                {i.secid} — {i.short_name}
              </option>
            ))}
          </select>
        </label>
        <label>
          {repo ? 'Вид РЕПО' : 'Направление'}
          <select value={draft.side} onChange={(e) => set('side', e.target.value as Side)}>
            <option value="buy">{repo ? 'обратное: фонд размещает деньги' : 'покупка'}</option>
            <option value="sell">{repo ? 'прямое: фонд привлекает деньги' : 'продажа'}</option>
          </select>
        </label>
        {!ksu && (
          <>
            <label>
              Количество, шт.
              <input inputMode="numeric" value={draft.quantity} required onChange={(e) => set('quantity', e.target.value)} />
            </label>
            <label>
              {isBond ? `Цена${repo ? ' первой части' : ''}, % от номинала` : 'Цена, ₽ за пай'}
              <input inputMode="decimal" value={draft.price} required onChange={(e) => set('price', e.target.value)} />
            </label>
          </>
        )}
        <label>
          Время сделки, МСК
          <input
            type="datetime-local"
            value={draft.trade_at}
            required
            onChange={(e) => {
              set('trade_at', e.target.value);
              void fillDefaults(draft.instrument_id, e.target.value, draft.kind);
            }}
          />
        </label>
        <label>
          {repo ? 'Первая часть: дата расчётов' : 'Дата расчётов'}
          <input type="date" value={draft.settle_date} required onChange={(e) => set('settle_date', e.target.value)} />
        </label>
        {ksu && (
          <label>
            Сумма первой части, ₽
            <input inputMode="decimal" value={draft.amount} required onChange={(e) => set('amount', e.target.value)} />
          </label>
        )}
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
        {repo && (
          <>
            <label>
              Ставка РЕПО, % годовых
              <input inputMode="decimal" value={draft.repo_rate} required onChange={(e) => set('repo_rate', e.target.value)} />
            </label>
            <label>
              Вторая часть: дата расчётов
              <input
                type="date"
                value={draft.second_date}
                required
                onChange={(e) => set('second_date', e.target.value)}
              />
            </label>
            <label>
              Вторая часть: сумма по отчёту брокера, ₽
              <input
                inputMode="decimal"
                value={draft.second_amount}
                required
                onChange={(e) => set('second_amount', e.target.value)}
              />
            </label>
          </>
        )}
        <label>
          Комиссия, ₽
          <input inputMode="decimal" value={draft.fee} onChange={(e) => set('fee', e.target.value)} />
        </label>
        {!repo && (
          <>
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
          </>
        )}
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
      {expected && (
        <p className="muted">
          По ставке и сроку вторая часть — {formatMoney(expected)} ₽. Введите сумму из отчёта брокера: если
          она отличается больше чем на рубль, сервис предупредит.
        </p>
      )}
      {repo && !ksu && (
        <p className="muted">
          Сумму первой части сервис посчитает по цене и НКД, вторую — сверит со ставкой и сроком.
        </p>
      )}
      {hint && <p className="notice notice-warning">{hint}</p>}
      {limits !== undefined && <LimitsCheck api={context.api} trade={limits} />}
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
