import type { ReactNode } from 'react';

import { Logo } from '../components/Logo';
import { passwordRules } from './password';

/** Карточка по центру экрана: вход, первый вход, приглашение. */
export function AuthLayout({ title, children }: { readonly title: string; readonly children: ReactNode }) {
  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <Logo />
          <span>AM System</span>
        </div>
        <h1>{title}</h1>
        {children}
      </div>
    </main>
  );
}

/** Новый пароль и повтор; под ними — правила, которые отмечаются по мере ввода. */
export function NewPassword({
  username,
  password,
  repeat,
  onPassword,
  onRepeat,
}: {
  readonly username: string;
  readonly password: string;
  readonly repeat: string;
  readonly onPassword: (value: string) => void;
  readonly onRepeat: (value: string) => void;
}) {
  return (
    <>
      <label>
        Новый пароль
        <input
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => onPassword(e.target.value)}
        />
      </label>
      <label>
        Повторите пароль
        <input
          type="password"
          autoComplete="new-password"
          value={repeat}
          onChange={(e) => onRepeat(e.target.value)}
        />
      </label>
      <ul className="password-rules" aria-label="Требования к паролю">
        {passwordRules(password, repeat, username).map((rule) => (
          <li key={rule.label} className={rule.ok ? 'ok' : undefined}>
            <span aria-hidden="true">{rule.ok ? '✓' : '•'}</span> {rule.label}
            <span className="sr-only">{rule.ok ? ' — выполнено' : ' — не выполнено'}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

export function passwordReady(password: string, repeat: string, username: string): boolean {
  return passwordRules(password, repeat, username).every((rule) => rule.ok);
}

export function Problem({ message }: { readonly message: string | null }) {
  return message ? (
    <p role="alert" className="notice notice-error">
      {message}
    </p>
  ) : null;
}

/** «Запомнить меня» с пояснением, чем она опасна на чужом компьютере. */
export function RememberMe({ checked, onChange }: { readonly checked: boolean; readonly onChange: (v: boolean) => void }) {
  return (
    <>
      <label className="checkbox">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        Запомнить меня
      </label>
      <p className="hint muted">
        Вход сохранится на неделю, даже если закрыть браузер. На чужом или общем компьютере не отмечайте.
      </p>
    </>
  );
}
