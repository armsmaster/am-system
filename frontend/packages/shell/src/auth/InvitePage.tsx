import { useEffect, useState, type FormEvent } from 'react';

import { AuthLayout, NewPassword, passwordReady, Problem, RememberMe } from './AuthLayout';
import { authMessage, type IdentityApi, type InviteInfo, type SessionData } from './identity';

type Loaded = { readonly status: 'loading' } | { readonly status: 'failed'; readonly message: string } | {
  readonly status: 'ready';
  readonly info: InviteInfo;
};

const DATE_TIME = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

/** Ссылка-приглашение: задать пароль и сразу войти. Ссылка одноразовая. */
export function InvitePage({
  token,
  api,
  onSession,
  onLogin,
}: {
  readonly token: string;
  readonly api: IdentityApi;
  readonly onSession: (data: SessionData) => void;
  readonly onLogin: () => void;
}) {
  const [state, setState] = useState<Loaded>({ status: 'loading' });

  useEffect(() => {
    let active = true;
    api.invite(token).then(
      (info) => active && setState({ status: 'ready', info }),
      (error: unknown) => active && setState({ status: 'failed', message: authMessage(error) }),
    );
    return () => {
      active = false;
    };
  }, [api, token]);

  if (state.status === 'loading') {
    return (
      <AuthLayout title="Приглашение">
        <p className="muted">Проверяем ссылку…</p>
      </AuthLayout>
    );
  }
  if (state.status === 'failed') {
    return (
      <AuthLayout title="Ссылка не работает">
        <p className="notice notice-error">{state.message}</p>
        <button type="button" className="primary auth-wide" onClick={onLogin}>
          На страницу входа
        </button>
      </AuthLayout>
    );
  }
  return <SetPassword token={token} info={state.info} api={api} onSession={onSession} />;
}

function SetPassword({
  token,
  info,
  api,
  onSession,
}: {
  readonly token: string;
  readonly info: InviteInfo;
  readonly api: IdentityApi;
  readonly onSession: (data: SessionData) => void;
}) {
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSession(await api.acceptInvite(token, password, remember));
    } catch (failure) {
      setError(authMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout title="Задайте пароль">
      <p className="auth-lead">
        Здравствуйте{info.full_name ? `, ${info.full_name}` : ''}! Ваш логин — <b>{info.username}</b>. Придумайте
        пароль: с ним вы будете входить в систему.
      </p>
      <p className="hint muted">Ссылка действует до {DATE_TIME.format(new Date(info.expires_at))}.</p>
      <form className="auth-form" aria-label="Пароль по приглашению" onSubmit={(e) => void submit(e)}>
        <NewPassword
          username={info.username}
          password={password}
          repeat={repeat}
          onPassword={setPassword}
          onRepeat={setRepeat}
        />
        <RememberMe checked={remember} onChange={setRemember} />
        <Problem message={error} />
        <button type="submit" className="primary" disabled={busy || !passwordReady(password, repeat, info.username)}>
          {busy ? 'Сохраняем…' : 'Задать пароль и войти'}
        </button>
      </form>
    </AuthLayout>
  );
}
