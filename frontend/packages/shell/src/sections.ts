import { hasRole, type Session } from '@am/platform';

import type { IconName } from './components/Icon';

/** Кому какой раздел виден: те же правила, что у маршрутов. */
export interface Access {
  readonly admin: boolean;
  /** Сделки вводят все авторизованные пользователи (решение 25): трейдинг, сопровождение, админ. */
  readonly books: boolean;
  /** Лимиты смотрит и риск-менеджмент: он ведёт набор лимитов (design/09). */
  readonly limits: boolean;
}

export function accessOf(session: Session): Access {
  const books = hasRole(session, 'trading') || hasRole(session, 'operations');
  return { admin: hasRole(session, 'admin'), books, limits: books || hasRole(session, 'risk') };
}

export type GroupId = 'market' | 'portfolios' | 'management';

export const GROUPS: readonly { readonly id: GroupId; readonly label: string }[] = [
  { id: 'market', label: 'Рынок' },
  { id: 'portfolios', label: 'Портфели' },
  { id: 'management', label: 'Управление' },
];

export interface Section {
  readonly path: string;
  readonly label: string;
  /** Одна строка на карточке стартовой страницы: что в разделе. */
  readonly description: string;
  readonly icon: IconName;
  readonly group: GroupId;
  readonly visible: (access: Access) => boolean;
}

const everyone = () => true;

/** Разделы в порядке навигации. Отсюда строятся боковая панель и стартовая страница. */
export const SECTIONS: readonly Section[] = [
  {
    path: '/refdata',
    label: 'Справочник',
    description: 'Бумаги, эмитенты, календарь',
    icon: 'books',
    group: 'market',
    visible: everyone,
  },
  {
    path: '/marketdata',
    label: 'Цены',
    description: 'Оценка, разброс, расчёт по облигации',
    icon: 'chart',
    group: 'market',
    visible: everyone,
  },
  {
    path: '/booking',
    label: 'Сделки',
    description: 'Ввод, позиции, результат, СЧА',
    icon: 'exchange',
    group: 'portfolios',
    visible: (a) => a.books,
  },
  {
    path: '/reconciliation',
    label: 'Сверка',
    description: 'Выписки, расхождения, закрытие дня',
    icon: 'checks',
    group: 'portfolios',
    visible: (a) => a.books,
  },
  {
    path: '/limits',
    label: 'Лимиты',
    description: 'Использование, нарушения, наборы',
    icon: 'shield',
    group: 'portfolios',
    visible: (a) => a.limits,
  },
  {
    path: '/admin',
    label: 'Администрирование',
    description: 'Портфели, счета, доступы, тарифы',
    icon: 'settings',
    group: 'management',
    visible: (a) => a.admin,
  },
  {
    path: '/audit',
    label: 'Журнал',
    description: 'Кто, что и когда изменил',
    icon: 'history',
    group: 'management',
    visible: (a) => a.admin,
  },
];

/** Раздел, в котором открыт адрес; null — главная или неизвестная страница. */
export function sectionAt(pathname: string): Section | null {
  return SECTIONS.find((s) => pathname === s.path || pathname.startsWith(`${s.path}/`)) ?? null;
}
