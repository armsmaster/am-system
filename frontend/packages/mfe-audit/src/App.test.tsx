import { ApiError, type ApiClient, type Role } from '@am/platform';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { auditApi, type AuditEvent, type EventPage } from './api';
import AuditApp, { JournalPage } from './App';

afterEach(() => {
  vi.unstubAllGlobals();
});

function session(...roles: Role[]) {
  return {
    username: 'admin',
    displayName: 'Админ',
    roles: new Set(roles),
    getAccessToken: () => Promise.resolve('t'),
    logout: () => undefined,
  };
}

function item(n: number, fields: Partial<AuditEvent> = {}): AuditEvent {
  return {
    event_id: `e-${n}`,
    occurred_at: '2026-10-25T09:14:03Z',
    service: 'portfolio',
    type: 'portfolio.updated',
    actor: { subject: 'u-1', username: 'admin' },
    entity_type: 'portfolio',
    entity_id: `p-${n}`,
    data: { before: { code: `P${n}`, name: 'Было' }, after: { code: `P${n}`, name: 'Стало' } },
    comment: null,
    ...fields,
  };
}

function pages(...results: (EventPage | Error)[]) {
  const events = vi.fn((_before: string | null) => {
    const next = results.shift()!;
    return next instanceof Error ? Promise.reject(next) : Promise.resolve(next);
  });
  return { events };
}

describe('JournalPage', () => {
  it('shows events with actions and changes', async () => {
    render(<JournalPage api={pages({ items: [item(1, { comment: 'опечатка' })], next_before: null })} />);

    expect(await screen.findByText('Изменён портфель')).toBeInTheDocument();
    expect(screen.getByText('Название: Было → Стало')).toBeInTheDocument();
    expect(screen.getByText('опечатка')).toBeInTheDocument();
    expect(screen.getByText('25.10.2026, 12:14:03')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Показать ещё' })).not.toBeInTheDocument();
  });

  it('loads older events on request', async () => {
    const api = pages(
      { items: [item(2)], next_before: 'e-2' },
      { items: [item(1)], next_before: null },
    );
    render(<JournalPage api={api} />);

    await userEvent.click(await screen.findByRole('button', { name: 'Показать ещё' }));

    expect(await screen.findByText('P1')).toBeInTheDocument();
    expect(screen.getByText('P2')).toBeInTheDocument();
    expect(api.events.mock.calls).toEqual([[null], ['e-2']]);
  });

  it('says when the journal is empty', async () => {
    render(<JournalPage api={pages({ items: [], next_before: null })} />);

    expect(await screen.findByText('Событий пока нет.')).toBeInTheDocument();
  });

  it('explains a failure', async () => {
    render(<JournalPage api={pages(new ApiError(403, null))} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('Недостаточно прав');
  });
});

describe('AuditApp', () => {
  it('refuses everyone but admin', () => {
    render(<AuditApp session={session('operations')} basePath="/audit" />);

    expect(screen.getByRole('alert')).toHaveTextContent('только администратору');
  });

  it('reads the journal from the audit service', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response('{"items":[],"next_before":null}', { status: 200 })),
    );
    vi.stubGlobal('fetch', fetchMock);

    render(<AuditApp session={session('admin')} basePath="/audit" />);

    expect(await screen.findByText('Событий пока нет.')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/audit/events?limit=50', expect.anything());
  });
});

describe('auditApi', () => {
  it('asks for the next page after a given event', async () => {
    const get = vi.fn((..._args: unknown[]) => Promise.resolve(null));
    const api = auditApi({ get } as unknown as ApiClient);

    await api.events('e-9');

    expect(get).toHaveBeenCalledWith('/api/audit/events?limit=50&before=e-9');
  });
});
