import type { Session } from './session';

/** Ответ API с кодом ошибки. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly body: unknown,
  ) {
    super(`API request failed with status ${status}`);
    this.name = 'ApiError';
  }
}

export interface ApiClient {
  /** Запросы к API на том же адресе, что и приложение (например, /api/portfolio/me). */
  get<T>(path: string): Promise<T>;
  post<T>(path: string, body: unknown): Promise<T>;
  put<T>(path: string, body?: unknown): Promise<T>;
  patch<T>(path: string, body: unknown): Promise<T>;
  delete<T>(path: string): Promise<T>;
  /** Файл, например детализация расчёта в Excel. */
  blob(path: string): Promise<Blob>;
}

type Fetch = (input: string, init: RequestInit) => Promise<Response>;

export function createApiClient(
  session: Pick<Session, 'getAccessToken'>,
  fetchImpl: Fetch = (input, init) => fetch(input, init),
): ApiClient {
  async function send(method: string, path: string, accept: string, body?: unknown): Promise<Response> {
    const token = await session.getAccessToken();
    const headers: Record<string, string> = { Accept: accept, Authorization: `Bearer ${token}` };
    const init: RequestInit = { method, headers };
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    return fetchImpl(path, init);
  }

  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const response = await send(method, path, 'application/json', body);
    const payload: unknown = await readBody(response);
    if (!response.ok) {
      throw new ApiError(response.status, payload);
    }
    // Типы ответов позже будут генерироваться из OpenAPI сервисов.
    return payload as T;
  }

  return {
    get: (path) => request('GET', path),
    post: (path, body) => request('POST', path, body),
    put: (path, body) => request('PUT', path, body),
    patch: (path, body) => request('PATCH', path, body),
    delete: (path) => request('DELETE', path),
    blob: async (path) => {
      const response = await send('GET', path, '*/*');
      if (!response.ok) {
        throw new ApiError(response.status, await readBody(response));
      }
      return response.blob();
    },
  };
}

/**
 * Понятное пользователю сообщение об ошибке запроса.
 * Сервисы отвечают на нарушение правил так: {"detail": {"code", "message"}}.
 */
export function errorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return 'Не удалось связаться с сервером. Проверьте соединение и повторите.';
  }
  const detail = (error.body as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'object' && detail !== null && 'message' in detail) {
    return String(detail.message);
  }
  switch (error.status) {
    case 403:
      return 'Недостаточно прав для этого действия.';
    case 404:
      return 'Объект не найден — возможно, его уже изменили. Обновите страницу.';
    case 422:
      return 'Проверьте введённые данные.';
    default:
      return `Сервер ответил ошибкой ${error.status}. Повторите позже.`;
  }
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
