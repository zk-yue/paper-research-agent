/** Shared HTTP transport; preserve server validation errors for every caller. */
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

type JsonOptions = Omit<RequestInit, 'body'> & { body?: unknown };

function errorDetail(payload: unknown): string | undefined {
  if (!payload || typeof payload !== 'object' || !('detail' in payload)) return;
  const detail = payload.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((item: unknown) => {
      if (item && typeof item === 'object' && 'msg' in item) return String(item.msg);
      return '';
    }).filter(Boolean).join('; ') || undefined;
  }
}

export async function request<T>(path: string, options: JsonOptions = {}): Promise<T> {
  const { body, ...init } = options;
  const headers = new Headers(init.headers);
  if (body !== undefined) headers.set('Content-Type', 'application/json');
  const response = await fetch(`/api${path}`, {
    ...init,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let payload: unknown;
  try {
    payload = text ? JSON.parse(text) : undefined;
  } catch {
    if (response.ok) throw new ApiError('服务器返回了无效的 JSON', response.status);
  }
  if (!response.ok) {
    throw new ApiError(errorDetail(payload) ?? `请求失败（HTTP ${response.status}）`, response.status);
  }
  return payload as T;
}
