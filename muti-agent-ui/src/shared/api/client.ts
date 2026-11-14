import type { ApiErrorBody } from './types';

const RAW_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').trim();
export const API_BASE = RAW_BASE.replace(/\/+$/, '');

export const USE_MOCK = (import.meta.env.VITE_USE_MOCK ?? 'true') !== 'false';

export function apiUrl(path: string): string {
  if (!API_BASE) return path;
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details?: Record<string, unknown>;
  readonly retryable: boolean;
  readonly requestId?: string;

  constructor(status: number, body: Partial<ApiErrorBody>) {
    super(body.message ?? `请求失败（HTTP ${status}）`);
    this.name = 'ApiError';
    this.status = status;
    this.code = body.code ?? 'E_INTERNAL';
    this.details = body.details;
    this.retryable = body.retryable ?? false;
    this.requestId = body.request_id;
  }
}

export function authToken(): string {
  return sessionStorage.getItem('ma.token') ?? '';
}

export function setAuthToken(token: string): void {
  if (token) sessionStorage.setItem('ma.token', token);
  else sessionStorage.removeItem('ma.token');
}

interface RequestOptions {
  signal?: AbortSignal;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  options: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  const token = authToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: options.signal,
    });
  } catch {
    throw new ApiError(0, {
      code: 'E_LLM_CONNECTION',
      message: '连不上后端服务。确认 BFF 已启动，或切回 Mock 模式。',
      retryable: true,
    });
  }

  const text = await response.text();
  const payload = text ? safeJson(text) : null;

  if (!response.ok) {
    const body = (payload as { error?: ApiErrorBody } | null)?.error;
    throw new ApiError(response.status, body ?? {
      code: response.status === 401 ? 'E_UNAUTHORIZED' : 'E_INTERNAL',
      message: `请求失败（HTTP ${response.status}）`,
      retryable: response.status >= 500,
    });
  }

  return payload as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export const http = {
  get: <T>(path: string, options?: RequestOptions) => request<T>('GET', path, undefined, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('POST', path, body ?? {}, options),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('PATCH', path, body ?? {}, options),
  delete: <T>(path: string, options?: RequestOptions) => request<T>('DELETE', path, undefined, options),
};
