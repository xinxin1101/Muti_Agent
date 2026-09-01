const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";
// A cache miss may prepare a deterministic dependency environment before a Run exists.
const REQUEST_TIMEOUT_MS = 120_000;

type ApiErrorPayload = Readonly<{
  detail?: unknown;
}>;

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export class ApiClient {
  readonly baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  async getJson<T>(path: string, init?: RequestInit): Promise<T> {
    return this.requestJson<T>(path, {
      ...init,
      method: "GET",
    });
  }

  async postJson<TResponse, TBody>(
    path: string,
    body: TBody,
    init?: RequestInit,
  ): Promise<TResponse> {
    const headers = new Headers(init?.headers);
    headers.set("Content-Type", "application/json");
    return this.requestJson<TResponse>(path, {
      ...init,
      method: init?.method ?? "POST",
      headers,
      body: JSON.stringify(body),
    });
  }

  async postNoBody<TResponse>(
    path: string,
    init?: RequestInit,
  ): Promise<TResponse> {
    const headers = new Headers(init?.headers);
    headers.delete("Content-Type");
    return this.requestJson<TResponse>(path, {
      ...init,
      method: "POST",
      headers,
      body: undefined,
    });
  }

  private async requestJson<T>(path: string, init: RequestInit): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    const controller = new AbortController();
    const callerSignal = init.signal;
    const cancelFromCaller = () => controller.abort();
    callerSignal?.addEventListener("abort", cancelFromCaller, { once: true });
    const timeoutId = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    try {
      const response = await fetch(this.url(path), {
        ...init,
        headers,
        signal: controller.signal,
      });

      if (!response.ok) {
        let detail = `DevFlow API 请求失败，HTTP 状态码：${response.status}。`;
        try {
          const payload = (await response.json()) as ApiErrorPayload;
          if (typeof payload.detail === "string" && payload.detail.trim()) {
            detail = payload.detail;
          }
        } catch {
          // Keep the bounded status-based fallback when the response is not JSON.
        }
        throw new ApiError(response.status, detail);
      }

      if (response.status === 204) {
        return undefined as T;
      }
      return (await response.json()) as T;
    } catch (error) {
      if (controller.signal.aborted && !callerSignal?.aborted) {
        throw new ApiError(408, "DevFlow API 响应超时，请检查后端与 GitHub 网络连接后重试。");
      }
      throw error;
    } finally {
      window.clearTimeout(timeoutId);
      callerSignal?.removeEventListener("abort", cancelFromCaller);
    }
  }

  async deleteJson<TResponse, TBody>(path: string, body: TBody): Promise<TResponse> {
    return this.postJson<TResponse, TBody>(path, body, { method: "DELETE" });
  }

  private url(path: string): string {
    const normalizedPath = path.startsWith("/") ? path : `/${path}`;
    return `${this.baseUrl}${normalizedPath}`;
  }
}

export const apiClient = new ApiClient(
  import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL,
);
