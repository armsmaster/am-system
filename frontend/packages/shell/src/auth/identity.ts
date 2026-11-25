/**
 * Сервис входа (решение 170): своя страница входа поверх Keycloak.
 *
 * Access token приходит в ответе и живёт в памяти страницы. Refresh token сервис
 * кладёт в cookie, которую скрипты не видят: по ней страница получает новый
 * access token при открытии и раз в несколько минут.
 */

const BASE = '/api/identity';

export interface SessionUser {
  readonly id: string;
  readonly username: string;
  readonly display_name: string;
  readonly roles: readonly string[];
}

export interface SessionData {
  readonly access_token: string;
  /** Секунд до истечения access token. */
  readonly expires_in: number;
  readonly user: SessionUser;
}

export interface InviteInfo {
  readonly username: string;
  readonly full_name: string;
  readonly expires_at: string;
}

/** Отказ сервиса входа: код для логики и сообщение для пользователя. */
export class AuthError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = 'AuthError';
  }
}

export interface IdentityApi {
  login(username: string, password: string, remember: boolean): Promise<SessionData>;
  /** Первый вход с временным паролем: задать свой и войти. */
  firstLogin(username: string, password: string, newPassword: string, remember: boolean): Promise<SessionData>;
  refresh(): Promise<SessionData>;
  logout(): Promise<void>;
  invite(token: string): Promise<InviteInfo>;
  acceptInvite(token: string, password: string, remember: boolean): Promise<SessionData>;
  changePassword(accessToken: string, current: string, next: string): Promise<void>;
}

type Fetch = (input: string, init: RequestInit) => Promise<Response>;

export function createIdentityApi(fetchImpl: Fetch): IdentityApi {
  async function send<T>(method: string, path: string, body?: unknown, token?: string): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    const init: RequestInit = { method, headers, credentials: 'same-origin' };
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    if (token !== undefined) {
      headers.Authorization = `Bearer ${token}`;
    }
    let response: Response;
    try {
      response = await fetchImpl(BASE + path, init);
    } catch {
      throw new AuthError(0, 'network', 'Не удалось связаться с сервером. Проверьте соединение и повторите.');
    }
    const payload = await readBody(response);
    if (!response.ok) {
      throw toError(response.status, payload);
    }
    return payload as T;
  }

  const invite = (token: string) => `/public/invites/${encodeURIComponent(token)}`;
  return {
    login: (username, password, remember) => send('POST', '/public/auth/login', { username, password, remember }),
    firstLogin: (username, password, newPassword, remember) =>
      send('POST', '/public/auth/first-login', { username, password, new_password: newPassword, remember }),
    refresh: () => send('POST', '/public/auth/refresh'),
    logout: () => send('POST', '/public/auth/logout'),
    invite: (token) => send('GET', invite(token)),
    acceptInvite: (token, password, remember) => send('POST', `${invite(token)}/accept`, { password, remember }),
    changePassword: (accessToken, current, next) =>
      send('POST', '/me/password', { current_password: current, new_password: next }, accessToken),
  };
}

export const identity = createIdentityApi((input, init) => fetch(input, init));

/** Сообщение для пользователя из любой ошибки входа. */
export function authMessage(error: unknown): string {
  return error instanceof AuthError ? error.message : 'Что-то пошло не так. Обновите страницу и повторите.';
}

function toError(status: number, payload: unknown): AuthError {
  const detail = (payload as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'object' && detail !== null && 'code' in detail && 'message' in detail) {
    return new AuthError(status, String(detail.code), String(detail.message));
  }
  if (status === 422) {
    return new AuthError(status, 'invalid', 'Проверьте введённые данные.');
  }
  return new AuthError(status, 'server', `Сервер ответил ошибкой ${status}. Повторите позже.`);
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (text === '') {
    return null;
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}
