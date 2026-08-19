import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient, ApiError } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ApiClient", () => {
  it("posts JSON through the typed backend boundary", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = new ApiClient("http://localhost:8000/");
    const result = await client.postJson<{ ok: boolean }, { value: number }>(
      "/api/v1/example",
      { value: 1 },
    );

    expect(result.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/v1/example",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ value: 1 }),
      }),
    );
  });

  it("surfaces bounded API error detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "unknown run" }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const client = new ApiClient("http://localhost:8000");
    await expect(client.getJson("/api/v1/runs/missing")).rejects.toEqual(
      new ApiError(404, "unknown run"),
    );
  });
});
