import { useEffect, useState } from 'react';

/**
 * Настройки вида в браузере пользователя: свёрнутая панель, тема. Хранилище может
 * быть недоступно (приватное окно, запрет сайта) — тогда действуют значения по умолчанию.
 */
function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Не запомнили — не беда: настройка действует до перезагрузки.
  }
}

export const COLLAPSED_KEY = 'am.sidebar.collapsed';
export const THEME_KEY = 'am.theme';

export function useCollapsed(): [boolean, (value: boolean) => void] {
  const [collapsed, setCollapsed] = useState(() => read(COLLAPSED_KEY) === 'true');
  const change = (value: boolean) => {
    setCollapsed(value);
    write(COLLAPSED_KEY, String(value));
  };
  return [collapsed, change];
}

export type Theme = 'system' | 'light' | 'dark';

export const THEMES: readonly Theme[] = ['system', 'light', 'dark'];

/** Тема: «как в системе» — без атрибута, светлая и тёмная — data-theme на <html>. */
export function useTheme(): [Theme, (value: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    const stored = read(THEME_KEY);
    return THEMES.find((t) => t === stored) ?? 'system';
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'system') {
      delete root.dataset.theme;
    } else {
      root.dataset.theme = theme;
    }
  }, [theme]);
  const change = (value: Theme) => {
    setTheme(value);
    write(THEME_KEY, value);
  };
  return [theme, change];
}
