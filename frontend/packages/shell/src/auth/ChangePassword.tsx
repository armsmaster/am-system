import type { Session } from '@am/platform';
import { Drawer } from '@am/ui';
import { useState, type FormEvent } from 'react';

import { NewPassword, passwordReady, Problem } from './AuthLayout';
import { authMessage, identity, type IdentityApi } from './identity';

/** Смена своего пароля: текущий, новый и повтор. Открывается из меню пользователя. */
export function ChangePassword({
  session,
  onClose,
  api = identity,
}: {
  readonly session: Session;
  readonly onClose: () => void;
  readonly api?: IdentityApi;
}) {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [repeat, setRepeat] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.changePassword(await session.getAccessToken(), current, next);
      setDone(true);
    } catch (failure) {
      setError(authMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Drawer title="Смена пароля" open onClose={onClose}>
      {done ? (
        <section>
          <p className="notice notice-ok" role="status">
            Пароль изменён. В следующий раз входите с новым.
          </p>
          <button type="button" onClick={onClose}>
            Готово
          </button>
        </section>
      ) : (
        <form className="drawer-form" aria-label="Смена пароля" onSubmit={(e) => void submit(e)}>
          <label>
            Текущий пароль
            <input
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
            />
          </label>
          <NewPassword
            username={session.username}
            password={next}
            repeat={repeat}
            onPassword={setNext}
            onRepeat={setRepeat}
          />
          <Problem message={error} />
          <button
            type="submit"
            className="primary"
            disabled={busy || current === '' || !passwordReady(next, repeat, session.username)}
          >
            {busy ? 'Сохраняем…' : 'Сменить пароль'}
          </button>
        </form>
      )}
    </Drawer>
  );
}
