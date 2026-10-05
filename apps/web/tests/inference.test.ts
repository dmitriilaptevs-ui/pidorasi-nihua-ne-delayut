import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getFreeModels, isExactZero, parseFreeModels } from "../src/lib/server/free-models";
import { generate, InferenceBudget, upstreamBody, validatePrompt } from "../src/lib/server/inference";
import { readBoundedJson, requireOrigin } from "../src/lib/server/http";
import { config } from "../src/lib/server/config";
import { readBoundedResponse } from "../src/lib/server/bounded-json";

const rawModel = {
  id: "example/model:free", name: "Example: Model (free)", context_length: 8192,
  pricing: { prompt: "0", completion: "0", request: "0.0" },
  architecture: { input_modalities: ["text"], output_modalities: ["text"] },
};
const input = { model: rawModel.id, prompt: "Привет", consent: true as const };

beforeEach(() => {
  vi.stubEnv("APP_ORIGIN", "http://localhost");
  vi.stubEnv("VK_APP_TYPE", "public");
  vi.stubEnv("OPENROUTER_FREE_MODELS", "");
  vi.stubEnv("OPENROUTER_API_KEY", "test-provider-key");
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

const catalog = () => parseFreeModels({ data: [rawModel] });

describe("exactly free catalog", () => {
  it.each(["0", "0.0", "0.000000", "0e-99", "0E+4"])("accepts exact zero %s", (value) => expect(isExactZero(value)).toBe(true));
  it.each(["0.000000000001", "1e-999", "-0.1", "", "free", null, 0, Infinity, "0x0"])("rejects ambiguous/nonzero price %s", (value) => expect(isExactZero(value)).toBe(false));
  it("requires a :free ID, zero costs including extra components, and text support", () => {
    const invalid = [
      { ...rawModel, id: "example/paid" },
      { ...rawModel, pricing: { prompt: "0", completion: "0", image: "0.01" } },
      { ...rawModel, pricing: { prompt: "0" } },
      { ...rawModel, pricing: { prompt: 0, completion: 0 } },
      { ...rawModel, architecture: { input_modalities: ["image"], output_modalities: ["image"] } },
      { ...rawModel, context_length: 100 },
    ];
    expect(parseFreeModels({ data: invalid })).toEqual([]);
    expect(catalog()).toEqual([{ id: rawModel.id, name: "Example: Model", provider: "example", contextLength: 8192 }]);
  });
  it("supports explicit approved-model allowlists", () => {
    expect(parseFreeModels({ data: [rawModel] }, ["different/model:free"])).toEqual([]);
    expect(parseFreeModels({ data: [rawModel] }, [rawModel.id])).toHaveLength(1);
  });
  it("fails closed on invalid catalog and never replaces it with fixture models", () => {
    expect(() => parseFreeModels({ models: [] })).toThrow();
    expect(parseFreeModels({ data: [] })).toEqual([]);
  });
  it("fetches public metadata without leaking the server inference key", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ data: [rawModel] }));
    expect(await getFreeModels({ fetcher })).toHaveLength(1);
    const [url, options] = fetcher.mock.calls[0];
    expect(url).toBe("https://openrouter.ai/api/v1/models");
    expect(options.headers).not.toHaveProperty("Authorization");
    expect(options.redirect).toBe("error");
  });
  it("surfaces catalog failure instead of stale or invented free models", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("Unavailable", { status: 503 }));
    await expect(getFreeModels({ fetcher })).rejects.toMatchObject({ code: "catalog_unavailable" });
  });
  it("coalesces simultaneous public catalog fetches", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ data: [rawModel] }));
    vi.stubGlobal("fetch", fetcher);
    const results = await Promise.all([getFreeModels(), getFreeModels()]);
    expect(results[0]).toEqual(results[1]);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("propagates cancellation to a fresh catalog request", async () => {
    const controller = new AbortController();
    const fetcher = vi.fn((_url, options) => new Promise<Response>((_resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    const pending = getFreeModels({ fresh: true, fetcher, signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ code: "catalog_unavailable" });
    expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
  });
});

describe("local request boundary", () => {
  it("cancels an oversized upstream body before fully buffering it", async () => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      pull(controller) { controller.enqueue(new Uint8Array(128)); },
      cancel() { cancelled = true; },
    });
    await expect(readBoundedResponse(new Response(body), 100)).rejects.toThrow();
    expect(cancelled).toBe(true);
  });
  it("requires https for public origins, rejects paths and invalid VK application types", () => {
    vi.stubEnv("APP_ORIGIN", "http://public.example");
    expect(() => config()).toThrow();
    vi.stubEnv("APP_ORIGIN", "https://public.example");
    expect(() => config()).not.toThrow();
    vi.stubEnv("APP_ORIGIN", "https://public.example/path");
    expect(() => config()).toThrow();
    vi.stubEnv("APP_ORIGIN", "http://localhost/path");
    expect(() => config()).toThrow();
    vi.stubEnv("APP_ORIGIN", "http://localhost");
    vi.stubEnv("VK_APP_TYPE", "guess");
    expect(() => config()).toThrow();
  });
  it("rejects foreign and missing Origin", () => {
    expect(() => requireOrigin(new Request("http://localhost/api/chat"))).toThrow();
    expect(() => requireOrigin(new Request("http://localhost/api/chat", { headers: { Origin: "https://attacker.example" } }))).toThrow();
    expect(() => requireOrigin(new Request("http://localhost/api/chat", { headers: { Origin: "http://localhost" } }))).not.toThrow();
  });
  it("bounds streamed JSON request bytes even without Content-Length", async () => {
    const req = new Request("http://localhost/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt: "я".repeat(1000) }) });
    await expect(readBoundedJson(req, 100)).rejects.toMatchObject({ status: 413 });
  });
  it("requires JSON objects", async () => {
    await expect(readBoundedJson(new Request("http://localhost/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: "[]" }))).rejects.toMatchObject({ status: 400 });
  });
  it("requires consent, bounded text and no injectable upstream fields", () => {
    expect(validatePrompt({ ...input, prompt: "  Привет  " })).toEqual(input);
    expect(() => validatePrompt({ ...input, consent: false })).toThrow();
    expect(() => validatePrompt({ ...input, tools: [] })).toThrow();
    expect(() => validatePrompt({ ...input, models: ["paid/model"] })).toThrow();
    expect(() => validatePrompt({ ...input, prompt: "x".repeat(2001) })).toThrow();
    expect(() => validatePrompt({ ...input, model: "paid/model" })).toThrow();
  });
});

describe("server-only inference", () => {
  it("uses a fixed endpoint, zero price ceiling and no paid fallback", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ choices: [{ message: { content: "Реальный формат ответа" } }], usage: { cost: 0, prompt_tokens: 4, completion_tokens: 6 } }));
    const result = await generate(input, catalog(), new AbortController().signal, fetcher);
    const [url, options] = fetcher.mock.calls[0];
    expect(url).toBe("https://openrouter.ai/api/v1/chat/completions");
    expect(options.headers.Authorization).toBe("Bearer test-provider-key");
    expect(JSON.parse(options.body)).toEqual(upstreamBody(input));
    expect(JSON.parse(options.body).provider).toMatchObject({ allow_fallbacks: false, max_price: { prompt: 0, completion: 0, request: 0, image: 0 } });
    expect(JSON.stringify(result)).not.toContain("test-provider-key");
    expect(result).toMatchObject({ text: "Реальный формат ответа", inputTokens: 4, outputTokens: 6 });
  });
  it("rejects non-catalog models without any upstream call", async () => {
    const fetcher = vi.fn();
    await expect(generate(input, [], new AbortController().signal, fetcher)).rejects.toMatchObject({ code: "model_unavailable" });
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("fails closed without a key", async () => {
    vi.stubEnv("OPENROUTER_API_KEY", "");
    const fetcher = vi.fn();
    await expect(generate(input, catalog(), new AbortController().signal, fetcher)).rejects.toMatchObject({ status: 503 });
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("never retries quota failures or exposes raw provider errors", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("private upstream debug data", { status: 429 }));
    await expect(generate(input, catalog(), new AbortController().signal, fetcher)).rejects.toMatchObject({ code: "provider_rate_limit" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("reports network uncertainty without automatic retry", async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error("secret network details"));
    await expect(generate(input, catalog(), new AbortController().signal, fetcher)).rejects.toMatchObject({ code: "provider_timeout" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("rejects empty answers instead of inventing text", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ choices: [{ message: { content: "" } }], usage: { cost: 0 } }));
    await expect(generate(input, catalog(), new AbortController().signal, fetcher)).rejects.toMatchObject({ code: "empty_response" });
  });
  it("rejects unknown cost instead of assuming a request was free", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ choices: [{ message: { content: "text" } }] }));
    await expect(generate(input, catalog(), new AbortController().signal, fetcher)).rejects.toMatchObject({ code: "unconfirmed_provider_cost" });
  });
  it("checks model identity, allowing the provider to omit the :free suffix", async () => {
    const good = vi.fn().mockResolvedValue(Response.json({ model: "example/model", choices: [{ message: { content: "text" } }], usage: { cost: 0 } }));
    await expect(generate(input, catalog(), new AbortController().signal, good)).resolves.toMatchObject({ model: input.model });
    const bad = vi.fn().mockResolvedValue(Response.json({ model: "other/paid-model", choices: [{ message: { content: "text" } }], usage: { cost: 0 } }));
    await expect(generate(input, catalog(), new AbortController().signal, bad)).rejects.toMatchObject({ code: "model_response_mismatch" });
  });
  it("alerts on an unexpected nonzero provider cost", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ choices: [{ message: { content: "text" } }], usage: { cost: 0.001 } }));
    await expect(generate(input, catalog(), new AbortController().signal, fetcher)).rejects.toMatchObject({ code: "unexpected_provider_cost" });
  });
});

