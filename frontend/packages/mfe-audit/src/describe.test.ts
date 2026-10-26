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

  it('lists every field of a created object, with codes instead of ids', () => {
    const item = event('account.created', {
      after: {
        id: 'a',
        fund_id: 'f-uuid',
        fund_code: 'F1',
        code: 'A1',
        cash_kind: 'broker',
        is_fund: true,
        extra: '',
      },
    });

    expect(changes(item)).toEqual([
      { field: 'Фонд', before: '—', after: 'F1' },
      { field: 'Код', before: '—', after: 'A1' },
      { field: 'Денежный счёт', before: '—', after: 'брокерский' },
      { field: 'Это фонд', before: '—', after: 'да' },
      { field: 'extra', before: '—', after: '—' },
    ]);
  });

  it('has nothing to show without a new state', () => {
    expect(changes(event('access.granted', { user: {} }))).toEqual([]);
  });
});

describe('formatTime', () => {
  it('shows Moscow time', () => {
    expect(formatTime('2026-10-25T09:14:03Z')).toBe('25.10.2026, 12:14:03');
  });
});
