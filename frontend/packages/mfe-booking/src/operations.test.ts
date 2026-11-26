import { describe, expect, it } from 'vitest';

import {
  expenseOwed,
  feesDueThrough,
  isPending,
  matches,
  opRanges,
  opTotals,
  settlementOf,
} from './operations';
import { operation } from './test/fixtures';

const TODAY = '2026-12-03';
const expense = (fields: Parameters<typeof operation>[0]) =>
  operation({ kind: 'expense_accrual', units: null, planned_date: null, expense_kind: 'auditor', ...fields });

describe('fund operations (decision 175)', () => {
  it('tells the settlement of units', () => {
    expect(settlementOf(operation({ planned_date: '2026-12-04' }), TODAY)).toBe('waiting');
    expect(settlementOf(operation({ planned_date: '2026-12-02' }), TODAY)).toBe('overdue');
    expect(settlementOf(operation({ planned_date: null }), TODAY)).toBe('waiting');
    expect(settlementOf(operation({ settled_date: '2026-11-27' }), TODAY)).toBe('settled');
    expect(settlementOf(expense({}), TODAY)).toBe('none');
    expect(isPending(operation(), TODAY)).toBe(true);
    expect(isPending(operation({ status: 'cancelled' }), TODAY)).toBe(false);
    expect(isPending(expense({}), TODAY)).toBe(false);
  });

  it('filters by group and pending settlement', () => {
    expect(matches(expense({}), 'all', TODAY)).toBe(true);
    expect(matches(expense({}), 'expenses', TODAY)).toBe(true);
    expect(matches(expense({}), 'units', TODAY)).toBe(false);
    expect(matches(operation(), 'pending', TODAY)).toBe(true);
  });

  it('counts quick periods back from today', () => {
    expect(opRanges(TODAY)).toEqual({
      month: ['2026-11-04', TODAY],
      quarter: ['2026-09-04', TODAY],
      year: ['2025-12-04', TODAY],
    });
  });

  it('sums the period without cancelled operations', () => {
    const totals = opTotals(
      [
        operation(),
        operation({ id: 'o-2', kind: 'unit_redemption', amount: '5000000.00', settled_date: '2026-11-30' }),
        operation({ id: 'o-3', status: 'cancelled' }),
        expense({ id: 'o-4', kind: 'expense_payment', amount: '120000.00' }),
        operation({ id: 'o-5', kind: 'fee_payment', units: null, planned_date: null, amount: '30000.00' }),
        operation({ id: 'o-6', kind: 'security_transfer', units: null, planned_date: null, amount: null }),
        // Суммы нет — в итогах ноль, а не ошибка.
        operation({ id: 'o-7', kind: 'fee_payment', units: null, planned_date: null, amount: null }),
        operation({ id: 'o-8', kind: 'unit_redemption', amount: null }),
      ],
      TODAY,
    );
    expect(totals).toEqual({
      issued: '100000000.00',
      redeemed: '5000000.00',
      paid: '150000.00',
      pending: 2,
      pendingAmount: '100000000.00',
    });
  });

  it('tells what is accrued and not paid', () => {
    const ops = [
      expense({ id: 'a', amount: '100000.00', op_date: '2026-11-01' }),
      expense({ id: 'b', kind: 'expense_payment', amount: '30000.00', op_date: '2026-11-20' }),
      expense({ id: 'c', amount: '50000.00', op_date: '2026-12-05' }),
      expense({ id: 'd', amount: '7000.00', status: 'cancelled' }),
      expense({ id: 'e', amount: '9000.00', expense_kind: 'specdep' }),
      expense({ id: 'f', amount: '1.00', fund_id: 'p-other' }),
    ];
    expect(expenseOwed(ops, 'p-fund', 'auditor', '2026-12-03')).toBe(70000);
    expect(expenseOwed(ops, 'p-fund', 'auditor', '2026-12-03', 'b')).toBe(100000);
    expect(feesDueThrough({ management: { '2026-10': '100.50', '2026-11': '200.25', '2026-12': '50.00' } }, 'management', '2026-11-30')).toBe(300.75);
    expect(feesDueThrough({}, 'specdep', '2026-11-30')).toBe(0);
  });
});
