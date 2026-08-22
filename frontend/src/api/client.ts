const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";

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
      method: "POST",
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

    const response = await fetch(this.url(path), {
      ...init,
      headers,
    });

    if (!response.ok) {
      let detail = `DevFlow API request failed with HTTP ${response.status}.`;
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

    return (await response.json()) as T;
  }

  private url(path: string): string {
    const normalizedPath = path.startsWith("/") ? path : `/${path}`;
    return `${this.baseUrl}${normalizedPath}`;
  }
}

export const apiClient = new ApiClient(
  import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL,
);
