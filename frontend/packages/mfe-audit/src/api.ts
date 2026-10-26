import type { ApiClient } from '@am/platform';

export interface AuditEvent {
  readonly event_id: string;
  readonly occurred_at: string;
  readonly service: string;
  readonly type: string;
  readonly actor: { readonly subject: string; readonly username: string };
  readonly entity_type: string;
  readonly entity_id: string;
  readonly data: Readonly<Record<string, unknown>>;
  readonly comment: string | null;
}

export interface EventPage {
  readonly items: readonly AuditEvent[];
  /** Передать как ``before`` для следующей страницы; null — это всё. */
  readonly next_before: string | null;
}

export const PAGE_SIZE = 50;

export function auditApi(client: ApiClient) {
  return {
    events: (before: string | null) => {
      const query = new URLSearchParams({ limit: String(PAGE_SIZE) });
      if (before) {
        query.set('before', before);
      }
      return client.get<EventPage>(`/api/audit/events?${query.toString()}`);
    },
  };
}

export type AuditApi = ReturnType<typeof auditApi>;
