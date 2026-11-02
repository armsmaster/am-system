import { describe, expect, it } from 'vitest';

import type { AuditEvent } from './api';
import { actionLabel, changes, formatTime, objectLabel } from './describe';

function event(type: string, data: Record<string, unknown>, entityId = 'e-1'): AuditEvent {
  return {
    event_id: `id-${type}`,
    occurred_at: '2026-10-25T09:14:03Z',
    service: 'portfolio',
    type,
    actor: { subject: 'u-1', username: 'admin' },
    entity_type: 'portfolio',
    entity_id: entityId,
    data,
    comment: null,
  };
}

describe('actionLabel', () => {
  it('names known actions in Russian and keeps unknown ones as is', () => {
    expect(actionLabel('access.granted')).toBe('Выдан доступ');
    expect(actionLabel('limits.breached')).toBe('limits.breached');
  });
});

describe('objectLabel', () => {
  it.each([
    [event('portfolio.updated', { before: { code: 'OLD' }, after: { code: 'NEW' } }), 'NEW'],
    [event('portfolio.created', { after: { code: 'F1' } }), 'F1'],
    [event('x.deleted', { before: { code: 'GONE' } }), 'GONE'],
    [
      event('portfolio.linked', { parent: { code: 'BOOK' }, child: { code: 'F1-OFZ' } }),
      'F1-OFZ ↔ BOOK',
    ],
    [
      event('access.granted', { user: { username: 'trader' }, portfolio: { code: 'F1' } }),
      'trader: F1',
    ],
    [
      event('access.revoked', { user: { subject: 'u-7' }, portfolio: { code: 'F1' } }),
      'u-7: F1',
    ],
    [event('something.else', {}, 'raw-id'), 'raw-id'],
    [event('instrument.loaded', { instrument: { secid: 'SU26238RMFS4' } }), 'SU26238RMFS4'],
    [
      event('instrument.version_added', { instrument: { secid: 'SU26238RMFS4' }, version: 2 }),
      'SU26238RMFS4, версия 2',
    ],
    [event('calendar.day_set', { day: '2026-11-04' }), '04.11.2026'],
    [event('something.else', { after: { code: 42 } }, 'raw-id'), 'raw-id'],
  ])('%#', (item, label) => {
    expect(objectLabel(item)).toBe(label);
  });
});

