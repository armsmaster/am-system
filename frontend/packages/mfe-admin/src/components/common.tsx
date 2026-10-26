import type { Loaded } from '../hooks';
import type { ReactNode } from 'react';

export function ErrorNote({ message }: { readonly message: string | null }) {
  if (!message) {
    return null;
  }
  return (
    <p role="alert" className="notice notice-error">
      {message}
    </p>
  );
}

/** Показывает загрузку или ошибку, а готовые данные отдаёт ``children``. */
export function LoadedView<T>({
  state,
  children,
}: {
  readonly state: Loaded<T>;
  readonly children: (data: T) => ReactNode;
}) {
  if (state.status === 'loading') {
    return <p className="muted">Загрузка…</p>;
  }
  if (state.status === 'failed') {
    return <ErrorNote message={state.message} />;
  }
  return <>{children(state.data)}</>;
}
