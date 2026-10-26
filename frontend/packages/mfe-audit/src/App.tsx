import { createApiClient, errorMessage, hasRole, type MicrofrontendProps } from '@am/platform';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { auditApi, type AuditApi, type AuditEvent } from './api';
import { actionLabel, changes, formatTime, objectLabel } from './describe';

/** Журнал действий пользователей. Видит только администратор. */
export default function AuditApp({ session }: MicrofrontendProps) {
  const api = useMemo(() => auditApi(createApiClient(session)), [session]);

  if (!hasRole(session, 'admin')) {
    return <p role="alert">Журнал доступен только администратору.</p>;
  }
  return <JournalPage api={api} />;
}

export function JournalPage({ api }: { readonly api: AuditApi }) {
  const [items, setItems] = useState<readonly AuditEvent[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (before: string | null) => {
      setLoading(true);
      setError(null);
      try {
        const page = await api.events(before);
        setItems((current) => (before ? [...current, ...page.items] : page.items));
        setNext(page.next_before);
      } catch (failure) {
        setError(errorMessage(failure));
      } finally {
        setLoading(false);
      }
    },
    [api],
  );

  useEffect(() => {
    void load(null);
  }, [load]);

  return (
    <section>
      <h1>Журнал действий</h1>
      <p className="muted">Новые сверху. Записи журнала нельзя изменить или удалить.</p>
      {items.length > 0 && <EventsTable items={items} />}
      {!loading && !error && items.length === 0 && <p className="muted">Событий пока нет.</p>}
      {error && (
        <p role="alert" className="notice notice-error">
          {error}
        </p>
      )}
      {loading && <p className="muted">Загрузка…</p>}
      {next && !loading && (
        <button type="button" onClick={() => void load(next)}>
          Показать ещё
        </button>
      )}
    </section>
  );
}

function EventsTable({ items }: { readonly items: readonly AuditEvent[] }) {
  return (
    <table className="data">
      <thead>
        <tr>
          <th>Время (МСК)</th>
          <th>Пользователь</th>
          <th>Действие</th>
          <th>Объект</th>
          <th>Что изменилось</th>
        </tr>
      </thead>
      <tbody>
        {items.map((event) => (
          <tr key={event.event_id}>
            <td>{formatTime(event.occurred_at)}</td>
            <td>{event.actor.username}</td>
            <td>{actionLabel(event.type)}</td>
            <td>{objectLabel(event)}</td>
            <td>
              <ul className="plain">
                {changes(event).map((change) => (
                  <li key={change.field}>
                    {change.field}: {change.before} → {change.after}
                  </li>
                ))}
              </ul>
              {event.comment && <div className="muted">{event.comment}</div>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