describe('changes', () => {
  it('lists only changed fields of an update', () => {
    const item = event('account.updated', {
      before: { id: 'a', name: 'Счёт', depo_account: null, derivatives_market: false },
      after: { id: 'a', name: 'Счёт', depo_account: 'D-1', derivatives_market: true },
    });

    expect(changes(item)).toEqual([
      { field: 'Счёт депо', before: '—', after: 'D-1' },
      { field: 'Срочный рынок', before: 'нет', after: 'да' },
    ]);
  });

  it('lists every field of a created object, with codes instead of ids, in a fixed order', () => {
    const item = event('account.created', {
      after: {
        id: 'a',
        fund_id: 'f-uuid',
        fund_code: 'F1',
        code: 'A1',
        cash_kind: 'broker',
        is_fund: true,
        extra: '',
        another: 'x',
      },
    });

    expect(changes(item)).toEqual([
      { field: 'Код', before: '—', after: 'A1' },
      { field: 'Это фонд', before: '—', after: 'да' },
      { field: 'Фонд', before: '—', after: 'F1' },
      { field: 'Денежный счёт', before: '—', after: 'брокерский' },
      { field: 'another', before: '—', after: 'x' },
      { field: 'extra', before: '—', after: '—' },
    ]);
  });

  it('has nothing to show without states', () => {
    expect(changes(event('access.granted', { user: {} }))).toEqual([]);
  });

  it('shows what was removed', () => {
    const item = event('calendar.day_cleared', {
      before: { is_trading: false, comment: 'Праздник' },
      after: null,
    });

    expect(changes(item)).toEqual([
      { field: 'Торговый день', before: 'нет', after: '—' },
      { field: 'Комментарий', before: 'Праздник', after: '—' },
    ]);
  });

  it('names instrument fields and values', () => {
    const item = event('instrument.version_added', {
      before: { coupon_kind: 'unknown', coupons: '60 зап.' },
      after: { coupon_kind: 'fixed', coupons: '60 зап.' },
    });

    expect(changes(item)).toEqual([
      { field: 'Тип купона', before: 'не определён', after: 'фиксированный' },
    ]);
  });

  it('keeps the order of instrument fields whatever order the database returns', () => {
    // JSONB в PostgreSQL хранит ключи по длине, а не в порядке записи.
    const item = event('instrument.loaded', {
      after: { name: 'ОФЗ 26238', board: 'TQOB', issuer: 'Минфин', coupons: '40 зап.', currency: 'RUB' },
    });

    expect(changes(item).map((change) => change.field)).toEqual([
      'Название',
      'Режим торгов',
      'Валюта',
      'Эмитент',
      'Купоны',
    ]);
  });

  it('shows dates the Russian way', () => {
    const item = event('instrument.version_added', {
      before: { maturity_date: '2037-08-12' },
      after: { maturity_date: '2037-08-13' },
    });

    expect(changes(item)).toEqual([
      { field: 'Дата погашения', before: '12.08.2037', after: '13.08.2037' },
    ]);
  });

  it('describes a manual price and a valuation rule change', () => {
    const manual = event('manual_price.set', {
      instrument: { secid: 'SU26238RMFS4' },
      after: { value: '51.5', valid_through: '2026-10-27' },
    });
    const rule = event('price_rule.changed', {
      instrument: { secid: 'RU000A10F504' },
      before: { price_types: 'VWAP', dema_timeframe: null, dema_half_life: null, spread_threshold_bp: 50 },
      after: { price_types: 'VWAP, DEMA', dema_timeframe: 10, dema_half_life: '60', spread_threshold_bp: 50 },
    });

    expect([actionLabel(manual.type), objectLabel(manual)]).toEqual(['Введена ручная цена', 'SU26238RMFS4']);
    expect(changes(manual)).toEqual([
      { field: 'Цена', before: '—', after: '51.5' },
      { field: 'Действует по', before: '—', after: '27.10.2026' },
    ]);
    expect(actionLabel(rule.type)).toBe('Изменено правило оценки');
    expect(changes(rule)).toEqual([
      { field: 'Типы цен', before: 'VWAP', after: 'VWAP, DEMA' },
      { field: 'Таймфрейм DEMA, мин', before: '—', after: '10' },
      { field: 'Период полураспада DEMA, мин', before: '—', after: '60' },
    ]);
  });

  it('describes trades', () => {
    const amended = event('trade.amended', {
      trade: { secid: 'SU26238RMFS4', portfolio: 'OPIF1-BONDS' },
      before: { portfolio: 'OPIF1-BONDS', status: 'confirmed', side: 'sell', quantity: '20000', amount: '11224200.00', settle_date: '2026-12-01' },
      after: { portfolio: 'OPIF1-BONDS', status: 'entered', side: 'sell', quantity: '25000', amount: '14030250.00', settle_date: '2026-12-01' },
    });
    const hidden = event('trade.entered', { trade: { secid: 'LQDT', portfolio: null }, before: null, after: { side: 'buy' } });

    expect([actionLabel(amended.type), objectLabel(amended)]).toEqual(['Исправлена сделка', 'OPIF1-BONDS: SU26238RMFS4']);
    expect(changes(amended)).toEqual([
      { field: 'Статус', before: 'подтверждена', after: 'введена' },
      { field: 'Количество', before: '20000', after: '25000' },
      { field: 'Сумма', before: '11224200.00', after: '14030250.00' },
    ]);
    expect(objectLabel(hidden)).toBe('—: LQDT');
    expect(changes(hidden)).toEqual([{ field: 'Направление', before: '—', after: 'покупка' }]);
    expect(['trade.cancelled', 'trade.confirmed'].map(actionLabel)).toEqual(['Отменена сделка', 'Подтверждена сделка']);
  });

  it('describes a REPO and a compensation contribution', () => {
    const repo = event('trade.entered', {
      trade: { secid: 'КСУ', portfolio: 'OPIF1-TREASURY' },
      before: null,
      after: { kind: 'repo', side: 'buy', repo_rate: '14.2', second_date: '2026-11-30', second_amount: '30035013.70' },
    });
    const contribution = event('operation.entered', {
      operation: { kind: 'repo_contribution', portfolio: 'OPIF1-BONDS' },
      before: null,
      after: { direction: 'out', amount: '20000.00' },
    });

    expect(changes(repo)).toEqual([
      { field: 'Тип', before: '—', after: 'РЕПО с НКЦ' },
      { field: 'Направление', before: '—', after: 'покупка' },
      { field: 'Ставка РЕПО', before: '—', after: '14.2' },
      { field: 'Вторая часть: дата', before: '—', after: '30.11.2026' },
      { field: 'Вторая часть: сумма', before: '—', after: '30035013.70' },
    ]);
    expect(objectLabel(contribution)).toBe('OPIF1-BONDS: компенсационный взнос по РЕПО');
    expect(changes(contribution)).toEqual([
      { field: 'Сумма', before: '—', after: '20000.00' },
      { field: 'Кто платит', before: '—', after: 'фонд — НКЦ' },
    ]);
  });

  it('describes fee rates and fee payments', () => {
    const rate = event('fee_rate.set', {
      fund: { code: 'OPIF1' },
      after: { kind: 'management', rate: '1.5', valid_from: '2026-11-26' },
    });
    const payment = event('operation.entered', {
      operation: { kind: 'fee_payment', portfolio: 'OPIF1-TREASURY' },
      before: null,
      after: { fee_kind: 'management', period_end: '2026-11-30' },
    });

    expect([actionLabel(rate.type), objectLabel(rate)]).toEqual(['Изменена ставка комиссии от СЧА', 'OPIF1']);
    expect(changes(rate)).toEqual([
      { field: 'Тип', before: '—', after: 'вознаграждение УК' },
      { field: 'ставка', before: '—', after: '1.5' },
      { field: 'Действует с', before: '—', after: '26.11.2026' },
    ]);
    expect(objectLabel(payment)).toBe('OPIF1-TREASURY: выплата вознаграждения');
    expect(changes(payment)).toEqual([
      { field: 'Вид комиссии', before: '—', after: 'вознаграждение УК' },
      { field: 'Оплачено по', before: '—', after: '30.11.2026' },
    ]);
    expect(objectLabel(event('fee_rate.set', { fund: {} }))).toBe('');
  });

  it('describes statements and accepted discrepancies', () => {
    const received = event('statement.received', {
      account: { code: 'OPIF1-BROKER' },
      date: '2026-12-02',
      before: { opening_cash: '46026949.95', closing_cash: '46026949.95', trades: 0, movements: 0 },
      after: { opening_cash: '46026949.95', closing_cash: '46911949.95', trades: 0, movements: 1 },
    });
    const accepted = event('discrepancy.accepted', {
      account: { code: 'OPIF1-BROKER' },
      date: '2026-12-02',
      before: null,
      after: { line: 'movement:coupon::i-1', status: 'not_in_statement', system: '885000', statement: null },
    });

    expect([actionLabel(received.type), objectLabel(received)]).toEqual([
      'Получена выписка по счёту',
      'OPIF1-BROKER, 02.12.2026',
    ]);
    expect(changes(received)).toEqual([
      { field: 'Остаток на конец', before: '46026949.95', after: '46911949.95' },
      { field: 'Движений денег', before: '0', after: '1' },
    ]);
    expect([actionLabel(accepted.type), objectLabel(accepted)]).toEqual([
      'Принято расхождение сверки',
      'OPIF1-BROKER, 02.12.2026',
    ]);
    expect(changes(accepted)).toEqual([
      { field: 'Статус', before: '—', after: 'нет в выписке' },
      { field: 'Строка сверки', before: '—', after: 'movement:coupon::i-1' },
      { field: 'В системе', before: '—', after: '885000' },
      { field: 'В выписке', before: '—', after: '—' },
    ]);
    expect(objectLabel(event('statement.received', { account: {}, date: '2026-12-02' }))).toBe(', 02.12.2026');
    expect(['matched', 'mismatched', 'not_in_system'].map((status) => changes(event('x', { after: { status } }))[0]?.after)).toEqual([
      'сходится',
      'расходится',
      'нет в системе',
    ]);
  });

  it('describes prices for past days', () => {
    const backfilled = event('prices.backfilled', {
      since: '2026-10-22',
      instruments: [{ id: 'i-1', secid: 'LQDT', since: '2026-10-26' }, { id: 'i-2', secid: 'SU26238RMFS4', since: '2026-10-22' }],
    });

    expect([actionLabel(backfilled.type), objectLabel(backfilled)]).toEqual([
      'Цены за прошлые дни',
      'LQDT, SU26238RMFS4 — с 22.10.2026',
    ]);
    expect(changes(backfilled)).toEqual([]);
  });

  it('describes fund operations', () => {
    const settled = event('operation.settled', {
      operation: { kind: 'unit_issue', portfolio: 'OPIF1-TREASURY' },
      before: { portfolio: 'OPIF1-TREASURY', receiver: null, status: 'active', kind: 'unit_issue', units: '100000', settled_date: null },
      after: { portfolio: 'OPIF1-TREASURY', receiver: null, status: 'active', kind: 'unit_issue', units: '100000', settled_date: '2026-11-27' },
    });
    const hidden = event('operation.entered', {
      operation: { kind: 'expense_accrual', portfolio: null },
      before: null,
      after: { expense_kind: 'specdep' },
    });

    expect([actionLabel(settled.type), objectLabel(settled)]).toEqual([
      'Отмечен расчёт по паям',
      'OPIF1-TREASURY: выдача паёв',
    ]);
    expect(changes(settled)).toEqual([{ field: 'Расчёт отмечен', before: '—', after: '27.11.2026' }]);
    expect(objectLabel(hidden)).toBe('—: начисление расхода');
    expect(changes(hidden)).toEqual([{ field: 'Вид расхода', before: '—', after: 'специализированный депозитарий' }]);
    expect(['operation.entered', 'operation.cancelled'].map(actionLabel)).toEqual([
      'Проведена операция фонда',
      'Отменена операция фонда',
    ]);
  });

  it('spells out floater terms', () => {
    const item = event('instrument.version_added', {
      before: { floater: null },
      after: { floater: { rate: 'key_rate', margin_bp: '130', capitalized: false, note: 'x' } },
    });

    expect(changes(item)).toEqual([
      {
        field: 'Параметры флоатера',
        before: '—',
        after: 'ставка: ключевая ставка ЦБ; маржа (бп): 130; капитализация: нет; note: x',
      },
    ]);
  });
});

describe('formatTime', () => {
  it('shows Moscow time', () => {
    expect(formatTime('2026-10-25T09:14:03Z')).toBe('25.10.2026, 12:14:03');
  });
});