describe("local free quota and concurrency", () => {
  it("halts new requests when cost validation has tripped the circuit", () => {
    const budget = new InferenceBudget();
    budget.blockForUnexpectedCost();
    expect(() => budget.acquire("alice")).toThrow();
  });
  it("bounds concurrency and safely releases an acquired slot once", () => {
    const budget = new InferenceBudget();
    const release = budget.acquire("alice");
    expect(() => budget.acquire("alice")).toThrow();
    const second = budget.acquire("bob");
    expect(() => budget.acquire("carol")).toThrow();
    release(); release();
    expect(() => budget.acquire("alice")).not.toThrow();
    second();
  });
  it("limits per-minute use even when requests fail", () => {
    const budget = new InferenceBudget();
    for (let i = 0; i < 3; i++) budget.acquire("alice", 100_000)();
    expect(() => budget.acquire("alice", 100_001)).toThrow();
    expect(() => budget.acquire("alice", 161_000)).not.toThrow();
  });
  it("enforces a shared daily cap across identities", () => {
    const budget = new InferenceBudget();
    for (let i = 0; i < 20; i++) budget.acquire(`user-${i}`, 100_000)();
    expect(() => budget.acquire("new-user", 161_000)).toThrow();
    expect(() => budget.acquire("new-user", 86_500_000)).not.toThrow();
  });
});
