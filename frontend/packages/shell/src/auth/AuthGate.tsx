import type { Session } from '@am/platform';
import { useCallback, useEffect, useState, type ReactNode } from 'react';

import { AuthLayout } from './AuthLayout';
import { AuthError, authMessage, identity, type IdentityApi, type SessionData } from './identity';
import { InvitePage } from './InvitePage';
import { LoginPage } from './LoginPage';
import { createSession } from './session';

type Gate =
  | { readonly kind: 'checking' }
  | { readonly kind: 'login' }
  | { readonly kind: 'invite'; readonly token: string }
  | { readonly kind: 'offline'; readonly message: string }
  | { readonly kind: 'ready'; readonly session: Session };

/** Ссылка-приглашение: /invite/<секрет>. */
export function inviteToken(pathname: string): string | null {
  const match = /^\/invite\/([^/]+)\/?$/.exec(pathname);
  return match ? decodeURIComponent(match[1]!) : null;
}

/**
 * Кто перед нами: по cookie сервис входа выдаёт новый токен — сразу приложение;
 * cookie нет или сессия кончилась — страница входа; ссылка-приглашение — задать пароль.
 * Адрес страницы сохраняется: после входа пользователь там, куда шёл.
 */
export function AuthGate({
  children,
  api = identity,
  location = window.location,
  history = window.history,
}: {
  readonly children: (session: Session) => ReactNode;
  readonly api?: IdentityApi;
  readonly location?: Pick<Location, 'pathname' | 'reload' | 'assign'>;
  readonly history?: Pick<History, 'replaceState'>;
}) {
  const invite = inviteToken(location.pathname);
  const [gate, setGate] = useState<Gate>(invite === null ? { kind: 'checking' } : { kind: 'invite', token: invite });

  const start = useCallback(
    (data: SessionData) => setGate({ kind: 'ready', session: createSession(data, { api, location }) }),
    [api, location],
  );

  const check = useCallback(() => {
    setGate({ kind: 'checking' });
    api.refresh().then(start, (error: unknown) =>
      setGate(
        error instanceof AuthError && error.status === 401
          ? { kind: 'login' }
          : { kind: 'offline', message: authMessage(error) },
      ),
    );
  }, [api, start]);

  useEffect(() => {
    if (invite === null) {
      check();
    }
  }, [check, invite]);

  // Со страницы приглашения — на главную: адрес со секретом в истории не оставляем.
  const leaveInvite = () => history.replaceState(null, '', '/');

  switch (gate.kind) {
    case 'checking':
      return (
        <main className="auth-page">
          <p className="muted">Проверяем вход…</p>
        </main>
      );
    case 'login':
      return <LoginPage api={api} onSession={start} />;
    case 'invite':
      return (
        <InvitePage
          token={gate.token}
          api={api}
          onSession={(data) => {
            leaveInvite();
            start(data);
          }}
          onLogin={() => {
            // Уже вошёл — сразу в приложение, иначе — страница входа.
            leaveInvite();
            check();
          }}
        />
      );
    case 'offline':
      return (
        <AuthLayout title="Сервис входа недоступен">
          <p className="notice notice-error">{gate.message}</p>
          <button type="button" className="primary auth-wide" onClick={check}>
            Повторить
          </button>
        </AuthLayout>
      );
    case 'ready':
      return <>{children(gate.session)}</>;
  }
}
