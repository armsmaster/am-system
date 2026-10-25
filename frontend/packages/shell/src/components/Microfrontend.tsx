import type { Session } from '@am/platform';
import { Component, lazy, Suspense, useMemo, useState, type ReactNode } from 'react';

import type { MicrofrontendLoader } from '../microfrontends';

interface MicrofrontendViewProps {
  readonly load: MicrofrontendLoader;
  readonly session: Session;
  readonly basePath: string;
}

/**
 * Место, куда монтируется микрофронтенд.
 * Если remote не загрузился или упал, shell продолжает работать,
 * а пользователь может повторить загрузку.
 */
export function Microfrontend({ load, session, basePath }: MicrofrontendViewProps) {
  const [attempt, setAttempt] = useState(0);
  // Новая попытка — новый lazy-компонент: React кэширует результат загрузки.
  const Remote = useMemo(() => lazy(load), [load, attempt]);

  return (
    <RemoteBoundary key={attempt} onRetry={() => setAttempt((n) => n + 1)}>
      <Suspense fallback={<p className="muted">Загрузка раздела…</p>}>
        <Remote session={session} basePath={basePath} />
      </Suspense>
    </RemoteBoundary>
  );
}

interface BoundaryProps {
  readonly onRetry: () => void;
  readonly children: ReactNode;
}

interface BoundaryState {
  readonly failed: boolean;
}

export class RemoteBoundary extends Component<BoundaryProps, BoundaryState> {
  override state: BoundaryState = { failed: false };

  static getDerivedStateFromError(): BoundaryState {
    return { failed: true };
  }

  override componentDidCatch(error: unknown): void {
    console.error('Microfrontend failed', error);
  }

  override render() {
    if (this.state.failed) {
      return (
        <div role="alert" className="notice notice-error">
          <p>Раздел временно недоступен.</p>
          <button type="button" onClick={this.props.onRetry}>
            Повторить
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
