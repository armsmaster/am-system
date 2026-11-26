import { useEffect, useState } from 'react';

import type { Context } from '../App';
import type { NavDay, Operation } from '../api';
import { decimalInput, EXPENSE_LABELS, FEE_LABELS, formatDate, formatMoney, formatPrice, shiftDay } from '../format';
import { expenseOwed, feesDueThrough } from '../operations';
import type { Draft } from './OperationForm';

/** Цена за пай по операции дальше нашей оценки, чем на этот процент, — повод проверить сумму. */
export const PRICE_GAP_PERCENT = 1;

const money = (value: number) => formatMoney(value.toFixed(2));

function number(value: string): number | null {
  const parsed = Number(decimalInput(value));
  return value.trim() === '' || !Number.isFinite(parsed) ? null : parsed;
}

/**
 * Подсказки до проведения (решение 175): цена за пай рядом с нашей оценкой, сколько
 * вознаграждения закроет выплата, сколько расхода начислено и не выплачено. Расчёт
 * фонда берём на последний день до даты операции.
 */
export function OperationHints({
  context,
  draft,
  operations,
  except,
}: {
  readonly context: Context;
  readonly draft: Draft;
  readonly operations: readonly Operation[];
  /** Исправляемая операция: её саму в подсказке не считаем — она уже в оценке фонда. */
  readonly except: Operation | null;
}) {
  const needsNav = draft.kind === 'unit_issue' || draft.kind === 'unit_redemption' || draft.kind === 'fee_payment';
  const [day, setDay] = useState<NavDay | null | 'loading'>('loading');

  useEffect(() => {
    if (!needsNav || draft.fund_id === '' || draft.op_date === '') {
      return undefined;
    }
    let active = true;
    // Выплата закрывает то, что начислено до неё; паи — по оценке на день операции.
    const last = draft.kind === 'fee_payment' ? shiftDay(draft.op_date, -1) : draft.op_date;
    setDay('loading');
    context.api.nav(draft.fund_id, shiftDay(last, -10), last).then(
      (found) => active && setDay(found.days.filter((d) => d.unit_price !== null).at(-1) ?? null),
      () => active && setDay(null),
    );
    return () => {
      active = false;
    };
  }, [context.api, needsNav, draft.fund_id, draft.op_date, draft.kind]);

  const amount = number(draft.amount);
  const lines: { readonly text: string; readonly warn?: boolean }[] = [];

  if (draft.kind === 'unit_issue' || draft.kind === 'unit_redemption') {
    const units = number(draft.units);
    if (day === 'loading') {
      lines.push({ text: 'Сверяем с оценкой фонда…' });
    } else if (day === null) {
      lines.push({ text: 'Оценки фонда на эту дату нет — сверить цену за пай не с чем.' });
    } else {
      lines.push({ text: `Наша оценка на ${formatDate(day.date)}: ${formatMoney(day.unit_price)} ₽ за пай.` });
      if (units && amount) {
        const price = amount / units;
        const gap = (price / Number(day.unit_price) - 1) * 100;
        const shown = Math.abs(gap).toFixed(2);
        const sign = shown === '0.00' ? '' : gap > 0 ? '+' : '−';
        lines.push({
          text: `По операции — ${money(price)} ₽ за пай (${sign}${formatPrice(shown)}%).`,
          warn: Math.abs(gap) > PRICE_GAP_PERCENT,
        });
        const signed = (kind: string, value: number) => (kind === 'unit_issue' ? value : -value);
        // Исправляемая операция уже в паях фонда на этот день — убираем её прежние паи.
        const before =
          except !== null && except.op_date <= day.date ? signed(except.kind, Number(except.units)) : 0;
        const after = Number(day.units) - before + signed(draft.kind, units);
        lines.push({ text: `Паёв станет ${formatPrice(after.toFixed(5))}.` });
      }
    }
  }

  if (draft.kind === 'fee_payment' && day !== 'loading') {
    const due = day === null ? 0 : feesDueThrough(day.fees_due, draft.fee_kind, draft.period_end);
    lines.push({
      text: `Начислено и не выплачено (${FEE_LABELS[draft.fee_kind]}) по ${formatDate(draft.period_end)}: ${money(due)} ₽${
        day === null ? '' : ` на ${formatDate(day.date)}`
      }.`,
    });
    if (amount !== null) {
      const gap = amount - due;
      lines.push(
        Math.abs(gap) < 0.005
          ? { text: 'Сумма совпадает с начисленным.' }
          : { text: `Разница ${money(gap)} ₽ станет корректировкой в день выплаты.`, warn: true },
      );
    }
  }

  if (draft.kind === 'expense_payment') {
    const owed = expenseOwed(operations, draft.fund_id, draft.expense_kind, draft.op_date, except?.id ?? null);
    lines.push({ text: `Начислено и не выплачено (${EXPENSE_LABELS[draft.expense_kind]}): ${money(owed)} ₽.` });
    if (amount !== null && amount - owed > 0.005) {
      lines.push({ text: `Выплата больше начисленного на ${money(amount - owed)} ₽.`, warn: true });
    }
  }

  if (lines.length === 0) {
    return null;
  }
  return (
    <div className="operation-hints" aria-label="Подсказки" aria-live="polite">
      {lines.map((line) => (
        <p key={line.text} className={line.warn ? 'hint-warn' : undefined}>
          {line.text}
        </p>
      ))}
    </div>
  );
}
