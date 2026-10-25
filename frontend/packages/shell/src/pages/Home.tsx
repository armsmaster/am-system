import { createApiClient, ROLE_LABELS, type Role, type Session } from '@am/platform';
import { useEffect, useState } from 'react';

interface Me {
  readonly subject: string;
  readonly username: string;
  readonly roles: readonly Role[];
}

type State =
  | { readonly kind: 'loading' }
  | { readonly kind: 'ready'; readonly me: Me }
  | { readonly kind: 'failed' };

/**
 * Стартовая страница. Пока показывает, как сервисы видят пользователя:
 * это сквозная проверка вход → шлюз → сервис → проверка токена.
 */
export function Home({ session }: { readonly session: Session }) {
  const [state, setState] = useState<State>({ kind: 'loading' });

  useEffect(() => {
    let active = true;
    createApiClient(session)
      .get<Me>('/api/portfolio/me')
      .then(
        (me): State => ({ kind: 'ready', me }),
        (): State => ({ kind: 'failed' }),
      )
      .then((next) => {
        // Ответ мог прийти, когда пользователь уже ушёл со страницы.
        if (active) {
          setState(next);
        }
      });
    return () => {
      active = false;
    };
  }, [session]);

  return (
    <section>
      <h1>Добро пожаловать, {session.displayName}</h1>
      {state.kind === 'loading' && <p className="muted">Проверяем связь с сервисами…</p>}
      {state.kind === 'failed' && (
        <p role="alert" className="notice notice-error">
          Сервис портфелей недоступен.
        </p>
      )}
      {state.kind === 'ready' && (
        <dl className="facts">
          <dt>Пользователь в сервисах</dt>
          <dd>{state.me.username}</dd>
          <dt>Роли</dt>
          <dd>{state.me.roles.map((role) => ROLE_LABELS[role]).join(', ') || 'нет'}</dd>
        </dl>
      )}
    </section>
  );
}
