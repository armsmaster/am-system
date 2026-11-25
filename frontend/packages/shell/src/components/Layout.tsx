import { ROLE_LABELS, type Session } from '@am/platform';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { NavLink, useLocation } from 'react-router';

import { ChangePassword } from '../auth/ChangePassword';

import { THEMES, useCollapsed, useTheme, type Theme } from '../preferences';
import { GROUPS, SECTIONS, sectionAt, type Access } from '../sections';
import { Icon, type IconName } from './Icon';
import { Logo } from './Logo';

/**
 * Каркас приложения: боковая панель с разделами по группам, верхняя панель с тем,
 * где пользователь, темой и меню пользователя. Панель сворачивается в полосу иконок
 * (широкие таблицы); на узком экране открывается кнопкой поверх содержимого.
 */
export function Layout({
  session,
  access,
  children,
}: {
  readonly session: Session;
  readonly access: Access;
  readonly children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useCollapsed();
  const [drawer, setDrawer] = useState(false);
  const { pathname } = useLocation();

  // Перешли в другой раздел — выдвижная панель на узком экране закрывается.
  useEffect(() => setDrawer(false), [pathname]);

  const classes = ['layout', collapsed && 'collapsed', drawer && 'drawer-open'].filter(Boolean);
  return (
    <div className={classes.join(' ')}>
      <Sidebar access={access} collapsed={collapsed} onCollapse={() => setCollapsed(!collapsed)} />
      {drawer && <div className="scrim" aria-hidden="true" onClick={() => setDrawer(false)} />}
      <div className="main">
        <Topbar session={session} pathname={pathname} onMenu={() => setDrawer(true)} />
        <main className="content">{children}</main>
      </div>
    </div>
  );
}

function Sidebar({
  access,
  collapsed,
  onCollapse,
}: {
  readonly access: Access;
  readonly collapsed: boolean;
  readonly onCollapse: () => void;
}) {
  const item = (to: string, label: string, icon: IconName, end = false) => (
    <NavLink key={to} to={to} end={end} className="nav-item" title={collapsed ? label : undefined}>
      <Icon name={icon} />
      <span className="nav-label">{label}</span>
    </NavLink>
  );
  return (
    <aside className="sidebar">
      <div className="brand">
        <Logo />
        <span className="nav-label">AM System</span>
      </div>
      <nav className="nav" aria-label="Разделы">
        {item('/', 'Главная', 'home', true)}
        {GROUPS.map((group) => {
          const sections = SECTIONS.filter((s) => s.group === group.id && s.visible(access));
          if (sections.length === 0) {
            return null;
          }
          return (
            <div key={group.id} className="nav-group" role="group" aria-label={group.label}>
              <div className="nav-group-label" aria-hidden="true">
                {group.label}
              </div>
              {sections.map((s) => item(s.path, s.label, s.icon))}
            </div>
          );
        })}
      </nav>
      <button
        type="button"
        className="nav-item collapse-toggle"
        aria-label={collapsed ? 'Развернуть панель' : 'Свернуть панель'}
        onClick={onCollapse}
      >
        <Icon name={collapsed ? 'expand' : 'collapse'} />
        <span className="nav-label">Свернуть</span>
      </button>
    </aside>
  );
}

function Topbar({
  session,
  pathname,
  onMenu,
}: {
  readonly session: Session;
  readonly pathname: string;
  readonly onMenu: () => void;
}) {
  const section = sectionAt(pathname);
  const group = section && GROUPS.find((g) => g.id === section.group);
  return (
    <header className="topbar">
      <button type="button" className="icon-button menu-button" aria-label="Открыть разделы" onClick={onMenu}>
        <Icon name="menu" />
      </button>
      <div className="where" aria-label="Где вы">
        {section && group ? (
          <>
            <span className="muted">{group.label}</span>
            <span className="where-sep" aria-hidden="true">
              /
            </span>
            <span>{section.label}</span>
          </>
        ) : (
          pathname === '/' && <span>Главная</span>
        )}
      </div>
      <ThemeSwitch />
      <UserMenu session={session} />
    </header>
  );
}

const THEME_LABELS: Readonly<Record<Theme, string>> = {
  system: 'Тема как в системе',
  light: 'Светлая тема',
  dark: 'Тёмная тема',
};
const THEME_ICONS: Readonly<Record<Theme, IconName>> = { system: 'system', light: 'sun', dark: 'moon' };

function ThemeSwitch() {
  const [theme, setTheme] = useTheme();
  return (
    <div className="segmented" role="group" aria-label="Тема">
      {THEMES.map((value) => (
        <button
          key={value}
          type="button"
          className="icon-button"
          aria-label={THEME_LABELS[value]}
          aria-pressed={theme === value}
          title={THEME_LABELS[value]}
          onClick={() => setTheme(value)}
        >
          <Icon name={THEME_ICONS[value]} size={16} />
        </button>
      ))}
    </div>
  );
}

/** «Иван Иванов» → «ИИ»; одно слово — первые две буквы. */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const letters = words.length > 1 ? words[0]![0]! + words[1]![0]! : (words[0] ?? '?').slice(0, 2);
  return letters.toUpperCase();
}

function UserMenu({ session }: { readonly session: Session }) {
  const [open, setOpen] = useState(false);
  const [changing, setChanging] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const roles = [...session.roles].map((role) => ROLE_LABELS[role]).join(', ');

  // Щелчок мимо меню или Escape закрывают его.
  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const outside = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('mousedown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);

  return (
    <div className="user-menu" ref={box}>
      <button type="button" className="user-button" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="avatar" aria-hidden="true">
          {initials(session.displayName)}
        </span>
        <span className="user-name">{session.displayName}</span>
        <Icon name="chevron" size={14} />
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label="Пользователь">
          <div className="popover-head">
            <div>{session.displayName}</div>
            <div className="muted">{roles || 'Ролей нет'}</div>
          </div>
          <button
            type="button"
            className="popover-item"
            onClick={() => {
              setOpen(false);
              setChanging(true);
            }}
          >
            <Icon name="key" size={16} />
            Сменить пароль
          </button>
          <button type="button" className="popover-item" onClick={() => session.logout()}>
            <Icon name="logout" size={16} />
            Выйти
          </button>
        </div>
      )}
      {changing && <ChangePassword session={session} onClose={() => setChanging(false)} />}
    </div>
  );
}
