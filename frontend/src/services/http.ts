export const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...options, credentials: 'include',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json', ...options.headers }
  });
  const data = await response.json().catch(() => null);
  if (!response.ok || data === null) {
    throw new ApiError((typeof data?.detail === 'string' && data.detail) ||
      (typeof data?.message === 'string' && data.message) || `Сервис недоступен (${response.status}). Повторите позже.`, response.status);
  }
  return data as T;
}
