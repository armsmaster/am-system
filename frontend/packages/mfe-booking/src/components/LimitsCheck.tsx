import { errorMessage } from '@am/platform';
import { useEffect, useState } from 'react';

import type { BookingApi, Decimal, LimitChange, LimitsTrade, WhatIf } from '../api';
import { decimalInput } from '../format';

/** Пауза после ввода: проверяем, когда пользователь перестал печатать. */
const PAUSE_MS = 400;

/** Поля формы, по которым считается сделка для проверки. */
export interface TradeFields {
  readonly kind: LimitsTrade['kind'];
  readonly side: LimitsTrade['side'];
  readonly fundId: string | null;
  readonly instrument: { readonly id: string; readonly secid: string; readonly kind: 'bond' | 'fund_share' } | null;
  readonly quantity: string;
  readonly price: string;
  readonly faceValue: string;
  readonly accrued: string;
  /** Сумма первой части РЕПО с КСУ — её вводят сами. */
  readonly amount: string;
}

/**
 * Сделка для проверки лимитов — по формуле сервиса сделок: облигация — количество ×
 * (цена × номинал / 100 + НКД), пай — количество × цена. Не хватает полей — null.
 */
export function tradeForCheck(fields: TradeFields): LimitsTrade | null {
  if (!fields.fundId) {
    return null;
  }
  const number = (value: string) => Number(decimalInput(value || '0'));
  const quantity = number(fields.quantity);
  const paper = fields.instrument;
  const amount = !paper
    ? number(fields.amount)
    : paper.kind === 'bond'
      ? quantity * ((number(fields.price) * number(fields.faceValue)) / 100 + number(fields.accrued))
      : quantity * number(fields.price);
  if (!(amount > 0) || (fields.kind === 'outright' && !paper)) {
    return null;
  }
  return {
    fund_id: fields.fundId,
    kind: fields.kind,
    side: fields.side,
    amount: amount.toFixed(2),
    instrument_id: paper?.id ?? null,
    secid: paper?.secid ?? '',
    quantity: paper ? Math.trunc(quantity) : 0,
  };
}

type State =
  | { readonly status: 'idle' }
  | { readonly status: 'done'; readonly result: WhatIf }
  | { readonly status: 'failed'; readonly error: string };

/**
 * Лимиты после сделки — до ввода (design/09, решение 142). Только информация:
 * сделку не запрещает и комментария не просит; решает управляющий.
 */
export function LimitsCheck({ api, trade }: { readonly api: BookingApi; readonly trade: LimitsTrade | null }) {
  const [state, setState] = useState<State>({ status: 'idle' });
  const key = trade ? JSON.stringify(trade) : null;

  useEffect(() => {
    if (!key) {
      setState({ status: 'idle' });
      return undefined;
    }
    let current = true;
    const timer = setTimeout(() => {
      api.limitsCheck(JSON.parse(key) as LimitsTrade).then(
        (result) => current && setState({ status: 'done', result }),
        (failure: unknown) => current && setState({ status: 'failed', error: errorMessage(failure) }),
      );
    }, PAUSE_MS);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [api, key]);

  return (
    <section className="limits-check" aria-label="Лимиты после сделки" aria-live="polite">
      <h4>Лимиты после сделки</h4>
      <Body state={state} />
    </section>
  );
}

function Body({ state }: { readonly state: State }) {
  if (state.status === 'idle') {
    return <p className="muted">Заполните бумагу, количество и цену — покажем, как сделка изменит лимиты фонда.</p>;
  }
  if (state.status === 'failed') {
    return <p className="muted">Проверка лимитов недоступна: {state.error} Сделку можно ввести и без неё.</p>;
  }
  const { result } = state;
  if (!result.limit_set) {
    return <p className="muted">Набора лимитов у фонда нет — проверять нечем.</p>;
  }
  if (!result.calculated) {
    return <p className="muted">Расчёта фонда за сегодня ещё нет — проверить не по чему.</p>;
  }
  if (result.changes.length === 0) {
    return <p className="muted">Лимиты фонда сделка не меняет.</p>;
  }
  return (
    <>
      <ul className="plain">
        {result.changes.map((change, index) => (
          <li key={`${change.code}-${index}`} className={`status-${change.after_status}`}>
            {changeText(change)}
          </li>
        ))}
      </ul>
      <p className="muted">По позициям и ценам на сейчас. Проверка — только информация: решает управляющий.</p>
    </>
  );
}

/**
 * «Л3, на эмитента, Эмитент А: 9,64% → 10,18% при лимите 10%. После сделки лимит будет нарушен.»
 * Новые нарушения правил — в скобках: «… будет нарушен (короткая позиция: …).»
 */
export function changeText(change: LimitChange): string {
  const title = [change.code, change.label, change.group].filter(Boolean).join(', ');
  const values = valuesText(change);
  const reasons = change.reasons.length ? ` (${change.reasons.join('; ')})` : '';
  return `${title}${values ? `: ${values}` : ''}. ${verdict(change)}${reasons}.`;
}

function valuesText(change: LimitChange): string {
  if (change.type !== 'share' && change.type !== 'metric') {
    return '';
  }
  const unit = change.type === 'share' ? '%' : '';
  const bound =
    change.maximum !== null
      ? ` при лимите ${plain(change.maximum)}${unit}`
      : change.minimum !== null
        ? ` при минимуме ${plain(change.minimum)}${unit}`
        : '';
  return `${number(change.before)}${unit} → ${number(change.after)}${unit}${bound}`;
}

function verdict(change: LimitChange): string {
  const { before_status: before, after_status: after } = change;
  if (after === 'breach') {
    return before === 'breach'
      ? worse(change)
        ? 'Лимит уже нарушен — сделка увеличит нарушение'
        : 'Лимит нарушен и после сделки'
      : 'После сделки лимит будет нарушен';
  }
  if (after === 'warning') {
    return 'Близко к лимиту';
  }
  if (after === 'no_data') {
    return 'Нет данных для проверки';
  }
  return before === 'breach' ? 'Сделка устраняет нарушение' : 'В пределах';
}

function worse(change: LimitChange): boolean {
  if (change.reasons.length) {
    return true;
  }
  const before = Number(change.before ?? 0);
  const after = Number(change.after ?? 0);
  return change.maximum !== null ? after > before : after < before;
}

function number(value: Decimal | null): string {
  return value === null ? '—' : Number(value).toFixed(2).replace('.', ',').replace('-', '−');
}

function plain(value: Decimal): string {
  return value.replace('.', ',');
}
