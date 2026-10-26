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
