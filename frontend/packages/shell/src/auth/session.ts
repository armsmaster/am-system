import { parseRoles, type Session } from '@am/platform';

import { AuthError, identity, type IdentityApi, type SessionData } from './identity';

/** Токен обновляем заранее: за 30 секунд до истечения. */
const MARGIN_MS = 30_000;

export interface SessionOptions {
  readonly api?: IdentityApi;
  readonly now?: () => number;
  /** Сессия закончилась — перезагрузка покажет страницу входа; после выхода — на главную. */
  readonly location?: Pick<Location, 'reload' | 'assign'>;
}

/**
 * Сессия для shell и микрофронтендов: access token в памяти, обновление по cookie
 * через сервис входа. Одновременные запросы ждут одно обновление.
 */
export function createSession(
  initial: SessionData,
  { api = identity, now = Date.now, location = window.location }: SessionOptions = {},
): Session {
  let token = initial.access_token;
  let expiresAt = now() + initial.expires_in * 1000;
  let pending: Promise<string> | null = null;

  async function renew(): Promise<string> {
    try {
      const next = await api.refresh();
      token = next.access_token;
      expiresAt = now() + next.expires_in * 1000;
      return token;
    } catch (error) {
      if (error instanceof AuthError && error.status === 401) {
        location.reload();
      }
      throw error;
    } finally {
      pending = null;
    }
  }

  return {
    username: initial.user.username,
    displayName: initial.user.display_name,
    roles: parseRoles(initial.user.roles),

    getAccessToken() {
      if (now() < expiresAt - MARGIN_MS) {
        return Promise.resolve(token);
      }
      pending ??= renew();
      return pending;
    },

    logout() {
      // Сервис недоступен — cookie останется, но страница всё равно уйдёт на вход.
      void api
        .logout()
        .catch(() => undefined)
        .finally(() => location.assign('/'));
    },
  };
}
