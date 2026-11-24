import { describe, expect, it } from 'vitest';

import type { LadderDay, LadderMovement, MovementKind } from './api';
import { dayItems, outlook, planRows, securityMarks, summarize, termLabel } from './plan';
import { ladder } from './test/fixtures';

function mv(kind: MovementKind, cash: string, fields: Partial<LadderMovement> = {}): LadderMovement {
  return {
    portfolio_code: 'MAIN',
    kind,
    instrument_id: null,
    trade_id: null,
    operation_id: null,
    quantity: 0,
    cash,
    repo_term: null,
    ...fields,
  };
}

function day(date: string, movements: LadderMovement[], fields: Partial<LadderDay> = {}): LadderDay {
  return { date, trading: true, cash: '1000.00', securities: [], movements, ...fields };
}

const PLAN = ladder({
  instruments: [
    { instrument_id: 'i-ofz', secid: 'SU26238RMFS4', short_name: 'ОФЗ 26238' },
    { instrument_id: 'i-bank', secid: 'RU000A10BNK1', short_name: 'Банк А 1P-02' },
  ],
});

describe('settlement plan items (decision 166)', () => {
  it('names REPO terms in trading days', () => {
    expect([termLabel(1), termLabel(3), termLabel(7), termLabel(null)]).toEqual([
      'овернайт',
      '3 дня',
      '7 дней',
      'срок неизвестен',
    ]);
  });

  it('groups REPO by term and leg with its commission inside', () => {
    const items = dayItems(
      day('2026-11-17', [
        mv('repo_second', '8203089.04', { trade_id: 'r-1', repo_term: 1 }),
        mv('repo_first', '-8000000.00', { trade_id: 'r-2', repo_term: 1 }),
        mv('repo_first', '-200000.00', { trade_id: 'r-3', repo_term: 1 }),
        mv('fee', '-56.16', { trade_id: 'r-2', repo_term: 1 }),
        mv('repo_first', '-300000.00', { trade_id: 'r-4', repo_term: 7 }),
        mv('repo_contribution', '1000.00', { trade_id: 'r-5', repo_term: null }),
      ]),
      PLAN,
    );
    expect(items.map((i) => [i.tag, i.label, i.note, i.cash])).toEqual([
      ['РЕПО', 'овернайт · вторая часть', null, '8203089.04'],
      ['РЕПО', 'овернайт · первая часть · 2 сделки', 'комиссия 56,16', '-8200056.16'],
      ['РЕПО', '7 дней · первая часть', null, '-300000.00'],
      ['РЕПО', 'срок неизвестен · взнос', null, '1000.00'],
    ]);
  });

  it('groups trades by bond with commissions inside', () => {
    const buy = (trade: string, cash: string, quantity: number) =>
      mv('settlement', cash, { trade_id: trade, instrument_id: 'i-bank', quantity });
    const items = dayItems(
      day('2026-11-23', [
        buy('t-1', '-116760.80', 100),
        buy('t-2', '-12854.40', 10),
        buy('t-3', '-1071.20', 1),
        buy('t-4', '-40705.60', 49),
        mv('fee', '-70.71', { trade_id: 't-1', instrument_id: 'i-bank' }),
        mv('fee', '-33.08', { trade_id: 't-2', instrument_id: 'i-bank' }),
        mv('settlement', '5000.00', { trade_id: 't-5', instrument_id: 'i-ofz', quantity: -10 }),
        mv('settlement', '-2000.00', { trade_id: 't-6', instrument_id: 'i-ofz', quantity: 10 }),
        mv('fee', '-1.00', { trade_id: 't-7', instrument_id: 'i-gone' }),
      ]),
      PLAN,
    );
    expect(items.map((i) => [i.label, i.note, i.cash])).toEqual([
      ['Банк А 1P-02 · 4 покупки, +160 шт.', 'в т.ч. комиссии 103,79', '-171495.79'],
      ['ОФЗ 26238 · 2 сделки', null, '3000.00'],
      ['i-gone · комиссия', null, '-1.00'],
    ]);
    const sold = dayItems(day('2026-11-24', [mv('settlement', '9.00', { trade_id: 't', instrument_id: 'i-ofz', quantity: -3 })]), PLAN);
    expect(sold[0]!.label).toBe('ОФЗ 26238 · 1 продажа, −3 шт.');
  });

  it('lists payments per bond, fund movements by kind and skips movements without money', () => {
    const items = dayItems(
      day('2026-11-19', [
        mv('coupon', '3788800.00', { instrument_id: 'i-bank' }),
        mv('redemption', '1000.00', { instrument_id: null }),
        mv('payment_unknown', '0', { instrument_id: 'i-ofz' }),
        mv('expense_accrual', '0.00'),
        mv('fee', '-5.00'),
        mv('unit_settlement', '200.00'),
        mv('transfer_out', '-300.00'),
        mv('expense_payment', '-40.00'),
      ]),
      PLAN,
    );
    expect(items.map((i) => [i.category, i.tag, i.label])).toEqual([
      ['payment', 'купон', 'Банк А 1P-02'],
      ['payment', 'погашение', '—'],
      ['transfer', 'перевод', 'перевод: ушло'],
      ['units', 'паи', 'расчёт по паям'],
      ['expense', 'расходы', 'выплата расхода'],
      ['fee', 'комиссия', 'комиссии брокера'],
    ]);
  });

  it('sums the day and finds the opening balance', () => {
    const summary = summarize(
      day('2026-11-17', [mv('coupon', '258000.00', { instrument_id: 'i-ofz' }), mv('transfer_out', '-58.21')], {
        cash: '411925.40',
      }),
      PLAN,
    );
    expect([summary.inflow, summary.outflow, summary.opening]).toEqual(['258000.00', '-58.21', '153983.61']);
    expect(summarize(day('2026-11-18', [mv('coupon', '5.00')], { cash: '-1.00' }), PLAN).opening).toBe('-6.00');
    // Равные суммы — по алфавиту.
    const equal = dayItems(day('2026-11-19', [mv('coupon', '5.00', { instrument_id: 'i-ofz' }), mv('coupon', '5.00', { instrument_id: 'i-bank' })]), PLAN);
    expect(equal.map((i) => i.label)).toEqual(['Банк А 1P-02', 'ОФЗ 26238']);
  });
});

