const DEFAULT_API_BASE_URL = "http://localhost:8000";

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
    const headers = new Headers(init?.headers);
    headers.set("Accept", "application/json");

    const response = await fetch(this.url(path), {
      ...init,
      method: "GET",
      headers,
    });

    if (!response.ok) {
      throw new ApiError(
        response.status,
        `DevFlow API request failed with HTTP ${response.status}.`,
      );
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
