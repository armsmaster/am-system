import { errorMessage } from '@am/platform';
import { useCallback, useEffect, useState } from 'react';

export type Loaded<T> =
  | { readonly status: 'loading' }
  | { readonly status: 'ready'; readonly data: T }
  | { readonly status: 'failed'; readonly message: string };

/**
 * Загружает данные и возвращает функцию перезагрузки.
 * ``load`` должна быть стабильной (useCallback), иначе загрузка зациклится.
 * При перезагрузке старые данные остаются на экране до прихода новых.
 */
export function useLoad<T>(load: () => Promise<T>): [Loaded<T>, () => void] {
  const [state, setState] = useState<Loaded<T>>({ status: 'loading' });
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let active = true;
    load()
      .then(
        (data): Loaded<T> => ({ status: 'ready', data }),
        (error: unknown): Loaded<T> => ({ status: 'failed', message: errorMessage(error) }),
      )
      .then((next) => {
        if (active) {
          setState(next);
        }
      });
    return () => {
      active = false;
    };
  }, [load, version]);

  const reload = useCallback(() => setVersion((n) => n + 1), []);
  return [state, reload];
}

/**
 * Выполняет изменение: показывает ошибку, если сервер отказал,
 * и перезагружает данные, если всё прошло.
 */
export function useAction(onDone: () => void) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = useCallback(
    async (action: () => Promise<unknown>): Promise<boolean> => {
      setBusy(true);
      setError(null);
      try {
        await action();
        onDone();
        return true;
      } catch (failure) {
        setError(errorMessage(failure));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [onDone],
  );

  return { run, error, busy };
}
