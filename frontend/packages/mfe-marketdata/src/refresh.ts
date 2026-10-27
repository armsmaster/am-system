import { useEffect } from 'react';

/** Переоценка внутри дня (решение 68): экран с ценами обновляется сам. */
export const REFRESH_MS = 30_000;

/** Вызывает ``callback`` каждые ``ms``, пока ``enabled``; ``callback`` — стабильный. */
export function useEvery(callback: () => void, ms: number, enabled = true): void {
  useEffect(() => {
    if (!enabled) {
      return undefined;
    }
    const timer = setInterval(callback, ms);
    return () => clearInterval(timer);
  }, [callback, ms, enabled]);
}