describe('days and the outlook', () => {
  it('marks moved securities and every short one', () => {
    const marked = securityMarks(
      day('2026-11-27', [mv('settlement', '-1.00', { instrument_id: 'i-ofz', quantity: -400 }), mv('coupon', '1.00')], {
        securities: [
          { instrument_id: 'i-ofz', quantity: -400 },
          { instrument_id: 'i-bank', quantity: -5 },
          { instrument_id: 'i-flat', quantity: 7 },
        ],
      }),
    );
    expect(marked).toEqual([
      { id: 'i-ofz', quantity: -400, balance: -400 },
      { id: 'i-bank', quantity: 0, balance: -5 },
    ]);
  });

  it('folds quiet days, shortfalls included, but keeps today', () => {
    const plan = ladder({
      today: '2026-11-25',
      days: [
        day('2026-11-24', [mv('coupon', '1.00')]),
        day('2026-11-25', []),
        day('2026-11-26', []),
        day('2026-11-27', []),
        day('2026-11-30', [], { cash: '-5.00' }),
        day('2026-12-01', []),
      ],
    });
    expect(planRows(plan).map((r) => (r.kind === 'day' ? r.day.date : `${r.first.date}…${r.last.date}`))).toEqual([
      '2026-11-24',
      '2026-11-25',
      '2026-11-26…2026-12-01',
    ]);
  });

  it('finds shortfalls ahead and the first day of each short bond', () => {
    const plan = ladder({
      today: '2026-11-25',
      days: [
        day('2026-11-24', [], { cash: '-9.00', securities: [{ instrument_id: 'i-old', quantity: -1 }] }),
        day('2026-11-26', [], { securities: [{ instrument_id: 'i-ofz', quantity: -1 }] }),
        day('2026-11-27', [], { cash: '-3.00', securities: [{ instrument_id: 'i-ofz', quantity: -2 }] }),
      ],
    });
    const found = outlook(plan);
    expect(found.now?.date).toBe('2026-11-24');
    expect(found.lowest?.date).toBe('2026-11-27');
    expect(found.cashShort.map((d) => d.date)).toEqual(['2026-11-27']);
    expect(found.securitiesShort).toEqual([{ id: 'i-ofz', from: '2026-11-26' }]);
  });
});
