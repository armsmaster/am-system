import { useState, type FormEvent } from 'react';

import { AuthLayout, NewPassword, passwordReady, Problem, RememberMe } from './AuthLayout';
import { AuthError, authMessage, type IdentityApi, type SessionData } from './identity';

interface Props {
  readonly api: IdentityApi;
  readonly onSession: (data: SessionData) => void;
}

/** Вход по логину и паролю. Временный пароль — сразу форма «задайте свой». */
export function LoginPage({ api, onSession }: Props) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [temporary, setTemporary] = useState(false);
  const [forgot, setForgot] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSession(await api.login(username.trim(), password, remember));
    } catch (failure) {
      if (failure instanceof AuthError && failure.code === 'password_change_required') {
        setTemporary(true);
      } else {
        setError(authMessage(failure));
      }
    } finally {
      setBusy(false);
    }
  }

  if (temporary) {
    return (
      <FirstLogin
        api={api}
        username={username.trim()}
        password={password}
        remember={remember}
        onSession={onSession}
        onBack={() => {
          setTemporary(false);
          setPassword('');
        }}
      />
    );
  }

  return (
    <AuthLayout title="Вход">
      <form className="auth-form" aria-label="Вход" onSubmit={(e) => void submit(e)}>
        <label>
          Логин
          <input
            autoComplete="username"
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label>
          Пароль
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <RememberMe checked={remember} onChange={setRemember} />
        <Problem message={error} />
        <button type="submit" className="primary" disabled={busy || username.trim() === '' || password === ''}>
          {busy ? 'Входим…' : 'Войти'}
        </button>
        <button type="button" className="link" aria-expanded={forgot} onClick={() => setForgot(!forgot)}>
          Забыли пароль?
        </button>
        {forgot && (
          <p className="hint muted">
            Попросите администратора выдать новую ссылку для входа — по ней вы зададите новый пароль.
          </p>
        )}
      </form>
    </AuthLayout>
  );
}

function FirstLogin({
  api,
  username,
  password,
  remember,
  onSession,
  onBack,
}: Props & {
  readonly username: string;
  readonly password: string;
  readonly remember: boolean;
  readonly onBack: () => void;
}) {
  const [next, setNext] = useState('');
  const [repeat, setRepeat] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSession(await api.firstLogin(username, password, next, remember));
    } catch (failure) {
      setError(authMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout title="Задайте свой пароль">
      <p className="auth-lead">
        Вы вошли с временным паролем, его выдал администратор. Придумайте свой — временный перестанет работать.
      </p>
      <form className="auth-form" aria-label="Свой пароль" onSubmit={(e) => void submit(e)}>
        <NewPassword username={username} password={next} repeat={repeat} onPassword={setNext} onRepeat={setRepeat} />
        <Problem message={error} />
        <button type="submit" className="primary" disabled={busy || !passwordReady(next, repeat, username)}>
          {busy ? 'Сохраняем…' : 'Сохранить и войти'}
        </button>
        <button type="button" className="link" onClick={onBack}>
          Назад ко входу
        </button>
      </form>
    </AuthLayout>
  );
}
