import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClientError, apiFetch, formatNumber, formatRub } from "../src/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("apiFetch", () => {
  it("returns parsed JSON with same-origin credentials", async () => {
    const mock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", mock);

    const data = await apiFetch<{ ok: boolean }>("/api/example");
    expect(data.ok).toBe(true);
    const [, init] = mock.mock.calls[0];
    expect(init?.credentials).toBe("same-origin");
    expect(init?.cache).toBe("no-store");
  });

  it("sends JSON content-type when a body is present", async () => {
    const mock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", mock);
    await apiFetch("/api/example", { method: "POST", body: JSON.stringify({ a: 1 }) });
    const [, init] = mock.mock.calls[0];
    expect(new Headers(init?.headers).get("content-type")).toBe("application/json");
  });

  it("throws ApiClientError carrying status and code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: { message: "nope", code: "bad" } }), { status: 402 })),
    );
    try {
      await apiFetch("/api/example");
      expect.fail("apiFetch should have thrown");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiClientError);
      const typed = error as ApiClientError;
      expect(typed.status).toBe(402);
      expect(typed.code).toBe("bad");
      expect(typed.message).toBe("nope");
    }
  });

  it("resolves empty responses (204) without JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));
    await expect(apiFetch("/api/example", { method: "POST" })).resolves.toBeNull();
  });
});

describe("formatters", () => {
  it("formats kopecks as rubles", () => {
    expect(formatRub(50000)).toMatch(/500,00/);
    expect(formatRub(1)).toMatch(/0,01/);
  });

  it("formats large and дробные numbers", () => {
    expect(formatNumber("128000")).toMatch(/128\s?000/);
    expect(formatNumber(180)).toBe("180");
    expect(formatNumber("1.5")).toMatch(/1,5/);
  });
});
