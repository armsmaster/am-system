import { useEffect, type ReactNode } from 'react';

/**
 * Общие элементы страниц разделов: заголовок с действиями, быстрые фильтры, вкладки,
 * выдвижная панель для форм. Оформление — в общих стилях shell.
 */

/** Заголовок страницы: название слева, действия справа. */
export function PageHeader({ title, actions }: { readonly title: ReactNode; readonly actions?: ReactNode }) {
  return (
    <div className="page-header">
      <h1>{title}</h1>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}

export interface Choice<T extends string> {
  readonly value: T;
  readonly label: string;
  readonly count?: number;
}

/** Быстрые фильтры: одна нажатая кнопка из нескольких, со счётчиками. */
export function FilterChips<T extends string>({
  label,
  choices,
  value,
  onChange,
}: {
  readonly label: string;
  readonly choices: readonly Choice<T>[];
  readonly value: T;
  readonly onChange: (value: T) => void;
}) {
  return (
    <div className="filter-chips" role="group" aria-label={label}>
      {choices.map((choice) => (
        <button
          key={choice.value}
          type="button"
          aria-pressed={choice.value === value}
          onClick={() => onChange(choice.value)}
        >
          {choice.label}
          {choice.count !== undefined && <> <span className="chip-count">{choice.count}</span></>}
        </button>
      ))}
    </div>
  );
}

/** Вкладки внутри страницы: что показать под ними, решает страница. */
export function Tabs<T extends string>({
  label,
  tabs,
  value,
  onChange,
}: {
  readonly label: string;
  readonly tabs: readonly Choice<T>[];
  readonly value: T;
  readonly onChange: (value: T) => void;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((tab) => (
        <button
          key={tab.value}
          type="button"
          role="tab"
          aria-selected={tab.value === value}
          onClick={() => onChange(tab.value)}
        >
          {tab.label}
          {tab.count !== undefined && <> <span className="chip-count">{tab.count}</span></>}
        </button>
      ))}
    </div>
  );
}

/**
 * Выдвижная панель справа — для форм: таблицы остаются на всю ширину, форма
 * открывается по кнопке. Закрывается кнопкой, Escape или щелчком мимо.
 */
export function Drawer({
  title,
  open,
  onClose,
  children,
}: {
  readonly title: string;
  readonly open: boolean;
  readonly onClose: () => void;
  readonly children: ReactNode;
}) {
  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, [open, onClose]);

  if (!open) {
    return null;
  }
  return (
    <>
      <div className="drawer-scrim" aria-hidden="true" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={title}>
        <div className="drawer-head">
          <h2>{title}</h2>
          <button type="button" className="drawer-close" aria-label="Закрыть" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </>
  );
}
