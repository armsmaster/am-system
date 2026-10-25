import type { Session } from '@am/platform';
import Keycloak from 'keycloak-js';

import { sessionFromKeycloak } from './session';

export interface AuthConfig {
  readonly url: string;
  readonly realm: string;
  readonly clientId: string;
}

/**
 * Keycloak публикуется шлюзом на том же адресе, что и приложение (/auth).
 * Для локальной разработки адрес можно задать через PUBLIC_KEYCLOAK_URL.
 */
export function authConfig(
  keycloakUrl: string | undefined = import.meta.env.PUBLIC_KEYCLOAK_URL,
  origin: string = window.location.origin,
): AuthConfig {
  return { url: keycloakUrl || `${origin}/auth`, realm: 'am', clientId: 'am-web' };
}

/** Вход: без сессии Keycloak перенаправит на страницу логина и вернёт обратно. */
export async function startSession(config: AuthConfig = authConfig()): Promise<Session> {
  const keycloak = new Keycloak(config);
  await keycloak.init({ onLoad: 'login-required', pkceMethod: 'S256', checkLoginIframe: false });
  return sessionFromKeycloak(keycloak);
}
