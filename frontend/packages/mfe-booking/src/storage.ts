import { useState } from 'react';

/**
 * Значение, которое помнит этот браузер: колонки, группировка, размер шрифта.
 * Хранилище недоступно (приватный режим) — выбор просто не запомнится.
 */
export function useStored<T>(
  key: string,
  parse: (raw: string | null) => T,
  format: (value: T) => string = String,
): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => parse(read(key)));
  const update = (next: T) => {
    setValue(next);
    try {
      localStorage.setItem(key, format(next));
    } catch {
      // Не запомнится — и ладно: на экране выбор уже применён.
    }
  };
  return [value, update];
}

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
