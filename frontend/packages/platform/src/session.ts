/**
 * Сессия пользователя — то, что shell передаёт каждому микрофронтенду.
 *
 * Микрофронтенд не знает про Keycloak: он получает готовую сессию
 * и берёт из неё токен для запросов к API.
 */

export const ROLES = ['trading', 'operations', 'risk', 'admin'] as const;
export type Role = (typeof ROLES)[number];

export const ROLE_LABELS: Readonly<Record<Role, string>> = {
  trading: 'Трейдинг',
  operations: 'Сопровождение',
  risk: 'Риск-менеджмент',
  admin: 'Администратор',
};

export interface Session {
  readonly username: string;
  readonly displayName: string;
  readonly roles: ReadonlySet<Role>;
  /** Действующий access token; при необходимости обновляется. */
  getAccessToken(): Promise<string>;
  logout(): void;
}

/** Контракт микрофронтенда: props, с которыми shell монтирует remote. */
export interface MicrofrontendProps {
  readonly session: Session;
  /** Путь, под которым shell показывает микрофронтенд (например, /admin). */
  readonly basePath: string;
}

/** Админ видит всё и может всё — так же, как на бэкенде. */
export function hasRole(session: Pick<Session, 'roles'>, role: Role): boolean {
  return session.roles.has('admin') || session.roles.has(role);
}

/** Оставляет только роли системы; служебные роли Keycloak отбрасываются. */
export function parseRoles(raw: unknown): ReadonlySet<Role> {
  if (!Array.isArray(raw)) {
    return new Set();
  }
  return new Set(raw.filter((name): name is Role => ROLES.includes(name as Role)));
}
