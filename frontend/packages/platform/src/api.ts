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
  /** GET-запрос к API на том же адресе, что и приложение (например, /api/portfolio/me). */
  get<T>(path: string): Promise<T>;
}

type Fetch = (input: string, init: RequestInit) => Promise<Response>;

export function createApiClient(
  session: Pick<Session, 'getAccessToken'>,
  fetchImpl: Fetch = (input, init) => fetch(input, init),
): ApiClient {
  return {
    async get<T>(path: string): Promise<T> {
      const token = await session.getAccessToken();
      const response = await fetchImpl(path, {
        method: 'GET',
        headers: { Accept: 'application/json', Authorization: `Bearer ${token}` },
      });
      const body: unknown = await readBody(response);
      if (!response.ok) {
        throw new ApiError(response.status, body);
      }
      // Типы ответов позже будут генерироваться из OpenAPI сервисов.
      return body as T;
    },
  };
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
